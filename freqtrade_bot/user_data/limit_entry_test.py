"""Limit-order entry test for PumpML, as pre-registered in PREREG_limit_entry.md.

Same events, model, walk-forward and 0.5% bar as signal_bar_test.py; each chosen trade is replayed on 1-minute
Binance futures candles (cache shared with ml_delay.py), entering by market order or by limit order.

    ..\\.venv\\Scripts\\python limit_entry_test.py      -> limit_entry_results.json
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ml_5y as Y  # noqa: E402
import ml_delay as D  # noqa: E402
import ml_pumpdump as M  # noqa: E402
from ml_features import FEATURES  # noqa: E402
from trade_limit_test import period  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "limit_entry_results.json"
SPLIT = pd.Timestamp("2025-01-01", tz="UTC")
TAKER, MAKER, EXIT = 0.0015, 0.0002, 0.0015          # per side; TAKER/EXIT include slippage
START, END = 2, 10                                   # limit rests minutes 2..9; chase = market at minute 10
CURRENT = "MKT2 (current)"
VARIANTS = {CURRENT: None, "L0 skip": (0.0, False), "L0 chase": (0.0, True), "L0.2 skip": (0.002, False),
            "L0.2 chase": (0.002, True), "L0.5 skip": (0.005, False)}


def hold(a, side, j, entry, entry_cost):
    """Return (before funding) and exit minute for a trade filled in minute j at `entry`."""
    o, h, lo, c = a[:, 1], a[:, 2], a[:, 3], a[:, 4]
    last = min(j + M.HOLD * 60 - 1, len(c) - 1)
    cost = entry_cost + EXIT
    if side == "long":
        tp, sl = entry * (1 + M.LONG_TP), entry * (1 - M.LONG_SL)
        for k in range(j, last + 1):
            if lo[k] <= sl:
                return -M.LONG_SL - cost, k
            if h[k] >= tp:
                return M.LONG_TP - cost, k
        return c[last] / entry - 1 - cost, last
    tp, sl = entry * (1 - M.SHORT_TP), entry * (1 + M.SHORT_SL)
    for k in range(j, last + 1):
        if h[k] >= sl:
            return -M.SHORT_SL - cost, k
        if lo[k] <= tp:
            return M.SHORT_TP - cost, k
    return 1 - c[last] / entry - cost, last


def limit_fill(a, side, off):
    """(minute, price) of the first trade-through of a limit `off` better than the hour open, or None."""
    p0 = a[0, 1]
    if side == "long":
        px = p0 * (1 - off)
        hit = np.nonzero(a[START:END, 3] < px)[0]
    else:
        px = p0 * (1 + off)
        hit = np.nonzero(a[START:END, 2] > px)[0]
    return (START + int(hit[0]), px) if len(hit) else None


_fund = {}


def funding(pair, t0, t1, side):
    coin = pair.split("/")[0]
    if coin not in _fund:
        try:
            _fund[coin] = pd.read_feather(Y.path(coin, "funding_rate")).set_index("date")["funding_rate"]
        except FileNotFoundError:
            _fund[coin] = pd.Series(dtype=float)
    s = _fund[coin]
    r = float(s[(s.index > t0) & (s.index <= t1)].sum())
    return -r if side == "long" else r


def sides(t):
    if t.empty:
        return {}
    return {s: {"trades": int(len(g)), "avg_pct": round(float(g["ret"].mean() * 100), 3),
                "pnl_usdt": round(float(g["ret"].sum() * 100), 1)} for s, g in t.groupby(t["side"].copy())}


def main():
    t0 = time.time()
    D.CACHE.mkdir(exist_ok=True)
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
    chosen = M.choose(pd.concat(picks), M.MIN_PRED).reset_index(drop=True)
    print(f"{len(chosen)} chosen trades ({time.time() - t0:.0f}s); loading 1-minute candles ...", flush=True)

    rows = {n: [] for n in VARIANTS}
    info = {n: {"filled": 0, "missed": 0, "missed_mkt_ret": []} for n in VARIANTS}
    missing = 0
    for k, r in chosen.iterrows():
        sym = r["pair"].split("/")[0] + "USDT"
        cached = (D.CACHE / f"{sym}_{int(r['entry_time'].timestamp() * 1000)}.npy").exists()
        a = D.minutes(r["pair"], r["entry_time"])
        if not cached:
            time.sleep(0.25)                          # stay well under Binance's request limit
        if k % 250 == 0:
            print(f"  {k}/{len(chosen)}  missing {missing}  ({time.time() - t0:.0f}s)", flush=True)
        if a is None:
            missing += 1
            continue
        side = r["side"]
        base = {"date": r["date"], "pair": r["pair"], "side": side}
        mkt = hold(a, side, START, a[START, 1], TAKER)

        def add(name, j, ret_x):
            ret, x = ret_x
            t_in = r["entry_time"] + pd.Timedelta(minutes=j)
            t_out = r["entry_time"] + pd.Timedelta(minutes=x + 1)
            rows[name].append({**base, "entry_time": t_in, "exit_time": t_out,
                               "ret": ret + funding(r["pair"], t_in, t_out, side)})

        for name, v in VARIANTS.items():
            if v is None:
                add(name, START, mkt)
                continue
            off, chase = v
            f = limit_fill(a, side, off)
            if f:
                info[name]["filled"] += 1
                add(name, f[0], hold(a, side, f[0], f[1], MAKER))
            else:
                info[name]["missed"] += 1
                info[name]["missed_mkt_ret"].append(mkt[0])
                if chase:
                    add(name, END, hold(a, side, END, a[END, 1], TAKER))
    print(f"{len(chosen) - missing} trades with 1-minute data, {missing} without", flush=True)

    rep = {"prereg": "PREREG_limit_entry.md", "split": str(SPLIT.date()), "chosen": int(len(chosen)),
           "missing_1m": missing, "variants": {}}
    for name in VARIANTS:
        taken, pnl = M.portfolio(pd.DataFrame(rows[name]), 100.0, 5)
        a_t, b_t = taken[taken["entry_time"] < SPLIT], taken[taken["entry_time"] >= SPLIT]
        i = info[name]
        res = {"pick": period(a_t) | {"sides": sides(a_t)}, "holdout": period(b_t) | {"sides": sides(b_t)},
               "total_pnl_usdt": round(pnl, 1)}
        if VARIANTS[name] is not None:
            n = i["filled"] + i["missed"]
            res["fill_pct_all"] = round(i["filled"] / n * 100, 1) if n else 0.0
            res["missed_trades_avg_mkt_pct"] = round(float(np.mean(i["missed_mkt_ret"]) * 100), 3) if i["missed_mkt_ret"] else None
        rep["variants"][name] = res
        a, b = res["pick"], res["holdout"]
        print(f"{name:15} pick {a['trades']:>5} tr win {a.get('win_pct', 0):>4}% avg {a.get('avg_pct', 0):+.3f}% "
              f"P&L {a['pnl_usdt']:+8.1f} t {a['t']:+.2f} | holdout {b['trades']:>5} tr P&L {b['pnl_usdt']:+8.1f}"
              f"  fill {res.get('fill_pct_all', '-')}%", flush=True)

    base = rep["variants"][CURRENT]
    elig = {n: v for n, v in rep["variants"].items() if n != CURRENT and v["pick"]["pnl_usdt"] > 0
            and v["pick"]["t"] >= 2.0 and v["pick"]["pnl_usdt"] > base["pick"]["pnl_usdt"]}
    if not elig:
        rep["decision"] = {"eligible": [], "PASS": False,
                           "why": "no variant had positive pick P&L with t >= 2.0 that beat market entries"}
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
