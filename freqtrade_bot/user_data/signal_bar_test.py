"""Prediction-bar and ranking test for PumpML, as pre-registered in PREREG_signal_bar.md.

Same events, model, walk-forward and costs as trade_limit_test.py; only the bar and the order within an hour change.

    ..\\.venv\\Scripts\\python signal_bar_test.py      -> signal_bar_results.json
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ml_5y as Y  # noqa: E402
import ml_pumpdump as M  # noqa: E402
from ml_features import FEATURES  # noqa: E402
from trade_limit_test import period  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "signal_bar_results.json"
SPLIT = pd.Timestamp("2025-01-01", tz="UTC")
CURRENT = "B0.5 FC (current)"
VARIANTS = {CURRENT: (0.005, False), "B0.5 R": (0.005, True), "B1.0 FC": (0.010, False),
            "B1.0 R": (0.010, True), "B1.5 R": (0.015, True), "B2.0 R": (0.020, True)}


def ranked_portfolio(trades, stake=100.0, max_open=5):
    """M.portfolio, but within one entry hour the highest prediction goes first."""
    trades = trades.sort_values(["entry_time", "pred"], ascending=[True, False], kind="mergesort")
    open_until, pnl, taken = [], 0.0, []
    for _, t in trades.iterrows():
        open_until = [x for x in open_until if x > t["entry_time"]]
        if len(open_until) >= max_open:
            continue
        open_until.append(t["exit_time"])
        pnl += stake * t["ret"]
        taken.append(t)
    return pd.DataFrame(taken), pnl


def sides(taken):
    if taken.empty:
        return {}
    return {s: {"trades": int(len(g)), "avg_pct": round(float(g["ret"].mean() * 100), 3),
                "pnl_usdt": round(float(g["ret"].sum() * 100), 1)} for s, g in taken.groupby(taken["side"].copy())}


def main():
    t0 = time.time()
    print("building events ...", flush=True)
    ev = Y.build_events()
    first = ev["exit_time"].iloc[min(1000, len(ev) - 1)]
    months = pd.date_range((first + pd.offsets.MonthBegin(1)).normalize(), ev["date"].max(), freq="MS")
    picks = []
    for m in months:
        train = ev[ev["exit_time"] < m]
        test = ev[(ev["date"] >= m) & (ev["date"] < m + pd.offsets.MonthBegin(1))]
        if len(train) < 1000 or test.empty:
            continue
        t = test.copy()
        t["pred_long"] = M.model().fit(train[FEATURES], train["long_ret"]).predict(test[FEATURES])
        t["pred_short"] = M.model().fit(train[FEATURES], train["short_ret"]).predict(test[FEATURES])
        picks.append(t)
    p = pd.concat(picks)
    print(f"{len(ev)} events, predictions done ({time.time() - t0:.0f}s)", flush=True)

    rep = {"prereg": "PREREG_signal_bar.md", "split": str(SPLIT.date()), "variants": {}}
    for name, (bar, ranked) in VARIANTS.items():
        c = M.choose(p, bar)
        c["pred"] = np.where(c["side"] == "long", c["pred_long"], c["pred_short"])
        c["ret"] = np.where(c["side"] == "long", c["long_ret"] + c["long_fund"] - Y.DELAY_COST,
                            c["short_ret"] + c["short_fund"] - Y.DELAY_COST)
        taken, pnl = ranked_portfolio(c) if ranked else M.portfolio(c, 100.0, 5)
        a_t, b_t = taken[taken["entry_time"] < SPLIT], taken[taken["entry_time"] >= SPLIT]
        res = {"pick": period(a_t) | {"sides": sides(a_t)}, "holdout": period(b_t) | {"sides": sides(b_t)},
               "total_pnl_usdt": round(pnl, 1)}
        rep["variants"][name] = res
        a, b = res["pick"], res["holdout"]
        print(f"{name:18} pick {a['trades']:>5} tr  win {a.get('win_pct', 0):>4}%  avg {a.get('avg_pct', 0):+.3f}%  "
              f"P&L {a['pnl_usdt']:+8.1f}  t {a['t']:+.2f} | holdout {b['trades']:>5} tr  P&L {b['pnl_usdt']:+8.1f}",
              flush=True)

    base = rep["variants"][CURRENT]
    elig = {n: v for n, v in rep["variants"].items() if n != CURRENT and v["pick"]["pnl_usdt"] > 0
            and v["pick"]["t"] >= 2.0 and v["pick"]["pnl_usdt"] > base["pick"]["pnl_usdt"]}
    if not elig:
        rep["decision"] = {"eligible": [], "PASS": False,
                           "why": "no variant had positive pick P&L with t >= 2.0 that beat the current rule"}
    else:
        best = max(elig, key=lambda n: elig[n]["pick"]["pnl_usdt"])
        ho = elig[best]["holdout"]["pnl_usdt"]
        rep["decision"] = {"eligible": list(elig), "chosen": best, "holdout_pnl": ho,
                           "PASS": bool(ho > 0 and ho > base["holdout"]["pnl_usdt"])}
    print("\ndecision:", rep["decision"])
    OUT.write_text(json.dumps(rep, indent=1, default=str))
    print(f"saved {OUT.name}  ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
