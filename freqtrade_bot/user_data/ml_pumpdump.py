"""Machine-learning experiment: can a model learn which pumps/dumps continue and which reverse?

1. Events: every closed hour on every Binance USDT futures coin that moved at least 5% (up or down) on at least
   3x its normal volume, with at least $2M traded in the last 24 hours (ml_features.is_event).
2. Two possible trades per event, entered at the next hour's open (1x, fee+slippage 0.15% per side):
     long : take profit +8%, stop -8%, out after 24 h
     short: take profit -6%, stop +10%, out after 24 h
   If the stop and target are both touched within the same hour, the stop is assumed hit first.
   Funding payments are not included.
3. Walk-forward: for each month from Apr 2025, a gradient-boosting model is trained only on events whose trade
   had finished before that month started, then predicts that month's events. A trade is taken only when the
   predicted net return is at least MIN_PRED (fixed in advance: 0.5%).
4. Portfolio: 1,000 USDT, 100 USDT per trade, at most 5 open at once, trades taken in time order.

    .venv\\Scripts\\python user_data\\ml_pumpdump.py           walk-forward test -> ml_results.json
    .venv\\Scripts\\python user_data\\ml_pumpdump.py --save    also train on everything -> models/pumpml.joblib
Refresh the data first: freqtrade download-data -c user_data/config-pump.json --timeframes 1h --userdir user_data
"""
import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ml_features import FEATURES, MIN_CANDLES, add_market, breadth_from, btc_features, is_event, pair_features  # noqa: E402

warnings.filterwarnings("ignore")
HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "binance" / "futures"
PAIRS = (HERE / "pump_bt_pairs_all.txt").read_text().split()
OUT = HERE / "ml_results.json"
MODEL = HERE / "models" / "pumpml.joblib"

FEE = 0.0015 * 2
LONG_TP, LONG_SL = 0.08, 0.08
SHORT_TP, SHORT_SL = 0.06, 0.10
HOLD = 24
MIN_PRED = 0.005


def load(pair, kind="futures"):
    return pd.read_feather(DATA / f"{pair.split('/')[0]}_USDT_USDT-1h-{kind}.feather")


def simulate(o, h, lo, c, i):
    """Net returns of the long and the short trade for an event at row i (entry = open of row i+1)."""
    n = len(c)
    if i + 1 >= n:
        return np.nan, np.nan, None
    entry = o[i + 1]
    last = min(i + HOLD, n - 1)
    lt, ls = entry * (1 + LONG_TP), entry * (1 - LONG_SL)
    st, ss = entry * (1 - SHORT_TP), entry * (1 + SHORT_SL)
    long_r = short_r = None
    for j in range(i + 1, last + 1):
        if long_r is None:
            if lo[j] <= ls:
                long_r = -LONG_SL
            elif h[j] >= lt:
                long_r = LONG_TP
        if short_r is None:
            if h[j] >= ss:
                short_r = -SHORT_SL
            elif lo[j] <= st:
                short_r = SHORT_TP
        if long_r is not None and short_r is not None:
            break
    if last - i < HOLD and (long_r is None or short_r is None):
        return np.nan, np.nan, None
    if long_r is None:
        long_r = c[last] / entry - 1
    if short_r is None:
        short_r = 1 - c[last] / entry
    return long_r - FEE, short_r - FEE, last


def build_events():
    data = {}
    for p in PAIRS:
        df = load(p)
        if len(df) >= MIN_CANDLES:
            data[p] = df
    btc = btc_features(data["BTC/USDT:USDT"])
    breadth = breadth_from({p: d.set_index("date")["close"] for p, d in data.items()})
    rows = []
    for k, (pair, df) in enumerate(data.items()):
        f = pair_features(df)
        o, h, lo, c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
        for i in np.where(is_event(f))[0]:
            lr, sr, last = simulate(o, h, lo, c, i)
            if last is None:
                continue
            row = f.iloc[i].to_dict()
            row.update({"pair": pair, "long_ret": lr, "short_ret": sr,
                        "entry_time": df["date"].iloc[i + 1], "exit_time": df["date"].iloc[last]})
            rows.append(row)
        if k % 100 == 0:
            print(f"  {k}/{len(data)} coins, {len(rows)} events", flush=True)
    ev = add_market(pd.DataFrame(rows), btc, breadth)
    return ev.sort_values("date").reset_index(drop=True)


def model():
    return HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=31,
                                         min_samples_leaf=80, l2_regularization=1.0, random_state=7)


def portfolio(trades, stake=100.0, max_open=5):
    trades = trades.sort_values("entry_time")
    open_until, pnl, taken = [], 0.0, []
    for _, t in trades.iterrows():
        open_until = [x for x in open_until if x > t["entry_time"]]
        if len(open_until) >= max_open:
            continue
        open_until.append(t["exit_time"])
        pnl += stake * t["ret"]
        taken.append(t)
    return pd.DataFrame(taken), pnl


