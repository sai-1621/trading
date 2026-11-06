"""Market data. Angel One's own candles and prices whenever the bot is logged in to Angel One (paper or live);
Yahoo Finance (free, unofficial, can lag) when it is not, or as the fallback for a request Angel One fails.
Bars are dicts: {"t": datetime IST, "o","h","l","c","v"}.
"""
import io
import csv
import json
import logging
import time
from datetime import datetime, timedelta

import requests

import config
from market import IST

log = logging.getLogger("bot")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_session = requests.Session()
_session.headers.update(UA)

INDEX_CSV = {
    "NIFTY50": "ind_nifty50list.csv", "NIFTY100": "ind_nifty100list.csv",
    "NIFTY200": "ind_nifty200list.csv", "NIFTY500": "ind_nifty500list.csv",
}


def universe(name: str = None) -> dict[str, str]:
    """{NSE symbol: company name} for the index, cached for a day."""
    name = (name or config.UNIVERSE).upper()
    f = config.CACHE_DIR / f"{name}.json"
    if f.exists() and time.time() - f.stat().st_mtime < 86400:
        return json.loads(f.read_text())
    url = f"https://archives.nseindia.com/content/indices/{INDEX_CSV[name]}"
    try:
        r = _session.get(url, timeout=20)
        r.raise_for_status()
    except requests.RequestException:
        r = _session.get(f"https://niftyindices.com/IndexConstituent/{INDEX_CSV[name]}", timeout=20)
        r.raise_for_status()
    syms = {row["Symbol"].strip(): row["Company Name"].strip()
            for row in csv.DictReader(io.StringIO(r.text)) if row.get("Series", "EQ").strip() == "EQ"}
    f.write_text(json.dumps(syms))
    return syms


def yahoo_bars(symbol: str, interval: str = "5m", rng: str = "5d") -> list[dict]:
    """symbol: NSE symbol (RELIANCE) or a Yahoo symbol starting with ^ (^NSEI)."""
    ysym = symbol if symbol.startswith("^") else f"{symbol}.NS"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(ysym)}"
    for attempt in range(3):
        r = _session.get(url, params={"interval": interval, "range": rng}, timeout=20)
        if r.status_code == 429:
            time.sleep(2 + attempt * 3)
            continue
        r.raise_for_status()
        break
    res = r.json()["chart"]["result"][0]
    ts = res.get("timestamp") or []
    q = res["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(ts):
        o, h, l, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c):
            continue
        out.append({"t": datetime.fromtimestamp(t, IST), "o": o, "h": h, "l": l, "c": c, "v": v or 0})
    return out


def yahoo_last_price(symbol: str) -> float:
    r = _session.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.NS",
                     params={"interval": "1m", "range": "1d"}, timeout=15)
    r.raise_for_status()
    return float(r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"])


# ---------------------------------------------------------------- broker first, Yahoo as the fallback
# Set by Data(angel) when the bot is logged in to Angel One. Every bar request (live bars, the scanner's daily
# closes, the Time Machine's 60 days of 5-minute history, the trend filter) then goes to Angel One first and
# falls back to Yahoo only if Angel One fails for that request.
_ANGEL = None
ANGEL_INTERVAL = {"1m": "ONE_MINUTE", "5m": "FIVE_MINUTE", "15m": "FIFTEEN_MINUTE", "1h": "ONE_HOUR", "1d": "ONE_DAY"}
STATS = {"angel": 0, "yahoo": 0, "fallback": 0, "last_error": ""}


def set_broker(angel) -> None:
    global _ANGEL
    _ANGEL = angel


def rng_days(rng: str) -> int:
    """Yahoo range ("5d", "60d", "3mo", "6mo", "1y", "2y") -> calendar days."""
    if rng.endswith("mo"):
        return int(rng[:-2]) * 31
    return int(rng[:-1]) * {"d": 1, "y": 366}[rng[-1]]


def bars(symbol: str, interval: str = "5m", rng: str = "5d", fast: bool = False) -> list[dict]:
    """Bars for an NSE symbol (RELIANCE) or ^NSEI: Angel One when logged in, otherwise (or on error) Yahoo.
    fast=True (the live 5-minute loop): one try at Angel One, then Yahoo at once, so a rate-limit refusal never
    makes the bot late for the bar; the scanner, the Time Machine and backtests retry patiently instead."""
    if _ANGEL is not None and interval in ANGEL_INTERVAL:
        try:
            out = _ANGEL.candles(symbol, ANGEL_INTERVAL[interval], rng_days(rng), tries=1 if fast else 5)
            STATS["angel"] += 1
            return out
        except Exception as e:  # noqa: BLE001 - any broker problem: keep trading on Yahoo data
            STATS["fallback"] += 1
            STATS["last_error"] = f"{symbol} {interval}: {e}"[:200]
            log.warning("Angel One data failed for %s %s (%s): using Yahoo for this request", symbol, interval, e)
    STATS["yahoo"] += 1
    return yahoo_bars(symbol, interval, rng)


def last_price(symbol: str) -> float:
    if _ANGEL is not None:
        try:
            p = _ANGEL.ltp(symbol)
            STATS["angel"] += 1
            return p
        except Exception as e:  # noqa: BLE001
            STATS["fallback"] += 1
            STATS["last_error"] = f"{symbol} ltp: {e}"[:200]
            log.warning("Angel One price failed for %s (%s): using Yahoo", symbol, e)
    STATS["yahoo"] += 1
    return yahoo_last_price(symbol)


class Data:
    """Chooses the source: Angel One when a logged-in client is given, otherwise Yahoo."""

    def __init__(self, angel=None):
        self.angel = angel
        set_broker(angel)

    @property
    def source(self):
        if not self.angel:
            return "Yahoo (may lag)"
        if config.LIVE_BARS_SOURCE == "angel":
            return "Angel One (Yahoo as fallback)"
        return "Angel One prices, history and scanner; live 5-minute bars from Yahoo"

    def intraday(self, symbol: str, days: int = 5) -> list[dict]:
        rng = f"{days}d" if days <= 60 else "60d"
        if config.LIVE_BARS_SOURCE != "angel":          # see config.LIVE_BARS_SOURCE
            STATS["yahoo"] += 1
            return yahoo_bars(symbol, "5m", rng)
        return bars(symbol, "5m", rng, fast=True)

    def daily(self, symbol: str, rng: str = "1y") -> list[dict]:
        return bars(symbol, "1d", rng)

    def price(self, symbol: str) -> float:
        return last_price(symbol)

    def quote(self, symbol: str) -> tuple[float, float, float]:
        """(bid, ask, last). Yahoo has no order book, so an assumed spread is applied around the last price."""
        last = self.price(symbol)
        half = last * config.ASSUMED_SPREAD_PCT / 100 / 2
        return last - half, last + half, last
