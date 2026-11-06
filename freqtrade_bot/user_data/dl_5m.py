"""Download 12 months of 5-minute futures candles for the 100 most traded coins (FVG 5m test).

Coins are ranked by USDT traded over the last 90 days of the hourly data in data_5y. Writes
user_data/data_5m/<COIN>_USDT_USDT-5m-futures.feather. Uses the same gentle request pacing as dl_5y.py
(about 3 requests a second, pause around every full hour UTC). Safe to stop and rerun.

    .venv\\Scripts\\python user_data\\dl_5m.py      (from freqtrade_bot)
"""
import time

import pandas as pd

import dl_5y as D

OUT = D.HERE / "data_5m"
START = pd.Timestamp("2025-11-01", tz="UTC")
END = pd.Timestamp("2026-10-29 15:00", tz="UTC")  # end of the funding data in data_5y
TOP = 100


def top_coins():
    vol = {}
    for p in (D.HERE / "data_5y").glob("*_USDT_USDT-1h-futures.feather"):
        d = pd.read_feather(p, columns=["date", "close", "volume"])
        if d["date"].min() > START or d["date"].max() < END - pd.Timedelta(days=2):
            continue  # needs the whole 12 months
        d = d[d["date"] >= END - pd.Timedelta(days=90)]
        vol[p.name.split("_USDT_USDT")[0]] = float((d["close"] * d["volume"]).sum())
    return [c for c, _ in sorted(vol.items(), key=lambda x: -x[1])[:TOP]]


def candles(sym):
    rows, t, end = [], int(START.timestamp() * 1000), int(END.timestamp() * 1000)
    while t < end:
        k = D.get("klines", {"symbol": sym, "interval": "5m", "startTime": t, "endTime": end - 1, "limit": 1500})
        if not k:
            break
        rows += [[x[0], float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])] for x in k]
        t = k[-1][0] + 300_000
        if len(k) < 1500:
            break
    d = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    d["date"] = pd.to_datetime(d["date"], unit="ms", utc=True)
    return d


def main():
    OUT.mkdir(exist_ok=True)
    coins = top_coins()
    print(f"{len(coins)} coins: {' '.join(coins[:20])} ...", flush=True)
    t0 = time.time()
    for n, coin in enumerate(coins, 1):
        f = OUT / f"{coin}_USDT_USDT-5m-futures.feather"
        if f.exists():
            continue
        d = candles(coin + "USDT")
        if len(d):
            d.to_feather(f)
        print(f"{n}/{len(coins)} {coin:<12} {len(d)} candles ({time.time() - t0:.0f}s)", flush=True)
    print(f"done in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
