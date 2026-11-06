"""Download older hourly futures candles and funding rates (from Nov 2021) for the PumpML 5-year test.

Writes user_data/data_5y/<COIN>_USDT_USDT-1h-futures.feather and ...-1h-funding_rate.feather: the older history
joined with the existing files in data/binance/futures (those are not changed). Gentle on Binance so the live bots
on the same internet connection are not blocked: about 3 requests a second, and a pause from 2 minutes before to
4 minutes after every full hour UTC (the bots' hourly check at :30 IST). Safe to stop and rerun: finished coins
are skipped.

    ..\\.venv\\Scripts\\python dl_5y.py
"""
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
SRC = HERE / "data" / "binance" / "futures"
OUT = HERE / "data_5y"
PAIRS = (HERE / "pump_bt_pairs_all.txt").read_text().split()
START = pd.Timestamp("2021-11-01", tz="UTC")
API = "https://fapi.binance.com/fapi/v1"
GAP = 0.33
_last = [0.0]


def get(path, params):
    while True:
        now = datetime.now(timezone.utc)
        if now.minute >= 58 or now.minute < 4:      # stay clear of the bots' hourly check
            time.sleep(20)
            continue
        wait = GAP - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            r = requests.get(f"{API}/{path}", params=params, timeout=20)
        except requests.RequestException:
            time.sleep(10)
            continue
        if r.status_code in (418, 429) or r.status_code >= 500:
            time.sleep(60)
            continue
        if r.status_code != 200:
            return []
        return r.json()


def candles(sym, start_ms, end_ms):
    rows, t = [], start_ms
    while t < end_ms:
        k = get("klines", {"symbol": sym, "interval": "1h", "startTime": t, "endTime": end_ms - 1, "limit": 1500})
        if not k:
            break
        rows += [[x[0], float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])] for x in k]
        t = k[-1][0] + 3_600_000
        if len(k) < 1500:
            break
    d = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    d["date"] = pd.to_datetime(d["date"], unit="ms", utc=True)
    return d


def funding(sym, start_ms, end_ms):
    rows, t = [], start_ms
    while t < end_ms:
        k = get("fundingRate", {"symbol": sym, "startTime": t, "endTime": end_ms - 1, "limit": 1000})
        if not k:
            break
        rows += [[x["fundingTime"], float(x["fundingRate"])] for x in k]
        t = k[-1]["fundingTime"] + 1
        if len(k) < 1000:
            break
    d = pd.DataFrame(rows, columns=["date", "funding_rate"])
    d["date"] = pd.to_datetime(d["date"], unit="ms", utc=True).dt.floor("h")
    return d


def main():
    OUT.mkdir(exist_ok=True)
    t0 = time.time()
    for n, pair in enumerate(PAIRS, 1):
        coin = pair.split("/")[0]
        sym = coin + "USDT"
        fc, ff = OUT / f"{coin}_USDT_USDT-1h-futures.feather", OUT / f"{coin}_USDT_USDT-1h-funding_rate.feather"
        if fc.exists() and ff.exists():
            continue
        try:
            cur = pd.read_feather(SRC / f"{coin}_USDT_USDT-1h-futures.feather")
            curf = pd.read_feather(SRC / f"{coin}_USDT_USDT-1h-funding_rate.feather")
        except FileNotFoundError:
            continue
        end = cur["date"].min()
        old = candles(sym, int(START.timestamp() * 1000), int(end.timestamp() * 1000)) if end > START else cur.iloc[:0]
        c = pd.concat([old, cur]).drop_duplicates("date").sort_values("date").reset_index(drop=True)
        c["date"] = c["date"].astype(cur["date"].dtype)
        fend = curf["date"].min() if len(curf) else end
        oldf = funding(sym, int(START.timestamp() * 1000), int(fend.timestamp() * 1000)) if fend > START else curf.iloc[:0]
        f = pd.concat([oldf, curf]).drop_duplicates("date").sort_values("date").reset_index(drop=True)
        if len(curf):
            f["date"] = f["date"].astype(curf["date"].dtype)
        c.to_feather(fc)
        f.to_feather(ff)
        print(f"{n}/{len(PAIRS)} {coin:<14} from {c['date'].min():%Y-%m-%d}  +{len(old)} candles  ({time.time() - t0:.0f}s)",
              flush=True)
    print(f"done in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
