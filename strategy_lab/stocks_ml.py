"""Step 2 of the stock long/short + ML test: walk-forward machine learning on the candidates from
stocks_candidates.py.

    ..\\freqtrade_bot\\.venv\\Scripts\\python stocks_ml.py --market nse     (that venv has scikit-learn)

For each month, two gradient-boosting models (one for longs, one for shorts) are trained only on earlier months
and predict the net R of that month's candidates. Per stock and day the better side is taken if its prediction is
at least the threshold. Fixed in advance (main): threshold 0.10R and at most 3 trades a day (the bots' limit),
best predictions first. Baselines on the same months: every long (the bots' rule), every short, both.
"""
import argparse
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

warnings.filterwarnings("ignore")
HERE = Path(__file__).resolve().parent
FEATS = ["gap", "or_pct", "first_ret", "first_closepos", "first_vol_x", "prev_ret", "ret5", "vola20", "rs21", "rs63",
         "trend20", "idx_up50", "idx_up200", "idx_ret1", "idx_ret5", "log_price", "trigger_bar", "risk_pct",
         "trigger_move"]
MAIN_THR, MAX_PER_DAY, MIN_TRAIN_MONTHS = 0.10, 3, 6


def model():
    return HistGradientBoostingRegressor(max_iter=250, learning_rate=0.05, max_leaf_nodes=15,
                                         min_samples_leaf=60, l2_regularization=1.0, random_state=7)


def stats(t):
    if t.empty:
        return {"trades": 0}
    t = t.assign(year=t["day"].str[:4], month=t["day"].str[:7])
    bm = t.groupby("month")["r_net"].sum()
    return {"trades": int(len(t)), "win_pct": round(float((t["r_net"] > 0).mean() * 100), 1),
            "avg_r_gross": round(float(t["r"].mean()), 3), "avg_r_net": round(float(t["r_net"].mean()), 3),
            "total_r_net": round(float(t["r_net"].sum()), 1), "months_positive": f"{int((bm > 0).sum())}/{len(bm)}",
            "longs": int((t["side"] == "long").sum()), "shorts": int((t["side"] == "short").sum()),
            "by_year": {y: {"trades": int(len(g)), "avg_r_net": round(float(g["r_net"].mean()), 3),
                            "total_r_net": round(float(g["r_net"].sum()), 1)} for y, g in t.groupby("year")}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True, choices=["nse", "us"])
    a = ap.parse_args()
    raw = json.loads((HERE / "results" / f"candidates_{a.market}.json").read_text())
    df = pd.DataFrame(raw["candidates"])
    df["month"] = df["day"].str[:7]
    months = sorted(df["month"].unique())
    test_months = months[MIN_TRAIN_MONTHS:]
    preds = []
    for m in test_months:
        train, test = df[df["month"] < m], df[df["month"] == m].copy()
        test["pred"] = np.nan
        for side in ("long", "short"):
            tr, te = train[train["side"] == side], test["side"] == side
            if len(tr) < 300 or not te.any():
                continue
            mdl = model().fit(tr[FEATS], tr["r_net"].clip(-3, 3))
            test.loc[te, "pred"] = mdl.predict(test.loc[te, FEATS])
        preds.append(test)
    p = pd.concat(preds)

    def pick(thr, cap):
        best = p.dropna(subset=["pred"]).sort_values("pred", ascending=False).drop_duplicates(["sym", "day"])
        best = best[best["pred"] >= thr]
        if cap:
            best = best.groupby("day", group_keys=False).head(cap)
        return best

    runs = {"bots' rule: every long breakout": p[p["side"] == "long"],
            "every short breakdown": p[p["side"] == "short"],
            "both sides, every signal": p,
            "ML >= 0.0R": pick(0.0, None),
            "ML >= 0.1R": pick(0.1, None),
            "ML >= 0.2R": pick(0.2, None),
            "ML >= 0.1R, max 3 a day (main)": pick(MAIN_THR, MAX_PER_DAY)}
    rep = {"market": a.market, "candidates": int(len(df)), "test_months": [test_months[0], test_months[-1]],
           "runs": {k: stats(v) for k, v in runs.items()}}
    print(f"\n=== {a.market.upper()}: {raw['stocks']} stocks, {len(df)} candidates, unseen months "
          f"{test_months[0]} .. {test_months[-1]} ({len(test_months)}) ===")
    print(f"{'':36} {'trades':>7} {'won%':>6} {'R gross':>8} {'R net':>7} {'total R':>8} {'months+':>8}  by year (avg R net)")
    for k, s in rep["runs"].items():
        if not s["trades"]:
            print(f"{k:36} {0:>7}")
            continue
        yrs = "  ".join(f"{y}:{v['avg_r_net']:+.3f}({v['trades']})" for y, v in s["by_year"].items())
        print(f"{k:36} {s['trades']:>7} {s['win_pct']:>6} {s['avg_r_gross']:>+8.3f} {s['avg_r_net']:>+7.3f} "
              f"{s['total_r_net']:>+8.1f} {s['months_positive']:>8}  {yrs}")
    (HERE / "results" / f"stocks_ml_{a.market}.json").write_text(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
