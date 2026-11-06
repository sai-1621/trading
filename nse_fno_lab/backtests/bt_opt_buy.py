"""Backtest: strategy 1, intraday index option buying (leg 1 breakout + leg 2 flag), NIFTY / BANKNIFTY / SENSEX.

Data: Yahoo 5-minute bars (last ~60 sessions, cache grows each run) or longer broker history from
data/broker_historical.py when present. First 20 sessions warm up the indicators.
Option prices: Black-Scholes in trading time at IV = yesterday's India VIX x (index 20-day realised vol / NIFTY
20-day realised vol), both from completed days. No IV crush or skew. The OI filter is OFF (no free OI history);
live trading adds it, which can only remove trades. Entry at the next bar's open; adverse extreme first.

    .venv\\Scripts\\python backtests\\bt_opt_buy.py                 sensitivity table, 3 indices x 4 thresholds
    .venv\\Scripts\\python backtests\\bt_opt_buy.py NIFTY 2.0       one run with the trade list
    .venv\\Scripts\\python backtests\\bt_opt_buy.py climax          band / pullback-limit test (pre-registered)
"""
import json
import sys

import numpy as np
import pandas as pd

from common import RESULTS, print_summary, summary, t_stat, ticks

import config as C
import costs
import greeks
from data_yahoo import bars, vix_daily
from engines.base import State
from engines.opt_buy import StrategyEngine
from nse_calendar import years_to_expiry
from risk import DailyRisk

YAHOO = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "SENSEX": "^BSESN"}
THRESHOLDS = [1.5, 1.75, 2.0, 2.25]


def load_5m(sym: str) -> pd.DataFrame:
    try:
        from data.broker_historical import load_cached
        df = load_cached(sym)
        if df is not None and len(df) > 0:
            return df
    except ImportError:
        pass
    return bars(YAHOO[sym], "5m")


def iv_scale_by_day(sym: str) -> pd.Series:
    """India VIX is a NIFTY measure; scale it by the index's 20-day realised vol relative to NIFTY's (yesterday)."""
    if sym == "NIFTY":
        return pd.Series(dtype=float)
    a = bars(YAHOO[sym], "1d")["close"].pct_change().rolling(20).std()
    n = bars("^NSEI", "1d")["close"].pct_change().rolling(20).std()
    r = (a / n).shift(1).dropna()
    r.index = pd.to_datetime(r.index).date
    return r


def opt_px(S, opt, ts):
    T = years_to_expiry(ts.to_pydatetime(), opt["expiry"])
    return float(greeks.price(S, opt["strike"], T, C.RISK_FREE, opt["iv"], opt["cp"]))


