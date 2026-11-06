"""Exit-level test for PumpML, as pre-registered in PREREG_exits.md.

Same events, features, model and walk-forward as ml_5y.py; for each exit variant the trade results (labels) are
recomputed and the model is retrained on them.

    ..\\.venv\\Scripts\\python exits_test.py      -> exits_results.json
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
from ml_features import FEATURES, MIN_CANDLES, add_market, breadth_from, btc_features, is_event, pair_features  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "exits_results.json"
INF = 1e9
# name: (long TP, long SL, short TP, short SL)
VARIANTS = {"E0 current (L 8/8, S 6/10)": (0.08, 0.08, 0.06, 0.10), "E1 4/4": (0.04, 0.04, 0.04, 0.04),
            "E2 6/6": (0.06, 0.06, 0.06, 0.06), "E3 8/8": (0.08, 0.08, 0.08, 0.08),
            "E4 6/10": (0.06, 0.10, 0.06, 0.10), "E5 10/15": (0.10, 0.15, 0.10, 0.15),
            "E6 4/8": (0.04, 0.08, 0.04, 0.08), "E7 12/12": (0.12, 0.12, 0.12, 0.12),
            "E8 24h only": (INF, INF, INF, INF)}
KEYS = {n: f"v{k}" for k, n in enumerate(VARIANTS)}
SPLIT = pd.Timestamp("2025-01-01", tz="UTC")


def first_hit(hit):
    return int(np.argmax(hit)) if hit.any() else None


def build():
    coins, closes = [], {}
    for p in M.PAIRS:
        coin = p.split("/")[0]
        try:
            d = pd.read_feather(Y.path(coin, "futures"), columns=["date", "close"])
        except FileNotFoundError:
            continue
        if len(d) >= MIN_CANDLES:
            coins.append((p, coin))
            closes[p] = d.set_index("date")["close"]
    breadth = breadth_from(closes)
    del closes
    btc = btc_features(pd.read_feather(Y.path("BTC", "futures")))
    rows = []
    for k, (pair, coin) in enumerate(coins):
        df = pd.read_feather(Y.path(coin, "futures"))
        try:
            fr = pd.read_feather(Y.path(coin, "funding_rate")).set_index("date")["funding_rate"]
        except FileNotFoundError:
            fr = pd.Series(dtype=float)
        cum = fr.groupby(level=0).sum().reindex(df["date"], fill_value=0.0).cumsum().to_numpy()
        f = pair_features(df)
        o, h, lo, c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
        n = len(c)
        dates = df["date"]
        for i in np.where(is_event(f))[0]:
            if i + M.HOLD >= n:
                continue
            entry, last = o[i + 1], i + M.HOLD
            hh, ll = h[i + 1:last + 1], lo[i + 1:last + 1]
            row = f.iloc[i].to_dict()
            row.update({"pair": pair, "entry_time": dates.iloc[i + 1], "exit_time": dates.iloc[last]})
            for name, (ltp, lsl, stp, ssl) in VARIANTS.items():
                key = KEYS[name]
                for side, tp, sl in (("long", ltp, lsl), ("short", stp, ssl)):
                    if side == "long":
                        a, b = first_hit(ll <= entry * (1 - sl)), first_hit(hh >= entry * (1 + tp))
                    else:
                        a, b = first_hit(hh >= entry * (1 + sl)), first_hit(ll <= entry * (1 - tp))
                    if a is not None and (b is None or a <= b):
                        r, j = -sl, i + 1 + a
                    elif b is not None:
                        r, j = tp, i + 1 + b
                    else:
                        mv = c[last] / entry - 1
                        r, j = (mv if side == "long" else -mv), last
                    fund = cum[j] - cum[i + 1]
                    row[f"{key}_{side}"] = r - M.FEE
                    row[f"{key}_{side}_all"] = r - M.FEE - Y.DELAY_COST + (-fund if side == "long" else fund)
            rows.append(row)
        if k % 100 == 0:
            print(f"  {k}/{len(coins)} coins, {len(rows)} events", flush=True)
    ev = add_market(pd.DataFrame(rows), btc, breadth)
    return ev.sort_values("date").reset_index(drop=True)


def tstat(x):
    x = np.asarray(x, float)
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) if len(x) > 2 and x.std(ddof=1) > 0 else 0.0


def period_stats(taken):
    if taken.empty:
        return {"trades": 0, "avg_pct": 0.0, "t": 0.0, "win_pct": None, "pnl_usdt": 0.0}
    x = taken["ret"]
    return {"trades": int(len(x)), "avg_pct": round(float(x.mean() * 100), 3), "t": round(tstat(x), 2),
            "win_pct": round(float((x > 0).mean() * 100), 1), "pnl_usdt": round(float(x.sum() * 100), 1),
            "shorts_pct": round(float((taken["side"] == "short").mean() * 100), 1)}


def main():
    t0 = time.time()
    print("building events with all exit variants ...", flush=True)
    ev = build()
    first = ev["exit_time"].iloc[min(1000, len(ev) - 1)]
    months = pd.date_range((first + pd.offsets.MonthBegin(1)).normalize(), ev["date"].max(), freq="MS")
    print(f"{len(ev)} events, test months {months[0]:%Y-%m} .. {months[-1]:%Y-%m} ({time.time() - t0:.0f}s)", flush=True)
    rep = {"prereg": "PREREG_exits.md", "events": int(len(ev)), "split": str(SPLIT.date()), "variants": {}}
    for name in VARIANTS:
        key = KEYS[name]
        picks = []
        for m in months:
            train = ev[ev["exit_time"] < m]
            test = ev[(ev["date"] >= m) & (ev["date"] < m + pd.offsets.MonthBegin(1))]
            if len(train) < 1000 or test.empty:
                continue
            t = test[["date", "pair", "entry_time", "exit_time", f"{key}_long_all", f"{key}_short_all"]].copy()
            t["pred_long"] = M.model().fit(train[FEATURES], train[f"{key}_long"]).predict(test[FEATURES])
            t["pred_short"] = M.model().fit(train[FEATURES], train[f"{key}_short"]).predict(test[FEATURES])
            picks.append(t)
        p = pd.concat(picks)
        p["long_ret"], p["short_ret"] = p[f"{key}_long_all"], p[f"{key}_short_all"]
        taken, _ = M.portfolio(M.choose(p, M.MIN_PRED))
        res = {"pick": period_stats(taken[taken["entry_time"] < SPLIT]),
               "holdout": period_stats(taken[taken["entry_time"] >= SPLIT])}
        rep["variants"][name] = res
        s = res["pick"]
        print(f"{name:28} pick: {s['trades']:>5} trades  won {s['win_pct']}%  avg {s['avg_pct']:+.3f}%  "
              f"t {s['t']:+.2f}  P&L {s['pnl_usdt']:+.1f}  shorts {s.get('shorts_pct')}%  ({time.time() - t0:.0f}s)",
              flush=True)

    elig = {n: v["pick"] for n, v in rep["variants"].items() if v["pick"]["pnl_usdt"] > 0 and v["pick"]["t"] >= 2.0}
    if not elig:
        rep["decision"] = {"eligible": [], "PASS": False, "why": "no variant had positive pick P&L with t >= 2.0"}
    else:
        best = max(elig, key=lambda n: elig[n]["pnl_usdt"])
        ho = rep["variants"][best]["holdout"]
        rep["decision"] = {"eligible": list(elig), "chosen": best, "holdout": ho,
                           "PASS": bool(ho["pnl_usdt"] > 0 and ho["avg_pct"] > 0)}
    print("\ndecision:", rep["decision"])
    OUT.write_text(json.dumps(rep, indent=1, default=str))
    print(f"saved {OUT.name}  done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
