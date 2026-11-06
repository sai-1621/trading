"""Download 5-minute Bybit USDT-perpetual candles (public API, no keys) for the backtest.

Candidates: crypto linear USDT perps (Bybit symbolType '' or 'innovation'; stocks, ETFs, commodities and forex are
excluded) with 24h turnover >= $200k today (a little below the $500k filter, so coins that were in the band earlier
are included). The backtest applies the $0.5M-$500M filter day by day from the candles' own turnover, so the
universe has no look-ahead; coins delisted before today are missing (survivorship bias).

Writes data/<SYMBOL>.npz (ts ms int64; open, high, low, close, volume, turnover). A coin is saved only when its
history is complete back to max(start, listing time); a failed page is retried, never silently truncated.
4 parallel workers, about 8 requests a second in total (Bybit allows far more). Safe to rerun.

    .venv\\Scripts\\python dl_bybit.py --months 6
"""
import argparse
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
API = "https://api.bybit.com/v5/market"
NON_CRYPTO = {"stock", "ETF", "commodity", "forex"}
PEGGED = {"XAUTUSDT", "PAXGUSDT", "USDCUSDT", "USDEUSDT", "FDUSDUSDT"}
GAP = 0.12                      # seconds between requests, shared by all workers
_lock = threading.Lock()
_last = [0.0]


def get(path, params, tries=8):
    for attempt in range(tries):
        with _lock:
            wait = GAP - (time.time() - _last[0])
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        try:
            r = requests.get(f"{API}/{path}", params=params, timeout=20)
            js = r.json()
            if r.status_code == 200 and js.get("retCode") == 0:
                return js["result"]
            time.sleep(5 + attempt * 5)          # rate limit or server trouble: back off and retry
        except (requests.RequestException, ValueError):
            time.sleep(3 + attempt * 4)
    return None


def instruments() -> dict:
    out, cur = {}, None
    while True:
        p = {"category": "linear", "limit": 1000}
        if cur:
            p["cursor"] = cur
        res = get("instruments-info", p)
        for r in res["list"]:
            out[r["symbol"]] = r
        cur = res.get("nextPageCursor")
        if not cur:
            return out


def candidates(min_turnover=2e5):
    inst = instruments()
    tick = {x["symbol"]: float(x["turnover24h"]) for x in get("tickers", {"category": "linear"})["list"]}
    out = {}
    for s, r in inst.items():
        if (s.endswith("USDT") and r.get("contractType") == "LinearPerpetual" and r.get("status") == "Trading"
                and r.get("symbolType", "") not in NON_CRYPTO and s not in PEGGED and tick.get(s, 0) >= min_turnover):
            out[s] = int(r.get("launchTime") or 0)
    return out


def klines(sym, start_ms, end_ms):
    """Complete history or None (never a silently truncated series)."""
    out, end = [], end_ms
    while end > start_ms:
        res = get("kline", {"category": "linear", "symbol": sym, "interval": "5", "start": start_ms, "end": end,
                            "limit": 1000})
        if res is None:
            return None                           # a page failed after retries: do not save a partial series
        rows = res.get("list") or []
        if not rows:
            break
        out += rows
        oldest = int(rows[-1][0])
        if len(rows) < 1000 or oldest <= start_ms:
            break
        end = oldest - 1
    if not out:
        return None
    a = np.array(out, dtype=object)
    ts = a[:, 0].astype(np.int64)
    order = np.argsort(ts)
    ts, idx = np.unique(ts[order], return_index=True)
    return ts, a[order][idx][:, 1:7].astype(np.float64)


def complete(f: Path, start_ms: int, launch_ms: int) -> bool:
    try:
        ts = np.load(f)["ts"]
    except Exception:  # noqa: BLE001
        return False
    want = max(start_ms, launch_ms)
    return len(ts) > 0 and ts[0] <= want + 2 * 86400 * 1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=float, default=6)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    DATA.mkdir(exist_ok=True)
    cands = candidates()
    end = int(time.time() // 300 * 300 * 1000)
    start = end - int(a.months * 30.5 * 86400 * 1000)
    # remove files for excluded symbols (stocks etc.) and incomplete files from earlier runs
    for f in DATA.glob("*.npz"):
        if f.stem not in cands or not complete(f, start, cands[f.stem]):
            f.unlink()
    todo = [s for s in sorted(cands) if not (DATA / f"{s}.npz").exists()]
    print(f"{len(cands)} crypto candidates, {len(todo)} to download, {a.months} months", flush=True)
    t0, done = time.time(), [0]

    def one(s):
        k = klines(s, max(start, cands[s]), end)
        with _lock:
            done[0] += 1
            n = done[0]
        if k is None:
            print(f"{n}/{len(todo)} {s}: FAILED (will retry on the next run)", flush=True)
            return
        ts, c = k
        np.savez_compressed(DATA / f"{s}.npz", ts=ts, open=c[:, 0], high=c[:, 1], low=c[:, 2], close=c[:, 3],
                            volume=c[:, 4], turnover=c[:, 5])
        if n % 20 == 0:
            print(f"{n}/{len(todo)} {s}: {len(ts)} bars ({time.time() - t0:.0f}s)", flush=True)

    with ThreadPoolExecutor(a.workers) as pool:
        list(pool.map(one, todo))
    print(f"done in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
