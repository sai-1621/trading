"""Open-trade limit test for PumpML, as pre-registered in PREREG_trade_limit.md.

Same events, model and walk-forward as ml_5y.py (realistic costs); only the open-trade rule changes.

    ..\\.venv\\Scripts\\python trade_limit_test.py      -> trade_limit_results.json
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

HERE = Path(__file__).resolve().parent
OUT = HERE / "trade_limit_results.json"
SPLIT = pd.Timestamp("2025-01-01", tz="UTC")
VARIANTS = {"L3": (3, None), "L5 (current)": (5, None), "L8": (8, None), "L10": (10, None),
            "H2-5": (5, 2), "H2-10": (10, 2)}


def portfolio(trades, max_open, per_hour, stake=100.0):
    """M.portfolio, plus an optional cap on new trades sharing one entry hour."""
    if per_hour is None:
        return M.portfolio(trades, stake, max_open)
    trades = trades.sort_values("entry_time")
    open_until, pnl, taken, per = [], 0.0, [], {}
    for _, t in trades.iterrows():
        open_until = [x for x in open_until if x > t["entry_time"]]
        if len(open_until) >= max_open or per.get(t["entry_time"], 0) >= per_hour:
            continue
        open_until.append(t["exit_time"])
        per[t["entry_time"]] = per.get(t["entry_time"], 0) + 1
        pnl += stake * t["ret"]
        taken.append(t)
    return pd.DataFrame(taken), pnl


def tstat(x):
    x = np.asarray(x, float)
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) if len(x) > 2 and x.std(ddof=1) > 0 else 0.0


def period(taken):
    if taken.empty:
        return {"trades": 0, "pnl_usdt": 0.0, "t": 0.0}
    x = taken.sort_values("exit_time")
    usd = x["ret"].to_numpy() * 100
    eq = np.cumsum(usd)
    dd = float((eq - np.maximum.accumulate(np.maximum(eq, 0))).min())
    hours = taken.groupby("entry_time")["ret"].sum() * 100
    yrs = taken.assign(y=pd.to_datetime(taken["entry_time"]).dt.year).groupby("y")["ret"].sum() * 100
    return {"trades": int(len(taken)), "win_pct": round(float((taken["ret"] > 0).mean() * 100), 1),
            "avg_pct": round(float(taken["ret"].mean() * 100), 3), "t": round(tstat(taken["ret"]), 2),
            "pnl_usdt": round(float(usd.sum()), 1), "max_dd_usdt": round(dd, 1),
            "worst_hour_usdt": round(float(hours.min()), 1),
            "by_year": {int(k): round(float(v), 1) for k, v in yrs.items()}}


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
    chosen = M.choose(p, M.MIN_PRED)
    chosen["ret"] = np.where(chosen["side"] == "long", chosen["long_ret"] + chosen["long_fund"] - Y.DELAY_COST,
                             chosen["short_ret"] + chosen["short_fund"] - Y.DELAY_COST)

    rep = {"prereg": "PREREG_trade_limit.md", "split": str(SPLIT.date()), "variants": {}}
    for name, (mx, ph) in VARIANTS.items():
        taken, pnl = portfolio(chosen, mx, ph)
        res = {"pick": period(taken[taken["entry_time"] < SPLIT]), "holdout": period(taken[taken["entry_time"] >= SPLIT]),
               "total_pnl_usdt": round(pnl, 1)}
        rep["variants"][name] = res
        a, b = res["pick"], res["holdout"]
        print(f"{name:13} pick {a['trades']:>5} tr  P&L {a['pnl_usdt']:+8.1f}  t {a['t']:+.2f}  dd {a['max_dd_usdt']:+7.1f} | "
              f"holdout {b['trades']:>5} tr  P&L {b['pnl_usdt']:+8.1f}  | total {pnl:+.1f}", flush=True)

    base = rep["variants"]["L5 (current)"]
    elig = {n: v for n, v in rep["variants"].items() if n != "L5 (current)" and v["pick"]["pnl_usdt"] > 0
            and v["pick"]["t"] >= 2.0 and v["pick"]["pnl_usdt"] > base["pick"]["pnl_usdt"]}
    if not elig:
        rep["decision"] = {"eligible": [], "PASS": False, "why": "no variant had positive pick P&L with t >= 2.0 "
                                                                 "that beat the current limit; keep 5"}
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
