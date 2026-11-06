"""Market data. Yahoo Finance (free, unofficial, can lag) by default; Interactive Brokers' own bars and quotes
when connected. Bars are dicts: {"t": datetime ET, "o","h","l","c","v"}.
"""
import csv
import io
import json
import time
from datetime import datetime

import requests

import config
from market import ET

_session = requests.Session()
_session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
SP500_CSV = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"


def universe() -> dict:
    """{symbol: {"name", "cik"}} for the S&P 500, cached for a day."""
    f = config.CACHE_DIR / "sp500.json"
    if f.exists() and time.time() - f.stat().st_mtime < 86400:
        return json.loads(f.read_text())
    r = _session.get(SP500_CSV, timeout=30)
    r.raise_for_status()
    out = {row["Symbol"].strip(): {"name": row["Security"].strip(), "cik": row.get("CIK", "").strip()}
           for row in csv.DictReader(io.StringIO(r.text))}
    f.write_text(json.dumps(out))
    return out


def ysym(symbol: str) -> str:
    return symbol if symbol.startswith("^") else symbol.replace(".", "-")   # BRK.B -> BRK-B


def yahoo_bars(symbol: str, interval: str = "5m", rng: str = "5d") -> list[dict]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(ysym(symbol))}"
    for attempt in range(3):
        r = _session.get(url, params={"interval": interval, "range": rng}, timeout=20)
        if r.status_code == 429:
            time.sleep(2 + attempt * 3)
            continue
        r.raise_for_status()
        break
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(res.get("timestamp") or []):
        o, h, l, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c):
            continue
        out.append({"t": datetime.fromtimestamp(t, ET), "o": o, "h": h, "l": l, "c": c, "v": v or 0})
    return out


def yahoo_last_price(symbol: str) -> float:
    r = _session.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym(symbol)}",
                     params={"interval": "1m", "range": "1d"}, timeout=15)
    r.raise_for_status()
    return float(r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"])


class Data:
    """Interactive Brokers when connected, otherwise Yahoo."""

    def __init__(self, ib=None):
        self.ib = ib

    @property
    def source(self):
        return "Interactive Brokers" if self.ib else "Yahoo (may lag)"

    def intraday(self, symbol: str, days: int = 1) -> list[dict]:
        if self.ib:
            return self.ib.candles(symbol, days)
        return yahoo_bars(symbol, "5m", f"{max(days, 1)}d")

    def quote(self, symbol: str) -> tuple[float, float, float]:
        """(bid, ask, last). Yahoo has no order book, so an assumed spread is applied."""
        if self.ib:
            q = self.ib.quote(symbol)
            if q:
                return q
        last = yahoo_last_price(symbol)
        half = last * config.ASSUMED_SPREAD_PCT / 100 / 2
        return last - half, last + half, last
