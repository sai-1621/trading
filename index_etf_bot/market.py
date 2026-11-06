"""NSE clock: India time, the 9:15-15:30 session, 5-minute bars and trading holidays."""
import os
from datetime import datetime, date, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
OPEN_MIN = 9 * 60 + 15      # 09:15
CLOSE_MIN = 15 * 60 + 30    # 15:30
BAR_MIN = 5
BARS_PER_DAY = (CLOSE_MIN - OPEN_MIN) // BAR_MIN   # 75

# NSE equity holidays 2026 (weekdays). Check NSE's circular each year; add more with NSE_HOLIDAYS in .env.
HOLIDAYS = {
    "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03", "2026-04-14", "2026-05-01",
    "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02", "2026-10-20", "2026-11-10", "2026-11-24",
    "2026-12-25",
}
HOLIDAYS |= {d.strip() for d in os.getenv("NSE_HOLIDAYS", "").split(",") if d.strip()}
KNOWN_YEARS = {int(d[:4]) for d in HOLIDAYS}


def now() -> datetime:
    return datetime.now(IST)


def hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def minutes(t: datetime) -> int:
    return t.hour * 60 + t.minute


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in HOLIDAYS


def holiday_list_missing(d: date) -> bool:
    return d.year not in KNOWN_YEARS


def is_open(t: datetime | None = None) -> bool:
    t = t or now()
    return is_trading_day(t.date()) and OPEN_MIN <= minutes(t) < CLOSE_MIN


def session_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST)


def bar_index(ts: datetime) -> int:
    """0 for the 9:15 bar, 74 for the 15:25 bar."""
    return (minutes(ts) - OPEN_MIN) // BAR_MIN


def last_closed_bar_start(t: datetime | None = None) -> datetime | None:
    """Start time of the newest completed 5-minute bar today, or None before 9:20."""
    t = t or now()
    m = min(minutes(t), CLOSE_MIN)
    if m < OPEN_MIN + BAR_MIN:
        return None
    idx = (m - OPEN_MIN) // BAR_MIN - 1
    return session_start(t.date()) + timedelta(minutes=idx * BAR_MIN)


def next_open(t: datetime | None = None) -> datetime:
    t = t or now()
    d = t.date()
    if minutes(t) >= OPEN_MIN or not is_trading_day(d):
        d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return session_start(d)
