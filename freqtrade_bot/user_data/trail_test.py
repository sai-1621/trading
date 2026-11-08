"""Trailing-stop distance test (PREREG_trail.md). Results: trail_results.json."""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ml_5y as Y  # noqa: E402
import ml_pumpdump as M  # noqa: E402
from ml_features import FEATURES, MIN_CANDLES, is_event, pair_features  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "trail_results.json"
SPLIT = pd.Timestamp("2025-01-01", tz="UTC")
COST = 0.0015 * 2 + Y.DELAY_COST
DISTS = [0.03, 0.05, 0.08, 0.10, 0.12, 0.15, 0.20, 0.25]
EXITS = ["FIXED"] + [f"T{int(d * 100)}-{h}h" for h in (24, 72) for d in DISTS]


def fixed(o, h, lo, c, j, short):
    e = o[j]
    tp, sl = (e * 0.94, e * 1.10) if short else (e * 1.08, e * 0.92)
    last = min(j + 23, len(c) - 1)
    for k in range(j, last + 1):
        if short:
            if h[k] >= sl:
                return -(max(o[k], sl) / e - 1), k
            if lo[k] <= tp:
                return -(min(o[k], tp) / e - 1) if o[k] < tp else 0.06, k
        else:
            if lo[k] <= sl:
                return min(o[k], sl) / e - 1, k
            if h[k] >= tp:
                return max(o[k], tp) / e - 1 if o[k] > tp else 0.08, k
    r = c[last] / e - 1
    return (-r if short else r), last


def trail(o, h, lo, c, j, short, d, hours):
    """Vectorised: the stop in hour k trails the best price of hours j..k-1 (and the entry)."""
    e = o[j]
    last = min(j + hours - 1, len(c) - 1)
    oo = o[j:last + 1]
    if short:
        pb = np.minimum.accumulate(np.r_[e, lo[j:last]])
        stop = pb * (1 + d)
        hit = (oo >= stop) | (h[j:last + 1] >= stop)
    else:
        pb = np.maximum.accumulate(np.r_[e, h[j:last]])
        stop = pb * (1 - d)
        hit = (oo <= stop) | (lo[j:last + 1] <= stop)
    if hit.any():
        q = int(np.argmax(hit))
        px = oo[q] if ((oo[q] >= stop[q]) if short else (oo[q] <= stop[q])) else stop[q]
        r = px / e - 1
        return (-r if short else r), j + q
    r = c[last] / e - 1
    return (-r if short else r), last


def tstat(x):
    x = np.asarray(x, float)
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) if len(x) > 2 and x.std(ddof=1) > 0 else 0.0


