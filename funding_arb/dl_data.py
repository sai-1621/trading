"""Download what the backtest needs on top of the existing perp data (public Binance API, no keys).

1. Spot 1h candles from 2021-01-01 (or listing) for every perp in data/universe_candidates.csv -> data/spot/<SPOT>.feather
2. Perp 1h candles + funding for 2021-01-01 .. 2021-11-01 for coins whose existing data starts in Nov 2021
   -> data/perp_ext/<COIN>-1h.feather, <COIN>-funding.feather

Gentle on Binance so the live bots on the same connection are not blocked: about 4 requests a second and a pause
from 2 minutes before to 4 minutes after every full hour UTC. Safe to stop and rerun: finished files are skipped.
A coin is saved only when every page arrived (a failed page is retried, never silently truncated).

    .venv\\Scripts\\python dl_data.py
"""
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OLD = HERE.parent / "freqtrade_bot" / "user_data" / "data_5y"
START = int(pd.Timestamp("2021-01-01", tz="UTC").timestamp() * 1000)
OLD_START = int(pd.Timestamp("2021-11-01", tz="UTC").timestamp() * 1000)
END = int(pd.Timestamp("2026-10-30", tz="UTC").timestamp() * 1000)
GAP = 0.25
_last = [0.0]


def get(url, params, tries=8):
    for attempt in range(tries):
        while True:
            m = datetime.now(timezone.utc).minute
            if m >= 58 or m < 4:
                time.sleep(20)
                continue
            break
        wait = GAP - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (418, 429):
                time.sleep(60 * (attempt + 1))
            elif r.status_code == 400:
                return []                       # e.g. symbol unknown for that market
            else:
                time.sleep(5 * (attempt + 1))
        except (requests.RequestException, ValueError):
            time.sleep(5 * (attempt + 1))
    return None


def klines(url, sym, start, end):
    out, cur = [], start
    while cur < end:
        rows = get(url, {"symbol": sym, "interval": "1h", "startTime": cur, "endTime": end, "limit": 1000})
        if rows is None:
            return None
        if not rows:
            break
        out += rows
        nxt = rows[-1][0] + 3600_000
        if nxt <= cur:
            break
        cur = nxt
    if not out:
        return pd.DataFrame()
    d = pd.DataFrame([r[:6] for r in out], columns=["date", "open", "high", "low", "close", "volume"])
    d["date"] = pd.to_datetime(d["date"], unit="ms", utc=True)
    d[["open", "high", "low", "close", "volume"]] = d[["open", "high", "low", "close", "volume"]].astype(float)
    return d.drop_duplicates("date").sort_values("date").reset_index(drop=True)


def funding(sym, start, end):
    out, cur = [], start
    while cur < end:
        rows = get("https://fapi.binance.com/fapi/v1/fundingRate", {"symbol": sym, "startTime": cur, "endTime": end,
                                                                     "limit": 1000})
        if rows is None:
            return None
        if not rows:
            break
        out += rows
        nxt = rows[-1]["fundingTime"] + 1
        if len(rows) < 1000 or nxt <= cur:
            break
        cur = nxt
    if not out:
        return pd.DataFrame(columns=["date", "funding_rate"])
    d = pd.DataFrame({"date": pd.to_datetime([r["fundingTime"] for r in out], unit="ms", utc=True),
                      "funding_rate": [float(r["fundingRate"]) for r in out]})
    d["date"] = d["date"].dt.floor("min")
    return d.drop_duplicates("date").sort_values("date").reset_index(drop=True)


def main():
    uni = pd.read_csv(DATA / "universe_candidates.csv")
    (DATA / "spot").mkdir(exist_ok=True)
    (DATA / "perp_ext").mkdir(exist_ok=True)
    t0 = time.time()
    # 2. perp extension (small) first
    ext = []
    for c in uni["perp"]:
        f = OLD / f"{c}_USDT_USDT-1h-futures.feather"
        if f.exists() and pd.read_feather(f, columns=["date"])["date"].iloc[0] <= pd.Timestamp("2021-11-02", tz="UTC"):
            ext.append(c)
    for n, c in enumerate(ext, 1):
        fk, ff = DATA / "perp_ext" / f"{c}-1h.feather", DATA / "perp_ext" / f"{c}-funding.feather"
        if fk.exists() and ff.exists():
            continue
        k = klines("https://fapi.binance.com/fapi/v1/klines", f"{c}USDT", START, OLD_START)
        fr = funding(f"{c}USDT", START, OLD_START)
        if k is None or fr is None:
            print(f"perp ext {c}: FAILED (rerun later)", flush=True)
            continue
        k.to_feather(fk)
        fr.to_feather(ff)
        print(f"perp ext {n}/{len(ext)} {c}: {len(k)} bars, {len(fr)} funding ({time.time() - t0:.0f}s)", flush=True)
    # 1. spot
    for n, r in enumerate(uni.itertuples(), 1):
        f = DATA / "spot" / f"{r.spot}.feather"
        if f.exists():
            continue
        k = klines("https://api.binance.com/api/v3/klines", r.spot, START, END)
        if k is None:
            print(f"spot {r.spot}: FAILED (rerun later)", flush=True)
            continue
        k.to_feather(f)
        if n % 10 == 0:
            print(f"spot {n}/{len(uni)} {r.spot}: {len(k)} bars ({time.time() - t0:.0f}s)", flush=True)
    print(f"done in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
