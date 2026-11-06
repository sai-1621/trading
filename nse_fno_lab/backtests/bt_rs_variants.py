"""Backtest: strategy 3A swing breakout on NSE F&O stocks, daily bars, 2017-2026.

Universe: top 50 of data_yahoo.FNO_STOCKS by 20-day traded value (known the day before). The scan uses the day's
bar as of 15:15: volume counted at 90% of the full day; entry at the close with 0.1% slippage; delivery charges
both ways. Up to 3 new trades a day, 8 open, total value <= capital (no leverage).
Regime: NIFTY close > its 50-day SMA on the scan day.
Variants (fixed before running): the old 5-day cap, the cap + regime, no cap with an EMA(20) exit, no cap with a
10-day-low exit, no cap without the regime. Placebo: random universe stocks on the same entry days, same exits.
Biases in the strategy's favour: today's F&O list (survivors) and entry at the exact close.
Variant 3B (VWAP pullback) was dropped on 1 Nov 2026.

    .venv\\Scripts\\python backtests\\bt_rs_variants.py
"""
import json

import numpy as np
import pandas as pd

from common import RESULTS, summary, t_stat

import config as C
import costs
from data_yahoo import FNO_STOCKS, bars
from engines.base import Signal
from engines.rs_variants import SwingBreakoutEngine

VOL_AT_SCAN = 0.9
LIQUID_RATE = 0.06          # idle cash parked in LIQUIDBEES, 6% a year (simple)
START = "2017-01-01"
VARIANTS = {
    "A  5-day cap, EMA20, no regime (old)": {"hold_days": 5, "exit_rule": "ema20", "regime": False},
    "B  5-day cap, EMA20, regime": {"hold_days": 5, "exit_rule": "ema20", "regime": True},
    "C  no cap, EMA20 exit, regime": {"hold_days": None, "exit_rule": "ema20", "regime": True},
    "D  no cap, 10-day-low exit, regime": {"hold_days": None, "exit_rule": "low10", "regime": True},
    "E  no cap, EMA20 exit, no regime": {"hold_days": None, "exit_rule": "ema20", "regime": False},
}


def daily_frames():
    out = {}
    for s in FNO_STOCKS:
        try:
            out[s] = bars(s, "1d")
        except Exception:  # noqa: BLE001
            pass
    return out


def nifty_regime() -> pd.Series:
    n = bars("^NSEI", "1d")["close"]
    return (n > n.rolling(50).mean()).astype(float)


def run_3a(frames, cfg: dict, placebo_seed=None, start=START):
    e = SwingBreakoutEngine(cfg)
    reg = nifty_regime()
    ind = {}
    for s, f in frames.items():
        x = e.calculate_indicators(f)
        x["vol_ratio"] = x["vol_ratio"] * VOL_AT_SCAN
        x["symbol"] = s
        x["regime_ok"] = reg.reindex(x.index).fillna(0.0)
        ind[s] = x
    dates = sorted(set().union(*[set(x.index) for x in ind.values()]))
    dates = [d for d in dates if d >= pd.Timestamp(start)]
    rng = np.random.default_rng(placebo_seed) if placebo_seed is not None else None
    open_pos, trades, deployed = {}, [], []
    for d in dates:
        for s, p in list(open_pos.items()):
            x = ind[s]
            if d not in x.index:
                continue
            p["days"] += 1
            e.trades = {s: p}
            act = e.manage_open_position({"symbol": s, "bar": x.loc[d], "days_held": p["days"]})
            if act["action"] == "exit":
                ch = costs.equity_round_trip(p["entry"], act["price"], p["qty"], delivery=True).total
                pnl = (act["price"] - p["entry"]) * p["qty"] - ch
                trades.append({"date": p["date"], "exit_date": d, "symbol": s, "entry": p["entry"], "exit": act["price"],
                               "qty": p["qty"], "days": p["days"], "charges": round(ch, 2), "pnl": round(pnl, 2),
                               "risk_rs": round(p["risk"] * p["qty"], 2), "reason": act["reason"]})
                open_pos.pop(s)
        rows = {s: x.loc[d] for s, x in ind.items() if d in x.index and not pd.isna(x.loc[d, "traded_value20"])}
        deployed.append(sum(p["entry"] * p["qty"] for p in open_pos.values()))
        if len(rows) < 10:
            continue
        uni = sorted(rows, key=lambda s: rows[s]["traded_value20"], reverse=True)[:50]
        sigs = [sg for sg in (e.generate_signal(rows[s]) for s in uni if s not in open_pos) if sg]
        sigs.sort(key=lambda sg: -sg.meta["vol_ratio"])
        sigs = sigs[:e.cfg["picks"]]
        if rng is not None and sigs:
            n_pick = len(sigs)                              # same number of entries as the real scan
            pool = [s for s in uni if s not in open_pos and not pd.isna(rows[s]["atr"])]
            sigs = []
            for s in rng.choice(pool, size=min(n_pick, len(pool)), replace=False) if pool else []:
                b = rows[s]
                stop = b["close"] - e.cfg["stop_atr"] * b["atr"]
                q = e.compute_position_size(C.CAPITAL, b["close"] - stop, price=b["close"])
                if q > 0:
                    sigs.append(Signal("long", d, float(b["close"]), float(stop), float("inf"), "placebo",
                                       instrument={"symbol": s}, risk_per_unit=float(b["close"] - stop), qty=q))
        used = sum(p["entry"] * p["qty"] for p in open_pos.values())
        for sg in sigs:
            s = sg.instrument["symbol"]
            if len(open_pos) >= e.cfg["max_open"] or used + sg.underlying * sg.qty > C.CAPITAL:
                break
            open_pos[s] = {"date": d, "entry": sg.underlying, "qty": sg.qty, "stop": sg.stop, "days": 0,
                           "risk": sg.risk_per_unit}
            used += sg.underlying * sg.qty
    tr = pd.DataFrame(trades)
    years = (dates[-1] - dates[0]).days / 365.25
    s = summary(tr, C.CAPITAL, len(dates))
    if not tr.empty:
        R = tr["pnl"] / tr["risk_rs"]
        s.update({"t_stat_per_trade": round(t_stat(tr["pnl"]), 2), "avg_R": round(float(R.mean()), 3),
                  "gross_avg_R": round(float(((tr["pnl"] + tr["charges"]) / tr["risk_rs"]).mean()), 3),
                  "cost_per_R": round(float((tr["charges"] / tr["risk_rs"]).mean()), 3),
                  "avg_days": round(float(tr["days"].mean()), 1), "max_days": int(tr["days"].max()),
                  "share_R_ge_3": round(float((R >= 3).mean() * 100), 1),
                  "profit_from_R_ge_3": round(float(tr.loc[R >= 3, "pnl"].sum()), 0),
                  "simple_return_pct_per_year": round(float(tr["pnl"].sum() / C.CAPITAL * 100 / years), 2),
                  "avg_capital_used_pct": round(float(np.mean(deployed) / C.CAPITAL * 100), 1)})
        idle = sum(max(C.CAPITAL - x, 0.0) for x in deployed) * LIQUID_RATE / 252     # idle cash in LIQUIDBEES
        s["liquidbees_interest"] = round(idle, 0)
        s["cash_plus_pct_per_year"] = round(float((tr["pnl"].sum() + idle) / C.CAPITAL * 100 / years), 2)
        tr["half"] = np.where(pd.to_datetime(tr["date"]) < pd.Timestamp("2022-01-01"), "2017-2021", "2022-2026")
        s["by_half"] = tr.groupby("half").apply(lambda g: {"trades": len(g), "pnl": round(g.pnl.sum()),
                                                           "avg_R": round((g.pnl / g.risk_rs).mean(), 3)},
                                                include_groups=False).to_dict()
        s["by_year"] = tr.assign(y=pd.to_datetime(tr["date"]).dt.year).groupby("y")["pnl"].sum().round(0).to_dict()
    return tr, s