def run(sym="NIFTY", gt_min=2.0, flag_gt_min=None, flag=True, extra_slip_pts=0.0, iv_mult=1.0, gt_mode="min"):
    df = load_5m(sym)
    daily = bars(YAHOO[sym], "1d")
    vix = vix_daily() * iv_mult
    cfg = dict(C.S1, symbol=sym, gamma_theta_min=gt_min, flag_enabled=flag, gt_mode=gt_mode)
    if flag_gt_min is not None:
        cfg["flag_gamma_theta_min"] = flag_gt_min
    e = StrategyEngine(cfg, require_oi=False)
    scale = iv_scale_by_day(sym)
    x = e.calculate_indicators(df, daily=daily, vix=vix)
    idx = list(x.index)
    days = sorted(set(x.index.date))
    test_days = set(days[20:])
    trades, pos, risk, cur, pending = [], None, None, None, None
    for i, ts in enumerate(idx):
        d = ts.date()
        if d not in test_days:
            continue
        if d != cur:
            cur, risk, pos, pending = d, DailyRisk(), None, None
            e.new_day()
            e.iv_scale = float(scale.get(d, 1.0)) if len(scale) else 1.0
        b = x.iloc[i]
        if pos is not None:
            o, sg = pos["opt"], pos["sig"]
            long = sg.side == "long"
            for tt, p, close in ticks(b, ts, long):
                ltp = opt_px(p, o, tt)
                act = e.manage_open_position({"ts": tt, "spot": p, "option_ltp": ltp, "bar_close": close,
                                              "atr": b["atr_slow"]})
                if act["action"] != "exit":
                    continue
                why = act["reason"]
                if why == "stop hit":
                    lvl = act["stop"]
                    S = b["open"] if (long and b["open"] < lvl) or (not long and b["open"] > lvl) else lvl
                    ex = opt_px(S, o, tt)
                elif why == "target hit":
                    S = b["open"] if (long and b["open"] > sg.target) or (not long and b["open"] < sg.target) else sg.target
                    ex = opt_px(S, o, tt)
                elif why.startswith("premium stop"):
                    S, ex = p, min(sg.meta["premium_stop"], opt_px(b["open"], o, tt))
                else:
                    S, ex = p, ltp
                q = pos["qty"]
                ch = costs.option_round_trip(pos["entry"], ex, q, exchange=C.EXCHANGE_OF[sym]).total
                ch += 2 * extra_slip_pts * q
                pnl = (ex - pos["entry"]) * q - ch
                risk.on_close(pnl)
                trades.append({"date": d, "symbol": sym, "kind": sg.meta["kind"], "side": sg.side,
                               "entry_time": pos["t"], "exit_time": tt, "strike": o["strike"],
                               "expiry": o["expiry"].isoformat(), "prem_in": round(pos["entry"], 2),
                               "prem_out": round(ex, 2), "qty": q, "charges": round(ch, 2), "pnl": round(pnl, 2),
                               "reason": why, "gamma_theta": round(sg.meta["gamma_theta"], 2)})
                pos = None
                break
        # pending pullback limit (climax bar): fill inside this bar or expire
        if pending is not None and pos is None:
            sg = pending["sig"]
            long = sg.side == "long"
            lim = sg.meta["limit"]
            hit = b["low"] <= lim if long else b["high"] >= lim
            if hit:
                fill = min(lim, b["open"]) if long else max(lim, b["open"])
                tt = ts + pd.Timedelta(minutes=2.5)
                o = sg.instrument
                entry = opt_px(fill, o, tt)
                e.fill_limit(sg, fill, entry)
                lots = e.compute_position_size(C.CAPITAL, sg.risk_per_unit, premium=entry, room=risk.room())
                ok, _ = risk.can_enter(sg.risk_per_unit * lots * e.lot)
                pending = None
                if lots > 0 and ok:
                    e.on_entry(sg)
                    pos = {"sig": sg, "opt": o, "entry": entry, "qty": lots * e.lot, "t": tt}
                    # worst case inside the fill bar: the stop counts if this bar also reached it
                    if (long and b["low"] <= sg.stop) or (not long and b["high"] >= sg.stop):
                        ex = opt_px(sg.stop, o, ts + pd.Timedelta(minutes=5))
                        q = pos["qty"]
                        ch = costs.option_round_trip(entry, ex, q, exchange=C.EXCHANGE_OF[sym]).total + 2 * extra_slip_pts * q
                        pnl = (ex - entry) * q - ch
                        risk.on_close(pnl)
                        e.manage_open_position({"ts": ts + pd.Timedelta(minutes=5), "spot": sg.stop, "bar_close": True,
                                                "atr": b["atr_slow"]})
                        trades.append({"date": d, "symbol": sym, "kind": sg.meta["kind"], "side": sg.side,
                                       "entry_time": tt, "exit_time": ts + pd.Timedelta(minutes=5), "strike": o["strike"],
                                       "expiry": o["expiry"].isoformat(), "prem_in": round(entry, 2), "prem_out": round(ex, 2),
                                       "qty": q, "charges": round(ch, 2), "pnl": round(pnl, 2), "reason": "stop hit (fill bar)",
                                       "gamma_theta": round(sg.meta["gamma_theta"], 2)})
                        pos = None
            else:
                pending["left"] -= 1
                if pending["left"] <= 0:
                    pending = None
        # every bar goes through the engine (the leg-2 flag tracks the whole day)
        sig = e.generate_signal(b)
        if sig is not None and sig.meta.get("entry") == "limit":
            if pending is None and pos is None:
                pending = {"sig": sig, "left": sig.meta["valid_bars"]}
            continue
        if sig is None or pending is not None or i + 1 >= len(idx) or idx[i + 1].date() != d:
            continue
        t_in = idx[i + 1]
        o = sig.instrument
        entry = opt_px(x.iloc[i + 1]["open"], o, t_in)
        lots = e.compute_position_size(C.CAPITAL, sig.risk_per_unit, premium=entry, room=risk.room())
        ok, _ = risk.can_enter(sig.risk_per_unit * lots * e.lot)
        if lots <= 0 or not ok:
            if not ok:
                e.state = State.DONE_FOR_DAY
            continue
        e.on_entry(sig)
        pos = {"sig": sig, "opt": o, "entry": entry, "qty": lots * e.lot, "t": t_in}
    tr = pd.DataFrame(trades)
    s = summary(tr, C.CAPITAL, len(test_days))
    if not tr.empty:
        s["t_stat_per_trade"] = round(t_stat(tr["pnl"]), 2)
        s["by_kind"] = tr.groupby("kind")["pnl"].agg(["size", "sum", lambda v: (v > 0).mean() * 100]) \
            .rename(columns={"<lambda_0>": "win_pct"}).round(1).to_dict("index")
    return tr, s


CLIMAX_MODES = [("min g/t >= 1.75", "min", 1.75), ("min g/t >= 2.0 (current)", "min", 2.0),
                ("band 1.50-1.90", "band", 2.0), ("band + pullback limit above 1.90", "band_pullback", 2.0)]


