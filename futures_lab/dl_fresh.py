"""Download the fresh test data (2018-01-01 .. 2021-10-31): Binance SPOT 1h candles and perpetual funding rates
for the 19 coins, into futures_lab/data_fresh/. Public endpoints, no keys. Throttled."""
import json
import time
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
OUT = HERE / "data_fresh"
OUT.mkdir(exist_ok=True)
COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT", "LTC", "BCH", "TRX", "ATOM",
         "ETC", "NEAR", "FIL", "UNI", "AAVE"]
START = int(pd.Timestamp("2017-10-01", tz="UTC").timestamp() * 1000)   # 3 months of warm-up before 2018
END = int(pd.Timestamp("2021-11-01", tz="UTC").timestamp() * 1000)
S = requests.Session()


def get(url, params):
    for i in range(6):
        r = S.get(url, params=params, timeout=30)
        if r.status_code in (418, 429):
            time.sleep(30 * (i + 1))
            continue
        r.raise_for_status()
        time.sleep(0.25)
        return r.json()
    raise RuntimeError(f"rate limited: {url}")


def klines(sym):
    rows, t = [], START
    while t < END:
        k = get("https://api.binance.com/api/v3/klines", {"symbol": sym, "interval": "1h", "startTime": t,
                                                          "endTime": END - 1, "limit": 1000})
        if not k:
            break
        rows += k
        t = k[-1][0] + 3600_000
    df = pd.DataFrame([r[:6] for r in rows], columns=["date", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"], unit="ms", utc=True)
    return df.astype({c: float for c in ("open", "high", "low", "close", "volume")})


def funding(sym):
    rows, t = [], START
    while t < END:
        f = get("https://fapi.binance.com/fapi/v1/fundingRate", {"symbol": sym, "startTime": t, "endTime": END - 1,
                                                                 "limit": 1000})
        if not f:
            break
        rows += f
        t = f[-1]["fundingTime"] + 1
        if len(f) < 1000:
            break
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["date", "funding_rate"])
    df["date"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True).dt.floor("h")
    df["funding_rate"] = df["fundingRate"].astype(float)
    return df[["date", "funding_rate"]]


def main():
    for c in COINS:
        sym = f"{c}USDT"
        k = klines(sym)
        k.to_feather(OUT / f"{c}-1h.feather")
        f = funding(sym)
        f.reset_index(drop=True).to_feather(OUT / f"{c}-funding.feather")
        print(f"{c}: {len(k)} candles from {k.date.min() if len(k) else '-'}, {len(f)} funding rows", flush=True)


if __name__ == "__main__":
    main()