def main():
    t0 = time.time()
    rows = []
    for n, p in enumerate(M.PAIRS):
        coin = p.split("/")[0]
        try:
            df = pd.read_feather(Y.path(coin, "futures"))
        except FileNotFoundError:
            continue
        if len(df) < MIN_CANDLES:
            continue
        try:
            fr = pd.read_feather(Y.path(coin, "funding_rate")).set_index("date")["funding_rate"]
        except FileNotFoundError:
            fr = pd.Series(dtype=float)
        cum = fr.groupby(level=0).sum().reindex(df["date"], fill_value=0.0).cumsum().to_numpy()
        f = pair_features(df)
        o, h, lo, c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
        for i in np.where(is_event(f))[0]:
            j = i + 1
            if j + 72 >= len(c):
                continue
            rec = {"pair": p, "entry_time": df["date"].iloc[j], "jump": float(f["jump"].iloc[i])}
            for short in (False, True):
                s = "S" if short else "L"
                sign = 1 if short else -1                       # shorts receive positive funding, longs pay
                r, k = fixed(o, h, lo, c, j, short)
                rec[f"{s}|FIXED"] = r - COST + sign * (cum[k] - cum[j])
                for hours in (24, 72):
                    for d in DISTS:
                        r, k = trail(o, h, lo, c, j, short, d, hours)
                        rec[f"{s}|T{int(d * 100)}-{hours}h"] = r - COST + sign * (cum[k] - cum[j])
            rows.append(rec)
        if n % 100 == 0:
            print(f"  {n}/{len(M.PAIRS)} coins, {len(rows)} events ({time.time() - t0:.0f}s)", flush=True)
    ex = pd.DataFrame(rows)

    print("model predictions (monthly walk-forward) ...", flush=True)
    ev = Y.build_events()
    first = ev["exit_time"].iloc[min(1000, len(ev) - 1)]
    months = pd.date_range((first + pd.offsets.MonthBegin(1)).normalize(), ev["date"].max(), freq="MS")
    picks = []
    for m in months:
        train = ev[ev["exit_time"] < m]
        test = ev[(ev["date"] >= m) & (ev["date"] < m + pd.offsets.MonthBegin(1))]
        if len(train) < 1000 or test.empty:
            continue
        t = test[["pair", "entry_time"]].copy()
        t["pl"] = M.model().fit(train[FEATURES], train["long_ret"]).predict(test[FEATURES])
        t["ps"] = M.model().fit(train[FEATURES], train["short_ret"]).predict(test[FEATURES])
        picks.append(t)
    pr = pd.concat(picks)
    pr["side"] = np.where((pr["pl"] >= M.MIN_PRED) & (pr["pl"] >= pr["ps"]), "L",
                          np.where((pr["ps"] >= M.MIN_PRED) & (pr["ps"] > pr["pl"]), "S", ""))
    pr = pr[pr["side"] != ""]
    sel = ex.merge(pr[["pair", "entry_time", "side"]], on=["pair", "entry_time"])
    print(f"{len(ex)} events, {len(sel)} model trades ({time.time() - t0:.0f}s)", flush=True)

    groups = {"model picks": None, "long after pump": ("L", 1), "short after pump": ("S", 1),
              "long after dump": ("L", -1), "short after dump": ("S", -1)}
    rep = {"prereg": "PREREG_trail.md", "events": int(len(ex)), "model_trades": int(len(sel)), "groups": {}}
    for g, spec in groups.items():
        res = {}
        for e in EXITS:
            if spec is None:
                r = np.where(sel["side"] == "L", sel[f"L|{e}"], sel[f"S|{e}"])
                tm = sel["entry_time"]
            else:
                d = ex[np.sign(ex["jump"]) == spec[1]]
                r, tm = d[f"{spec[0]}|{e}"].to_numpy(), d["entry_time"]
            r = pd.Series(r, index=tm.index)
            a, b = r[tm < SPLIT], r[tm >= SPLIT]
            res[e] = {"pick_avg_pct": round(float(a.mean() * 100), 3), "pick_t": round(tstat(a), 2), "pick_n": int(len(a)),
                      "hold_avg_pct": round(float(b.mean() * 100), 3), "hold_t": round(tstat(b), 2), "hold_n": int(len(b))}
        rep["groups"][g] = res
        best = max(res, key=lambda e: res[e]["pick_avg_pct"])
        print(f"\n{g}: best in pick = {best}")
        for e in EXITS:
            v = res[e]
            print(f"   {e:9} pick {v['pick_avg_pct']:+.3f}% (t {v['pick_t']:+.2f})   holdout {v['hold_avg_pct']:+.3f}% (t {v['hold_t']:+.2f})")
    m = rep["groups"]["model picks"]
    chosen = max(m, key=lambda e: m[e]["pick_avg_pct"])
    win = chosen != "FIXED" and m[chosen]["hold_avg_pct"] > 0 and m[chosen]["hold_avg_pct"] > m["FIXED"]["hold_avg_pct"]
    rep["decision"] = {"chosen_in_pick": chosen, "chosen_holdout_avg_pct": m[chosen]["hold_avg_pct"],
                       "fixed_holdout_avg_pct": m["FIXED"]["hold_avg_pct"], "trail_wins": bool(win)}
    print("\ndecision:", rep["decision"])
    OUT.write_text(json.dumps(rep, indent=1, default=str))
    print(f"saved {OUT.name} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
