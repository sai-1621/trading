"""US market clock: New York time (daylight saving handled), the 9:30-16:00 session, 5-minute bars,
NYSE holidays and 1:00 p.m. early closes (2026-2027, from the day-trader skill pack).
On an early-close day the flat-by time and the entry cutoff move up by 3 hours, as in the original pack."""
import os
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import config

ET = ZoneInfo("America/New_York")
OPEN_MIN = 9 * 60 + 30
CLOSE_MIN = 16 * 60
EARLY_CLOSE_MIN = 13 * 60
BAR_MIN = 5
BARS_PER_DAY = (CLOSE_MIN - OPEN_MIN) // BAR_MIN   # 78

HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
    "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05",
    "2027-09-06", "2027-11-25", "2027-12-24",
}
HOLIDAYS |= {d.strip() for d in os.getenv("NYSE_HOLIDAYS", "").split(",") if d.strip()}
EARLY_CLOSES = {"2026-11-27", "2026-12-24", "2027-11-26"}
KNOWN_YEARS = {2026, 2027}


def now() -> datetime:
    return datetime.now(ET)


def hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def minutes(t: datetime) -> int:
    return t.hour * 60 + t.minute


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in HOLIDAYS


def is_early_close(d: date) -> bool:
    return d.isoformat() in EARLY_CLOSES


def close_min(d: date) -> int:
    return EARLY_CLOSE_MIN if is_early_close(d) else CLOSE_MIN


def _shift(d: date) -> int:
    return CLOSE_MIN - close_min(d)   # 180 minutes on an early-close day


def flat_by_min(d: date) -> int:
    return market_hhmm(config.FLAT_BY) - _shift(d)


def entries_to_min(d: date) -> int:
    return market_hhmm(config.NO_ENTRIES_AFTER) - _shift(d)


def market_hhmm(s):
    return hhmm(s)


def holiday_list_missing(d: date) -> bool:
    return d.year not in KNOWN_YEARS


def is_open(t: datetime | None = None) -> bool:
    t = t or now()
    return is_trading_day(t.date()) and OPEN_MIN <= minutes(t) < close_min(t.date())


def session_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET)


def bar_index(ts: datetime) -> int:
    return (minutes(ts) - OPEN_MIN) // BAR_MIN


def last_closed_bar_start(t: datetime | None = None) -> datetime | None:
    t = t or now()
    m = min(minutes(t), close_min(t.date()))
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
