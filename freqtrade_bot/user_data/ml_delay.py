"""Retest of the PumpML walk-forward with a realistic entry delay, using 1-minute prices.

The original test (ml_pumpdump.py) enters exactly at the next hour's open. The live bot enters about 2 minutes
later. This script:
  1. rebuilds the same events and the same month-by-month predictions (same code, same model settings),
  2. downloads 1-minute candles from Binance for every event the model would trade (pred >= 0.5%),
  3. replays each trade entering 0, 1, 2, 5 and 10 minutes after the hour, same take-profit / stop / 24 h exit,
     same 0.15% fee+slippage per side; on 1-minute candles, which of stop or target came first is mostly known,
  4. adds funding payments over the holding time (longs pay a positive rate, shorts receive it),
  5. runs the same portfolio (1,000 USDT, 100 per trade, max 5 open) for each delay.

    .venv\\Scripts\\python user_data\\ml_delay.py      -> ml_delay_results.json
1-minute candles are cached in user_data/data_1m_events/ (one file per event).
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ml_pumpdump as M  # noqa: E402
from ml_features import FEATURES  # noqa: E402

HERE = Path(__file__).resolve().parent
CACHE = HERE / "data_1m_events"
OUT = HERE / "ml_delay_results.json"
DELAYS = [0, 1, 2, 5, 10]
MINUTES = M.HOLD * 60 + max(DELAYS) + 1


def walk_forward_picks():
    ev = M.build_events()
    months = pd.date_range(pd.Timestamp("2025-04-01", tz="UTC"), ev["date"].max(), freq="MS")
    picks = []
    for m in months:
        end = m + pd.offsets.MonthBegin(1)
        train = ev[ev["exit_time"] < m]
        test = ev[(ev["date"] >= m) & (ev["date"] < end)]
        if len(train) < 1000 or test.empty:
            continue
        t = test.copy()
        t["pred_long"] = M.model().fit(train[FEATURES], train["long_ret"]).predict(test[FEATURES])
        t["pred_short"] = M.model().fit(train[FEATURES], train["short_ret"]).predict(test[FEATURES])
        picks.append(t)
    return M.choose(pd.concat(picks), M.MIN_PRED).reset_index(drop=True)


def minutes(pair, start):
    """1-minute OHLC from start (UTC) for MINUTES minutes, cached. None if Binance has no data."""
    sym = pair.split("/")[0] + "USDT"
    ms = int(start.timestamp() * 1000)
    f = CACHE / f"{sym}_{ms}.npy"
    if f.exists():
        a = np.load(f)
        return a if len(a) else None
    rows = []
    t = ms
    while len(rows) < MINUTES:
        for attempt in range(5):
            try:
                r = requests.get("https://fapi.binance.com/fapi/v1/klines", timeout=20,
                                 params={"symbol": sym, "interval": "1m", "startTime": t, "limit": 1500})
                if r.status_code == 429 or r.status_code >= 500:
                    time.sleep(5 * (attempt + 1))
                    continue
                break
            except requests.RequestException:
                time.sleep(5 * (attempt + 1))
        if r.status_code != 200:
            break
        k = r.json()
        if not k:
            break
        rows += [[x[0], float(x[1]), float(x[2]), float(x[3]), float(x[4])] for x in k]
        t = k[-1][0] + 60_000
        if len(k) < 1500:
            break
    a = np.array(rows[:MINUTES], dtype=float) if rows else np.empty((0, 5))
    # keep only if it really starts at the hour and covers the trade
    if len(a) and (a[0, 0] != ms or len(a) < MINUTES):
        a = np.empty((0, 5))
    np.save(f, a)
    return a if len(a) else None


def replay(a, side, delay):
    """Net return (before funding) and exit index for one trade on 1-minute candles."""
    o, h, lo, c = a[:, 1], a[:, 2], a[:, 3], a[:, 4]
    entry = o[delay]
    last = delay + M.HOLD * 60 - 1
    if side == "long":
        tp, sl = entry * (1 + M.LONG_TP), entry * (1 - M.LONG_SL)
        for j in range(delay, last + 1):
            if lo[j] <= sl:
                return -M.LONG_SL - M.FEE, j
            if h[j] >= tp:
                return M.LONG_TP - M.FEE, j
        return c[last] / entry - 1 - M.FEE, last
    tp, sl = entry * (1 - M.SHORT_TP), entry * (1 + M.SHORT_SL)
    for j in range(delay, last + 1):
        if h[j] >= sl:
            return -M.SHORT_SL - M.FEE, j
        if lo[j] <= tp:
            return M.SHORT_TP - M.FEE, j
    return 1 - c[last] / entry - M.FEE, last


_funding = {}


def funding(pair, t0, t1, side):
    """Funding paid (negative) or received over (t0, t1]."""
    if pair not in _funding:
        try:
            f = pd.read_feather(M.DATA / f"{pair.split('/')[0]}_USDT_USDT-1h-funding_rate.feather")
            _funding[pair] = f.set_index("date")["funding_rate"]
        except FileNotFoundError:
            _funding[pair] = pd.Series(dtype=float)
    s = _funding[pair]
    rate = float(s[(s.index > t0) & (s.index <= t1)].sum())
    return -rate if side == "long" else rate


def summary(df):
    taken, pnl = M.portfolio(df)
    if taken.empty:
        return {"trades": 0}
    yr = taken.assign(y=pd.to_datetime(taken["date"]).dt.year).groupby("y")["ret"].agg(["size", "mean", "sum"])
    bm = taken.assign(m=pd.to_datetime(taken["date"]).dt.strftime("%Y-%m")).groupby("m")["ret"].sum()
    return {"trades": int(len(taken)), "win_pct": round(float((taken["ret"] > 0).mean() * 100), 1),
            "avg_net_pct": round(float(taken["ret"].mean() * 100), 3), "pnl_usdt": round(pnl, 1),
            "months_positive": f"{int((bm > 0).sum())}/{len(bm)}",
            "by_year": {int(y): {"trades": int(v["size"]), "avg_net_pct": round(v["mean"] * 100, 3),
                                 "pnl_usdt": round(v["sum"] * 100, 1)} for y, v in yr.iterrows()}}


def main():
    t0 = time.time()
    CACHE.mkdir(exist_ok=True)
    print("rebuilding events and walk-forward predictions ...", flush=True)
    picks = walk_forward_picks()
    print(f"{len(picks)} model picks ({time.time() - t0:.0f}s); downloading 1-minute candles ...", flush=True)
    res = {d: [] for d in DELAYS}
    hourly, missing = [], 0
    for k, row in picks.iterrows():
        a = minutes(row["pair"], row["entry_time"])
        if k % 100 == 0:
            print(f"  {k}/{len(picks)}  missing {missing}  ({time.time() - t0:.0f}s)", flush=True)
        if a is None:
            missing += 1
            continue
        base = {"date": row["date"], "pair": row["pair"], "side": row["side"]}
        hourly.append({**base, "ret": row["ret"], "entry_time": row["entry_time"], "exit_time": row["exit_time"]})
        for d in DELAYS:
            r, j = replay(a, row["side"], d)
            t_in = row["entry_time"] + pd.Timedelta(minutes=d)
            t_out = row["entry_time"] + pd.Timedelta(minutes=j + 1)
            fund = funding(row["pair"], t_in, t_out, row["side"])
            slip = a[d, 1] / a[0, 1] - 1
            res[d].append({**base, "entry_time": t_in, "exit_time": t_out, "ret_nofund": r, "funding": fund,
                           "ret": r + fund, "slip_pct": (slip if row["side"] == "long" else -slip) * 100})
    print(f"{len(hourly)} trades with 1-minute data, {missing} without (delisted or missing)", flush=True)

    report = {"picks": int(len(picks)), "with_1m": len(hourly), "missing": missing, "runs": {}}
    h = pd.DataFrame(hourly)
    report["runs"]["hourly test (same trades, original)"] = summary(h)
    for d in DELAYS:
        df = pd.DataFrame(res[d])
        s = summary(df.assign(ret=df["ret_nofund"]))
        s2 = summary(df)
        report["runs"][f"{d} min delay"] = {
            "no_funding": s, "with_funding": s2,
            "avg_slip_pct_vs_hour_open": round(float(df["slip_pct"].mean()), 3),
            "avg_funding_pct": round(float(df["funding"].mean() * 100), 3)}
    print("\n" + f"{'':38} {'trades':>6} {'won%':>6} {'avg %':>7} {'P&L':>8} {'months+':>8}   2025 / 2026 avg %")

    def line(name, s):
        y = s.get("by_year", {})
        print(f"{name:38} {s['trades']:>6} {s['win_pct']:>6} {s['avg_net_pct']:>+7.3f} {s['pnl_usdt']:>+8.1f} "
              f"{s['months_positive']:>8}   {y.get(2025, {}).get('avg_net_pct', 0):+.3f} / {y.get(2026, {}).get('avg_net_pct', 0):+.3f}")

    line("hourly test (original)", report["runs"]["hourly test (same trades, original)"])
    for d in DELAYS:
        r = report["runs"][f"{d} min delay"]
        line(f"{d} min delay, no funding", r["no_funding"])
        line(f"{d} min delay, with funding", r["with_funding"])
        print(f"{'':38} slip vs hour open {r['avg_slip_pct_vs_hour_open']:+.3f}%, funding {r['avg_funding_pct']:+.3f}% per trade")
    OUT.write_text(json.dumps(report, indent=1, default=str))
    print(f"\nsaved {OUT.name}  done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
