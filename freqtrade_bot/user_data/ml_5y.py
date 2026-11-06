"""PumpML walk-forward over ~5 years (Nov 2021 - Oct 2026), same events, features, model and exits as
ml_pumpdump.py, on the longer history from dl_5y.py (user_data/data_5y).

Differences from ml_pumpdump.py:
  * loads one coin at a time (low memory),
  * reports three cost levels: ideal (enter exactly at the hour, as the original test), + funding payments,
    + funding + 0.15% entry delay cost (the average extra cost of entering 2 minutes late, measured on 1-minute
    prices by ml_delay.py).
Walk-forward: each month is predicted by models trained only on trades that had finished before it started;
the first month is the first one with at least 1,000 earlier events.
Coins that were delisted before today are not in the list (survivorship bias), see the README.

    ..\\.venv\\Scripts\\python ml_5y.py      -> ml_5y_results.json
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ml_pumpdump as M  # noqa: E402
from ml_features import FEATURES, MIN_CANDLES, add_market, breadth_from, btc_features, is_event, pair_features  # noqa: E402

HERE = Path(__file__).resolve().parent
D5 = HERE / "data_5y"
OUT = HERE / "ml_5y_results.json"
DELAY_COST = 0.0015


def path(coin, kind):
    p = D5 / f"{coin}_USDT_USDT-1h-{kind}.feather"
    return p if p.exists() else M.DATA / f"{coin}_USDT_USDT-1h-{kind}.feather"


def simulate(o, h, lo, c, i):
    """Like ml_pumpdump.simulate, plus the exit row of each side (for funding)."""
    n = len(c)
    if i + 1 >= n:
        return None
    entry, last = o[i + 1], min(i + M.HOLD, n - 1)
    if last - i < M.HOLD:
        return None
    lt, ls = entry * (1 + M.LONG_TP), entry * (1 - M.LONG_SL)
    st, ss = entry * (1 - M.SHORT_TP), entry * (1 + M.SHORT_SL)
    lr = sr = None
    lj = sj = last
    for j in range(i + 1, last + 1):
        if lr is None:
            if lo[j] <= ls:
                lr, lj = -M.LONG_SL, j
            elif h[j] >= lt:
                lr, lj = M.LONG_TP, j
        if sr is None:
            if h[j] >= ss:
                sr, sj = -M.SHORT_SL, j
            elif lo[j] <= st:
                sr, sj = M.SHORT_TP, j
        if lr is not None and sr is not None:
            break
    if lr is None:
        lr = c[last] / entry - 1
    if sr is None:
        sr = 1 - c[last] / entry
    return lr - M.FEE, sr - M.FEE, last, lj, sj


def build_events():
    coins = []
    closes = {}
    for p in M.PAIRS:
        coin = p.split("/")[0]
        try:
            d = pd.read_feather(path(coin, "futures"), columns=["date", "close"])
        except FileNotFoundError:
            continue
        if len(d) >= MIN_CANDLES:
            coins.append((p, coin))
            closes[p] = d.set_index("date")["close"]
    breadth = breadth_from(closes)
    del closes
    btc = btc_features(pd.read_feather(path("BTC", "futures")))
    rows = []
    for k, (pair, coin) in enumerate(coins):
        df = pd.read_feather(path(coin, "futures"))
        try:
            fr = pd.read_feather(path(coin, "funding_rate")).set_index("date")["funding_rate"]
        except FileNotFoundError:
            fr = pd.Series(dtype=float)
        # funding paid at the start of each hour, as a cumulative sum aligned to the candles
        cum = fr.groupby(level=0).sum().reindex(df["date"], fill_value=0.0).cumsum().to_numpy()
        f = pair_features(df)
        o, h, lo, c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
        dates = df["date"]
        for i in np.where(is_event(f))[0]:
            s = simulate(o, h, lo, c, i)
            if s is None:
                continue
            lr, sr, last, lj, sj = s
            # funding times strictly after entry (row i+1 start) up to and including the exit row
            fl = cum[lj] - cum[i + 1]
            fs = cum[sj] - cum[i + 1]
            row = f.iloc[i].to_dict()
            row.update({"pair": pair, "long_ret": lr, "short_ret": sr, "long_fund": -fl, "short_fund": fs,
                        "entry_time": dates.iloc[i + 1], "exit_time": dates.iloc[last]})
            rows.append(row)
        if k % 100 == 0:
            print(f"  {k}/{len(coins)} coins, {len(rows)} events", flush=True)
    ev = add_market(pd.DataFrame(rows), btc, breadth)
    return ev.sort_values("date").reset_index(drop=True)


def stats(taken, pnl):
    if taken.empty:
        return {"trades": 0}
    y = taken.assign(y=pd.to_datetime(taken["date"]).dt.year).groupby("y")["ret"].agg(["size", "mean", "sum"])
    bm = taken.assign(m=pd.to_datetime(taken["date"]).dt.strftime("%Y-%m")).groupby("m")["ret"].sum()
    return {"trades": int(len(taken)), "win_pct": round(float((taken["ret"] > 0).mean() * 100), 1),
            "avg_net_pct": round(float(taken["ret"].mean() * 100), 3), "pnl_usdt": round(pnl, 1),
            "months_positive": f"{int((bm > 0).sum())}/{len(bm)}",
            "longs": int((taken["side"] == "long").sum()), "shorts": int((taken["side"] == "short").sum()),
            "by_year": {int(k): {"trades": int(v["size"]), "avg_net_pct": round(v["mean"] * 100, 3),
                                 "pnl_usdt": round(v["sum"] * 100, 1)} for k, v in y.iterrows()}}


def main():
    t0 = time.time()
    print("building events ...", flush=True)
    ev = build_events()
    print(f"{len(ev)} events from {ev['date'].min()} to {ev['date'].max()} ({time.time() - t0:.0f}s)", flush=True)
    first = ev["exit_time"].iloc[min(1000, len(ev) - 1)]
    start = (first + pd.offsets.MonthBegin(1)).normalize()
    months = pd.date_range(start, ev["date"].max(), freq="MS")
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
        print(f"  {m:%Y-%m}: trained on {len(train)}, {len(test)} events", flush=True)
    p = pd.concat(picks)
    p["long_all"] = p["long_ret"] + p["long_fund"] - DELAY_COST
    p["short_all"] = p["short_ret"] + p["short_fund"] - DELAY_COST

    def side_ret(df, kind):
        if kind == "ideal":
            return np.where(df["side"] == "long", df["long_ret"], df["short_ret"])
        if kind == "fund":
            return np.where(df["side"] == "long", df["long_ret"] + df["long_fund"], df["short_ret"] + df["short_fund"])
        return np.where(df["side"] == "long", df["long_all"], df["short_all"])

    chosen = M.choose(p, M.MIN_PRED)
    variants = {"always long (no model), ideal": p.assign(side="long"),
                "always short (no model), ideal": p.assign(side="short"),
                "model, ideal (like the original test)": chosen,
                "model, + funding": chosen,
                "model, + funding + 2-min delay (realistic)": chosen}
    kinds = ["ideal", "ideal", "ideal", "fund", "all"]
    rep = {"events": int(len(ev)), "test_months": [str(months[0].date()), str(months[-1].date())],
           "delay_cost": DELAY_COST, "runs": {}}
    print(f"\nunseen months {months[0]:%Y-%m} .. {months[-1]:%Y-%m}")
    print(f"{'':44} {'trades':>6} {'won%':>6} {'avg %':>7} {'P&L':>8} {'months+':>8}   per year P&L (USDT on 1,000)")
    for (name, df), kind in zip(variants.items(), kinds):
        df = df.assign(ret=side_ret(df, kind))
        taken, pnl = M.portfolio(df)
        s = rep["runs"][name] = stats(taken, pnl)
        yrs = "  ".join(f"{y}:{v['pnl_usdt']:+.0f}" for y, v in s["by_year"].items())
        print(f"{name:44} {s['trades']:>6} {s['win_pct']:>6} {s['avg_net_pct']:>+7.3f} {s['pnl_usdt']:>+8.1f} "
              f"{s['months_positive']:>8}   {yrs}")
    OUT.write_text(json.dumps(rep, indent=1, default=str))
    print(f"\nsaved {OUT.name}  done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