def climax_report():
    """Pre-registered comparison (rules fixed on 1 Nov 2026 before any 3-year data): per index and pooled."""
    rows, pooled = [], {}
    for sym in YAHOO:
        df = load_5m(sym)
        src = "broker" if len(set(df.index.date)) > 100 else "yahoo"
        for name, mode, gt in CLIMAX_MODES:
            tr, s = run(sym, gt, gt_mode=mode)
            pooled.setdefault(name, []).append(tr)
            rows.append({"index": sym, "data": src, "sessions": s["sessions"], "mode": name, "trades": s.get("trades", 0),
                         "win %": s.get("win_pct"), "profit factor": s.get("profit_factor"), "total Rs": s.get("total_pnl"),
                         "avg Rs": s.get("avg_pnl"), "t": s.get("t_stat_per_trade")})
    t = pd.DataFrame(rows)
    pd.set_option("display.width", 250)
    print("\nStrategy 1 climax test (charges included; OI filter off)\n")
    print(t.to_string(index=False))
    print("\nPooled over the 3 indices:")
    out = []
    for name, parts in pooled.items():
        g = pd.concat([x for x in parts if not x.empty]) if any(not x.empty for x in parts) else pd.DataFrame()
        if g.empty:
            print(f"  {name:<34} no trades")
            continue
        pf = g.loc[g.pnl > 0, "pnl"].sum() / -g.loc[g.pnl < 0, "pnl"].sum() if (g.pnl < 0).any() else float("inf")
        out.append({"mode": name, "trades": len(g), "win_pct": round(100 * (g.pnl > 0).mean(), 1),
                    "profit_factor": round(pf, 2), "total": round(g.pnl.sum()), "t": round(t_stat(g.pnl), 2)})
        print(f"  {name:<34} {len(g):>4} trades  win {100 * (g.pnl > 0).mean():.0f}%  PF {pf:.2f}  "
              f"total Rs {g.pnl.sum():>10,.0f}  t {t_stat(g.pnl):.2f}")
    t.to_csv(RESULTS / "s1_climax.csv", index=False)
    (RESULTS / "s1_climax_pooled.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1].lower() == "climax":
        climax_report()
        sys.exit()
    if len(sys.argv) >= 3:
        tr, s = run(sys.argv[1].upper(), float(sys.argv[2]))
        print_summary(f"{sys.argv[1]} gamma/theta >= {sys.argv[2]}", s)
        if not tr.empty:
            print(tr[["date", "kind", "side", "entry_time", "strike", "prem_in", "prem_out", "qty", "pnl", "reason"]]
                  .to_string(index=False))
        sys.exit()
    rows, all_trades = [], []
    for sym in YAHOO:
        for gt in THRESHOLDS:
            tr, s = run(sym, gt)
            if not tr.empty:
                all_trades.append(tr.assign(threshold=gt))
            bk = s.get("by_kind", {})
            rows.append({"index": sym, "gamma/theta >=": gt, "sessions": s["sessions"], "trades": s.get("trades", 0),
                         "win %": s.get("win_pct"), "profit factor": s.get("profit_factor"),
                         "total Rs": s.get("total_pnl"), "avg Rs": s.get("avg_pnl"), "max DD": s.get("max_drawdown"),
                         "leg1 n": bk.get("leg1", {}).get("size", 0), "leg1 Rs": bk.get("leg1", {}).get("sum", 0),
                         "flag n": bk.get("leg2_flag", {}).get("size", 0),
                         "flag Rs": bk.get("leg2_flag", {}).get("sum", 0), "t": s.get("t_stat_per_trade")})
    t = pd.DataFrame(rows)
    pd.set_option("display.width", 250)
    print("\nStrategy 1 sensitivity (flag leg gamma/theta >= 1.6, OI filter off, charges included)\n")
    print(t.to_string(index=False))
    pooled = pd.concat(all_trades) if all_trades else pd.DataFrame()
    if not pooled.empty:
        print("\nPooled over the 3 indices:")
        for gt, g in pooled.groupby("threshold"):
            pf = g.loc[g.pnl > 0, "pnl"].sum() / -g.loc[g.pnl < 0, "pnl"].sum() if (g.pnl < 0).any() else np.inf
            print(f"  >= {gt}: {len(g)} trades, win {100 * (g.pnl > 0).mean():.0f}%, profit factor {pf:.2f}, "
                  f"total Rs {g.pnl.sum():,.0f}, t {t_stat(g.pnl):.2f}")
        pooled.to_csv(RESULTS / "s1_sensitivity_trades.csv", index=False)
    t.to_csv(RESULTS / "s1_sensitivity.csv", index=False)
    (RESULTS / "s1_sensitivity.json").write_text(json.dumps(rows, indent=1, default=str))