def choose(df, thr):
    long_ok = (df["pred_long"] >= thr) & (df["pred_long"] >= df["pred_short"])
    short_ok = (df["pred_short"] >= thr) & (df["pred_short"] > df["pred_long"])
    a = df[long_ok].assign(side="long", ret=df.loc[long_ok, "long_ret"])
    b = df[short_ok].assign(side="short", ret=df.loc[short_ok, "short_ret"])
    return pd.concat([a, b])


def main():
    t0 = time.time()
    print("building events ...", flush=True)
    ev = build_events()
    print(f"{len(ev)} events from {ev['date'].min()} to {ev['date'].max()} ({time.time() - t0:.0f}s)", flush=True)
    months = pd.date_range(pd.Timestamp("2025-04-01", tz="UTC"), ev["date"].max(), freq="MS")
    picks = []
    for m in months:
        end = m + pd.offsets.MonthBegin(1)
        train = ev[ev["exit_time"] < m]
        test = ev[(ev["date"] >= m) & (ev["date"] < end)]
        if len(train) < 1000 or test.empty:
            continue
        t = test.copy()
        t["pred_long"] = model().fit(train[FEATURES], train["long_ret"]).predict(test[FEATURES])
        t["pred_short"] = model().fit(train[FEATURES], train["short_ret"]).predict(test[FEATURES])
        picks.append(t)
    p = pd.concat(picks)
    report = {"run_at": datetime.now(timezone.utc).isoformat(), "features": FEATURES, "events": int(len(ev)),
              "settings": {"fee_round_trip": FEE, "long": [LONG_TP, LONG_SL], "short": [SHORT_TP, SHORT_SL],
                           "hold_h": HOLD, "min_pred": MIN_PRED}, "runs": {}}
    variants = {"all events, always long": p.assign(side="long", ret=p["long_ret"]),
                "all events, always short": p.assign(side="short", ret=p["short_ret"]),
                "model, pred >= 0.0%": choose(p, 0.0),
                "model, pred >= 0.5% (main)": choose(p, MIN_PRED),
                "model, pred >= 1.0%": choose(p, 0.01)}
    for name, tr in variants.items():
        taken, pnl = portfolio(tr)
        if taken.empty:
            report["runs"][name] = {"trades": 0}
            continue
        taken["month"] = pd.to_datetime(taken["date"]).dt.strftime("%Y-%m")
        bm = taken.groupby("month")["ret"].agg(["size", "sum"])
        yr = taken.assign(y=pd.to_datetime(taken["date"]).dt.year).groupby("y")["ret"].agg(["size", "mean", "sum"])
        r = report["runs"][name] = {
            "trades": int(len(taken)), "win_pct": round(float((taken["ret"] > 0).mean() * 100), 1),
            "avg_net_pct": round(float(taken["ret"].mean() * 100), 2), "pnl_usdt": round(pnl, 1),
            "longs": int((taken["side"] == "long").sum()), "shorts": int((taken["side"] == "short").sum()),
            "by_year": {int(y): {"trades": int(v["size"]), "avg_net_pct": round(v["mean"] * 100, 2),
                                 "pnl_usdt": round(v["sum"] * 100, 1)} for y, v in yr.iterrows()},
            "months_positive": f"{int((bm['sum'] > 0).sum())}/{len(bm)}",
            "by_month": {k: {"trades": int(v["size"]), "pnl_usdt": round(float(v["sum"] * 100), 1)} for k, v in bm.iterrows()}}
        print(f"\n{name}: {r['trades']} trades ({r['longs']} long / {r['shorts']} short), won {r['win_pct']}%, "
              f"avg {r['avg_net_pct']}% per trade, P&L {r['pnl_usdt']} USDT on 1,000, months positive {r['months_positive']}")
        for y, v in r["by_year"].items():
            print(f"   {y}: {v['trades']} trades, avg {v['avg_net_pct']}%, P&L {v['pnl_usdt']} USDT")
    OUT.write_text(json.dumps(report, indent=1, default=str))
    print(f"\nsaved {OUT.name}")
    if "--save" in sys.argv:
        MODEL.parent.mkdir(exist_ok=True)
        bundle = {"features": FEATURES, "min_pred": MIN_PRED,
                  "long": model().fit(ev[FEATURES], ev["long_ret"]),
                  "short": model().fit(ev[FEATURES], ev["short_ret"]),
                  "trained_at": datetime.now(timezone.utc).isoformat(), "events": int(len(ev)),
                  "data_from": str(ev["date"].min()), "data_to": str(ev["date"].max()),
                  "exits": {"long": [LONG_TP, LONG_SL], "short": [SHORT_TP, SHORT_SL], "hold_h": HOLD}}
        joblib.dump(bundle, MODEL)
        main_run = report["runs"].get("model, pred >= 0.5% (main)", {})
        MODEL.with_suffix(".json").write_text(json.dumps({k: v for k, v in bundle.items() if k not in ("long", "short")}
                                                         | {"walk_forward": {k: main_run.get(k) for k in (
                                                             "trades", "win_pct", "avg_net_pct", "pnl_usdt", "by_year", "months_positive")}},
                                                         indent=1))
        print(f"saved model {MODEL} (trained on {len(ev)} events)")
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
