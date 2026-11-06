"""NSE/BSE clock: IST, trading days, weekly/monthly expiries (moved to the previous trading day on a holiday)."""
import os
from datetime import date, datetime, time, timedelta, timezone

import config as C

IST = timezone(timedelta(hours=5, minutes=30))
TRADING_DAYS = 252
SESSION_MIN = 375

# NSE trading holidays 2026 (weekdays). Add more years with NSE_HOLIDAYS=YYYY-MM-DD,... in .env.
HOLIDAYS = {
    "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03", "2026-04-14", "2026-05-01",
    "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02", "2026-10-20", "2026-11-10", "2026-11-24",
    "2026-12-25",
}
HOLIDAYS |= {d.strip() for d in os.getenv("NSE_HOLIDAYS", "").split(",") if d.strip()}

# Before 1 Sep 2025 NIFTY weekly options expired on Thursday and SENSEX on Tuesday (used by backtests only).
EXPIRY_SWITCH = date(2025, 9, 1)
OLD_WEEKDAY = {"NIFTY": 3, "SENSEX": 1}


def now() -> datetime:
    return datetime.now(IST)


def hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in HOLIDAYS


def prev_trading_day(d: date) -> date:
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def weekly_weekday(symbol: str, d: date) -> int:
    if symbol in OLD_WEEKDAY and d < EXPIRY_SWITCH:
        return OLD_WEEKDAY[symbol]
    return C.WEEKLY_EXPIRY_WEEKDAY[symbol]


def last_weekday_of_month(y: int, m: int, weekday: int) -> date:
    d = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def expiries(symbol: str, start: date, count: int = 4) -> list[date]:
    """The next `count` expiry dates on or after `start`, holiday-adjusted."""
    out, d = [], start
    if symbol in C.MONTHLY_ONLY:
        y, m = d.year, d.month
        while len(out) < count:
            e = prev_trading_day(last_weekday_of_month(y, m, 1))
            if e >= start:
                out.append(e)
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return out
    while len(out) < count:
        wd = weekly_weekday(symbol, d)
        nominal = d + timedelta(days=(wd - d.weekday()) % 7)
        e = prev_trading_day(nominal)
        if e >= start and (not out or e > out[-1]):
            out.append(e)
        d = nominal + timedelta(days=1)
    return out


def is_expiry_day(symbol: str, d: date) -> bool:
    return expiries(symbol, d, 1)[0] == d


def nearest_expiry(symbol: str, d: date, min_dte_days: int = 0) -> date:
    """First expiry at least `min_dte_days` calendar days away (0 = today's expiry is allowed)."""
    for e in expiries(symbol, d, 6):
        if (e - d).days >= min_dte_days:
            return e
    raise ValueError("no expiry found")


def years_to_expiry(ts: datetime, expiry: date) -> float:
    """Time to 15:30 IST on expiry day in TRADING years: remaining session minutes today + 375 minutes for each
    trading day after today up to expiry, divided by 375 x 252. Index variance happens almost only while the
    market is open, so with India VIX as the volatility this prices same-day options correctly (calendar time
    would count nights and weekends and make a same-day straddle about 2x too cheap). Floor: 1 minute."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=IST)
    ts = ts.astimezone(IST)
    d0 = ts.date()
    m = ts.hour * 60 + ts.minute + ts.second / 60
    today = 0.0
    if d0 <= expiry and is_trading_day(d0):
        today = min(max(15 * 60 + 30 - m, 0.0), 375.0)
    d, full = d0 + timedelta(days=1), 0
    while d <= expiry:
        full += is_trading_day(d)
        d += timedelta(days=1)
    return max(today + 375 * full, 1.0) / (375 * TRADING_DAYS)