def placebo(frames, cfg, n=5):
    res = [run_3a(frames, cfg, placebo_seed=k)[1] for k in range(n)]
    return {k: round(float(np.mean([r.get(k) or 0 for r in res])), 3) for k in ("trades", "avg_R", "total_pnl", "win_pct")}


if __name__ == "__main__":
    fr = daily_frames()
    rows, out = [], {}
    for name, cfg in VARIANTS.items():
        tr, s = run_3a(fr, cfg)
        pl = placebo(fr, cfg) if name[0] in "ACD" else {}
        out[name] = {**s, "placebo": pl}
        if not tr.empty:
            tr.to_csv(RESULTS / f"s3a_{name[0]}_trades.csv", index=False)
        rows.append({"variant": name, "trades": s.get("trades"), "win %": s.get("win_pct"), "PF": s.get("profit_factor"),
                     "avg R": s.get("avg_R"), "gross R": s.get("gross_avg_R"), "cost/R": s.get("cost_per_R"),
                     "total Rs": s.get("total_pnl"), "t": s.get("t_stat_per_trade"), "avg days": s.get("avg_days"),
                     "% trades >=3R": s.get("share_R_ge_3"), "%/yr on 3L": s.get("simple_return_pct_per_year"),
                     "capital used %": s.get("avg_capital_used_pct"), "max DD": s.get("max_drawdown"),
                     "placebo avg R": pl.get("avg_R")})
    t = pd.DataFrame(rows)
    pd.set_option("display.width", 260)
    pd.set_option("display.max_columns", 30)
    print(t.to_string(index=False))
    print("\nBy half (trades / Rs / avg R):")
    for name in VARIANTS:
        bh = out[name].get("by_half", {})
        print(f"  {name:<40} " + "   ".join(f"{k}: {v['trades']} / {v['pnl']:+,} / {v['avg_R']:+.3f}" for k, v in bh.items()))
    n = bars("^NSEI", "1d")["close"]
    n = n[n.index >= pd.Timestamp(START)]
    yrs = (n.index[-1] - n.index[0]).days / 365.25
    bh_total = (n.iloc[-1] / n.iloc[0] - 1) * 100
    cagr = ((n.iloc[-1] / n.iloc[0]) ** (1 / yrs) - 1) * 100
    print(f"\nNIFTY buy-and-hold {START[:4]}-2026: +{bh_total:.0f}% total, {cagr:.1f}% a year (price only, no dividends)")
    out["NIFTY"] = {"total_pct": round(bh_total, 1), "cagr_pct": round(cagr, 2)}
    t.to_csv(RESULTS / "s3a_variants.csv", index=False)
    (RESULTS / "s3a_variants.json").write_text(json.dumps(out, indent=1, default=str))
