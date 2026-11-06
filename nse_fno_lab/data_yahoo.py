"""Free data from Yahoo Finance for the backtests (NIFTY ^NSEI, SENSEX ^BSESN, India VIX ^INDIAVIX, NSE stocks).

Limits: 5-minute bars only for the last 60 days, hourly for about 2 years, daily for decades.
Index bars have no volume (VWAP falls back to equal weights). Each download is cached in cache/ for 12 hours
and merged with older cached bars, so running again over the weeks builds a longer 5-minute history.
"""
import time
from pathlib import Path

import pandas as pd
import requests

import config as C
from nse_calendar import IST

_s = requests.Session()
_s.headers["User-Agent"] = "Mozilla/5.0"
CACHE = C.CACHE_DIR / "yahoo"
CACHE.mkdir(exist_ok=True)


def _ysym(symbol: str) -> str:
    return symbol if symbol.startswith("^") else f"{symbol}.NS"


def _fetch(symbol: str, interval: str, rng: str) -> pd.DataFrame:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(_ysym(symbol))}"
    for attempt in range(4):
        r = _s.get(url, params={"interval": interval, "range": rng}, timeout=30)
        if r.status_code == 429:
            time.sleep(3 + 5 * attempt)
            continue
        r.raise_for_status()
        break
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({"open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"],
                       "volume": q.get("volume")}, index=pd.to_datetime(res.get("timestamp", []), unit="s", utc=True))
    df = df.dropna(subset=["open", "high", "low", "close"])
    df["volume"] = df["volume"].fillna(0).astype(float)
    df.index = df.index.tz_convert(IST)
    if interval == "1d":
        df.index = pd.to_datetime(df.index.date)
    return df


def bars(symbol: str, interval: str = "5m", rng: str | None = None, max_age_h: float = 12) -> pd.DataFrame:
    rng = rng or {"5m": "60d", "15m": "60d", "1h": "730d", "1d": "10y"}[interval]
    f: Path = CACHE / f"{symbol.replace('^', '_')}_{interval}.pkl"
    old = pd.read_pickle(f) if f.exists() else None
    if old is not None and time.time() - f.stat().st_mtime < max_age_h * 3600:
        return old
    new = _fetch(symbol, interval, rng)
    if old is not None:
        new = pd.concat([old, new])
        new = new[~new.index.duplicated(keep="last")].sort_index()
    if interval in ("5m", "15m", "1h"):   # keep regular session bars only
        m = new.index.hour * 60 + new.index.minute
        new = new[(m >= 9 * 60 + 15) & (m < 15 * 60 + 30)]
    new.to_pickle(f)
    time.sleep(0.4)
    return new


def vix_daily() -> pd.Series:
    d = bars("^INDIAVIX", "1d")
    s = d["close"]
    s.index = pd.to_datetime(s.index).date
    return s


# Liquid NSE F&O stocks (2026). The backtest ranks them by traded value and keeps the top 50.
FNO_STOCKS = """RELIANCE HDFCBANK ICICIBANK INFY TCS SBIN BHARTIARTL AXISBANK KOTAKBANK LT ITC HINDUNILVR BAJFINANCE
MARUTI M&M SUNPHARMA TATAMOTORS TATASTEEL NTPC POWERGRID ONGC COALINDIA ADANIENT ADANIPORTS HCLTECH WIPRO TECHM
TITAN ULTRACEMCO ASIANPAINT NESTLEIND JSWSTEEL HINDALCO GRASIM BAJAJFINSV BAJAJ-AUTO EICHERMOT HEROMOTOCO
DRREDDY CIPLA DIVISLAB APOLLOHOSP INDUSINDBK BEL HAL BPCL IOC SHRIRAMFIN TRENT DLF VEDL PFC RECLTD
BANKBARODA PNB CANBK TVSMOTOR LUPIN AUROPHARMA GODREJCP DABUR TATAPOWER JINDALSTEL SAIL NMDC GAIL
ZYDUSLIFE CHOLAFIN LTIM PERSISTENT COFORGE MPHASIS POLYCAB DIXON INDIGO IRCTC TATACONSUM BRITANNIA""".split()
