"""Trend v2 choppy-market fix, exactly as pre-registered in futures_lab/PREREG_v2.md.

    ..\\freqtrade_bot\\.venv\\Scripts\\python dl_fresh.py     (once: 2017-2021 data)
    ..\\freqtrade_bot\\.venv\\Scripts\\python search_v2.py

Writes results_v2.json. The fresh period (2018 - Oct 2021) is computed only for the chosen variant and for the
unfixed v1 rule (the pass rule compares their win rates).
"""
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import search_v1 as S

HERE = Path(__file__).resolve().parent
FRESH = HERE / "data_fresh"
FRESH_PERIOD = ("2018-01-01", "2021-10-30")
BASE_FUNDING_DAY = 0.0003          # 0.01% per 8 hours, before a coin's perpetual existed


def load_fresh():
    closes, highs, lows, execp, fund = {}, {}, {}, {}, {}
    for c in S.COINS:
        h = pd.read_feather(FRESH / f"{c}-1h.feather").set_index("date").sort_index()
        d = h.resample("1D").agg({"high": "max", "low": "min", "close": "last"})
        closes[c], highs[c], lows[c] = d["close"], d["high"], d["low"]
        ex = h["close"][h.index.hour == 0]
        execp[c] = ex.set_axis(ex.index.normalize())
        f = pd.read_feather(FRESH / f"{c}-funding.feather")
        if len(f):
            f = f.set_index("date")["funding_rate"]
            daily = f.groupby((f.index - pd.Timedelta(hours=1, seconds=1)).normalize()).sum()
            fund[c] = (daily, f.index.min().normalize())
        else:
            fund[c] = (pd.Series(dtype=float), None)
    C = pd.DataFrame(closes).dropna(how="all")
    idx = C.index
    H, L = pd.DataFrame(highs).reindex(idx), pd.DataFrame(lows).reindex(idx)
    X = pd.DataFrame(execp).reindex(idx)
    F = pd.DataFrame(index=idx, columns=S.COINS, dtype=float)
    for c, (daily, first) in fund.items():
        col = daily.reindex(idx).fillna(0.0)
        if first is None:
            col[:] = BASE_FUNDING_DAY
        else:
            col[idx < first] = BASE_FUNDING_DAY
        F[c] = col
    return C, H, L, X, F


def atr(C, H, L, n=20):
    pc = C.shift(1)
    tr = pd.concat([(H - L), (H - pc).abs(), (L - pc).abs()]).groupby(level=0).max()
    return tr.reindex(C.index).rolling(n).mean()


def trend_v2(C, H, L, market, chop, exit_rule):
    """Long-only 55-day breakout with the v2 filters/exits. Returns position fractions (1, 0.5, 0) per day."""
    hi55 = H.shift(1).rolling(55).max()
    lo20 = L.shift(1).rolling(20).min()
    A = atr(C, H, L)
    er = (C - C.shift(55)).abs() / C.diff().abs().rolling(55).sum()
    btc = C["BTC"]
    btc_ok = btc > btc.rolling(200).mean()
    pos = pd.DataFrame(0.0, index=C.index, columns=C.columns)
    st = {c: None for c in C.columns}
    for d in C.index:
        mkt = bool(btc_ok.loc[d]) if market == "btc200" else True
        for c in C.columns:
            cl = C.at[d, c]
            if pd.isna(cl):
                continue
            s = st[c]
            if s is not None:
                s["peak"] = max(s["peak"], cl)
                a = A.at[d, c]
                out = not mkt
                if exit_rule == "low20":
                    out = out or (not pd.isna(lo20.at[d, c]) and cl < lo20.at[d, c])
                else:
                    out = out or (not pd.isna(a) and cl < s["peak"] - 3 * a)
                    if exit_rule == "tp_trail" and s["frac"] == 1.0 and cl >= s["entry"] + 2 * s["atr"]:
                        s["frac"] = 0.5
                if out:
                    st[c] = None
                else:
                    pos.at[d, c] = s["frac"]
                continue
            h = hi55.at[d, c]
            if mkt and not pd.isna(h) and cl > h and not pd.isna(A.at[d, c]):
                if chop == "er30" and not (er.at[d, c] >= 0.30):
                    continue
                st[c] = {"entry": cl, "atr": A.at[d, c], "peak": cl, "frac": 1.0}
                pos.at[d, c] = 1.0
    return pos


def run(data, v, period):
    C, H, L, X, F = data
    raw = trend_v2(C, H, L, v["market"], v["chop"], v["exit"])
    W = S.size(raw, C)
    bt, held = S.backtest(W, X.ffill(), F)
    m = S.metrics(bt, period)
    n, w = S.trade_wins(held, X.ffill(), period)
    m.update({"trades": n, "win_pct": round(w / n * 100, 1) if n else None})
    return m, bt


def main():
    pick_data = S.load()
    variants = [{"market": a, "chop": b, "exit": e} for a in ("none", "btc200") for b in ("none", "er30")
                for e in ("low20", "trail3", "tp_trail")]
    res = []
    for v in variants:
        m, bt = run(pick_data, v, S.PICK)
        res.append({"v": v, "pick": m, "_bt": bt})
        print(f"{json.dumps(v):<58} pick: ret {m['total_return_pct']:>7}% sharpe {m['sharpe']} dd {m['max_dd_pct']}%"
              f" win {m['win_pct']}% trades {m['trades']} lev {m['avg_leverage']}", flush=True)
    elig = [r for r in res if (r["pick"]["sharpe"] or 0) >= 1.0 and r["pick"]["max_dd_pct"] >= -40]
    elig.sort(key=lambda r: (r["pick"]["win_pct"] or 0, r["pick"]["sharpe"]), reverse=True)
    rep = {"run_at": datetime.now().isoformat(), "variants": [{"v": r["v"], "pick": r["pick"]} for r in res],
           "eligible": len(elig)}
    if elig:
        ch = elig[0]
        fresh = load_fresh()
        f_new, bt_new = run(fresh, ch["v"], FRESH_PERIOD)
        f_old, _ = run(fresh, {"market": "none", "chop": "none", "exit": "low20"}, FRESH_PERIOD)
        passed = (f_new["total_return_pct"] > 0 and (f_new["sharpe"] or 0) >= 0.5 and f_new["max_dd_pct"] >= -40
                  and f_new["worst_day_pct"] > -30 and (f_new["win_pct"] or 0) > (f_old["win_pct"] or 0))
        seen = S.metrics(ch["_bt"], S.HOLD)
        yearly = {str(y): round(((1 + g).prod() - 1) * 100, 1)
                  for y, g in pd.concat([bt_new["net"].loc[:"2021-10-30"], ch["_bt"]["net"]]).groupby(
                      lambda t: t.year)}
        rep.update({"chosen": ch["v"], "chosen_pick": ch["pick"], "fresh_chosen": f_new, "fresh_unfixed_v1": f_old,
                    "fresh_btc_buy_hold_pct": S.buy_hold(fresh[3].ffill(), FRESH_PERIOD),
                    "seen_2025_26_reported_only": seen, "yearly_pct": yearly, "passed": bool(passed)})
    else:
        rep.update({"passed": False, "note": "nothing eligible on the pick period; fresh data not looked at"})
    (HERE / "results_v2.json").write_text(json.dumps(rep, indent=1, default=float))
    print(json.dumps({k: v for k, v in rep.items() if k != "variants"}, indent=1, default=float))


if __name__ == "__main__":
    main()
