"""Forward test: 20% trailing stop vs the bot's fixed exits, replayed on 1-minute candles (PREREG_forward_trail.md).

    ..\\.venv\\Scripts\\python forward_trail.py          -> forward_trail.json
    ..\\.venv\\Scripts\\python forward_trail.py --count  only count trades so far
Read-only towards the bot (opens its database read-only, downloads public Binance candles, throttled).
"""
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
DB = HERE / "tradesv3.pumpml.dryrun.sqlite"
OUT = HERE / "forward_trail.json"
CACHE = HERE / "forward_trail_cache.json"
START = pd.Timestamp("2026-11-08 03:30", tz="UTC")
COST = 0.0015 * 2
TRAIL, HOURS = 0.20, 24


def minutes(pair, t0):
    sym = pair.split("/")[0] + "USDT"
    r = requests.get("https://fapi.binance.com/fapi/v1/klines", timeout=30,
                     params={"symbol": sym, "interval": "1m", "startTime": int(t0.timestamp() * 1000), "limit": 1500})
    time.sleep(0.3)
    if r.status_code != 200:
        return None
    k = np.array(r.json(), dtype=object)
    return k[:, 1:5].astype(float) if len(k) >= HOURS * 60 else None   # open, high, low, close


def fixed(a, e, short):
    o, h, lo, c = a.T
    tp, sl = (e * 0.94, e * 1.10) if short else (e * 1.08, e * 0.92)
    for k in range(HOURS * 60):
        if short:
            if h[k] >= sl:
                return -(max(o[k], sl) / e - 1), k
            if lo[k] <= tp:
                return -(min(o[k], tp) / e - 1), k
        else:
            if lo[k] <= sl:
                return min(o[k], sl) / e - 1, k
            if h[k] >= tp:
                return max(o[k], tp) / e - 1, k
    k = HOURS * 60 - 1
    return (-1 if short else 1) * (c[k] / e - 1), k


def trail(a, e, short):
    o, h, lo, c = (x[:HOURS * 60] for x in a.T)
    if short:
        stop = np.minimum.accumulate(np.r_[e, lo[:-1]]) * (1 + TRAIL)
        hit = (o >= stop) | (h >= stop)
    else:
        stop = np.maximum.accumulate(np.r_[e, h[:-1]]) * (1 - TRAIL)
        hit = (o <= stop) | (lo <= stop)
    if hit.any():
        q = int(np.argmax(hit))
        gap = (o[q] >= stop[q]) if short else (o[q] <= stop[q])
        px = o[q] if gap else stop[q]
        return (-1 if short else 1) * (px / e - 1), q
    k = len(c) - 1
    return (-1 if short else 1) * (c[k] / e - 1), k


def funding(pair, t0, t1, short):
    sym = pair.split("/")[0] + "USDT"
    r = requests.get("https://fapi.binance.com/fapi/v1/fundingRate", timeout=20,
                     params={"symbol": sym, "startTime": int(t0.timestamp() * 1000) + 1,
                             "endTime": int(t1.timestamp() * 1000), "limit": 1000})
    time.sleep(0.25)
    rate = sum(float(x["fundingRate"]) for x in r.json()) if r.status_code == 200 else 0.0
    return rate if short else -rate


def main():
    now = pd.Timestamp(datetime.now(timezone.utc))
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    t = pd.read_sql("select id, pair, is_short, open_rate, open_date, close_profit_abs, is_open from trades", con)
    con.close()
    t["open_date"] = pd.to_datetime(t["open_date"], utc=True)
    t = t[t["open_date"] >= START].sort_values("id")
    ready = t[t["open_date"] + pd.Timedelta(hours=HOURS, minutes=10) <= now]
    print(json.dumps({"trades_since_start": int(len(t)), "ready_to_replay": int(len(ready)), "start": str(START)}))
    if "--count" in sys.argv:
        return
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    rows = []
    for _, x in ready.iterrows():
        key = str(x["id"])
        if key not in cache:
            t0 = x["open_date"].floor("min")
            a = minutes(x["pair"], t0)
            if a is None:
                cache[key] = None
                continue
            short, e = bool(x["is_short"]), float(x["open_rate"])
            rf, kf = fixed(a, e, short)
            rt, kt = trail(a, e, short)
            ff = funding(x["pair"], t0, t0 + pd.Timedelta(minutes=kf + 1), short)
            ft = funding(x["pair"], t0, t0 + pd.Timedelta(minutes=kt + 1), short)
            cache[key] = {"pair": x["pair"], "side": "short" if short else "long", "entry": str(t0),
                          "fixed_pct": round((rf - COST + ff) * 100, 3), "fixed_min": kf + 1,
                          "trail_pct": round((rt - COST + ft) * 100, 3), "trail_min": kt + 1,
                          "bot_pnl_usdt": round(float(x["close_profit_abs"] or 0), 2)}
            CACHE.write_text(json.dumps(cache, indent=1))
        if cache[key]:
            rows.append(cache[key])
    d = np.array([r["trail_pct"] - r["fixed_pct"] for r in rows]) / 100
    tst = float(d.mean() / (d.std(ddof=1) / np.sqrt(len(d)))) if len(d) > 2 and d.std(ddof=1) > 0 else 0.0
    rep = {"prereg": "PREREG_forward_trail.md", "updated": now.isoformat(), "replayed": len(rows),
           "fixed_sum_usdt": round(sum(r["fixed_pct"] for r in rows), 2),
           "trail_sum_usdt": round(sum(r["trail_pct"] for r in rows), 2),
           "mean_diff_pct": round(float(d.mean() * 100), 3) if len(d) else None, "t": round(tst, 2), "trades": rows}
    print(json.dumps({k: v for k, v in rep.items() if k != "trades"}))
    OUT.write_text(json.dumps(rep, indent=1))
    print(f"saved {OUT.name}")


if __name__ == "__main__":
    main()
