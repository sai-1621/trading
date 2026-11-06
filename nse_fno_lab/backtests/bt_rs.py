"""Backtest: strategy 3, F&O stocks relative strength + opening volume breakout, intraday, last ~60 sessions.

Universe: the 50 most traded (by rupee value) of data_yahoo.FNO_STOCKS over the first 10 sessions; those sessions
are not traded (no look-ahead in the universe). 09:30 scan -> picks -> opening-range breakout entries at the
next bar's open. Intrabar: adverse extreme first. Charges: NSE intraday equity + 0.1% slippage per side.
Delivery-volume filter: off (needs NSE bhavcopy history). Swing (3-day) mode: not backtested here.
Placebo: the same trade rules on randomly chosen stocks / sides, to see whether the ranking adds anything.

    .venv\\Scripts\\python backtests\\bt_rs.py
"""
import json

import numpy as np
import pandas as pd

from common import RESULTS, print_summary, summary, t_stat, ticks

import config as C
import costs
from data_yahoo import FNO_STOCKS, bars, vix_daily
from engines.base import State
from engines.rs_breakout import StrategyEngine
from indicators import session_vwap
from risk import DailyRisk


def load():
    frames = {}
    for s in FNO_STOCKS:
        try:
            frames[s] = bars(s, "5m")
        except Exception:  # noqa: BLE001 - renamed / delisted symbols
            pass
    return frames


def run(frames, cfg_over=None, slippage_pct=None, placebo_seed=None):
    cfg = dict(C.S3, **(cfg_over or {}))
    e = StrategyEngine(cfg)
    nifty = bars("^NSEI", "5m")
    nifty["vwap"] = session_vwap(nifty)
    vix = vix_daily()
    days = sorted(set(nifty.index.date))
    rank_days, test_days = days[:10], days[10:]
    tv = {s: float((f["close"] * f["volume"])[np.isin(f.index.date, rank_days)].sum()) for s, f in frames.items()}
    uni = sorted(tv, key=tv.get, reverse=True)[:cfg["universe_size"]]
    ind = {}
    for s in uni:
        x = e.calculate_indicators(frames[s])
        x["symbol"] = s
        ind[s] = x
    rng = np.random.default_rng(placebo_seed) if placebo_seed is not None else None
    trades = []
    for d in test_days:
        e.new_day()
        risk = DailyRisk()
        nd = nifty[nifty.index.date == d]
        if len(nd) < 10:
            continue
        prev_n = nifty[nifty.index.date < d]
        if prev_n.empty:
            continue
        n_prev_close = prev_n["close"].iloc[-1]
        first3 = nd.iloc[:3]
        n_ret = (first3["close"].iloc[-1] / n_prev_close - 1) * 100
        n_or = (first3["high"].max() - first3["low"].min()) / n_prev_close * 100
        above = bool(first3["close"].iloc[-1] > first3["vwap"].iloc[-1])
        vprev = [v for k, v in vix.items() if k < d]
        t925 = nd.index[2]
        snap = pd.DataFrame({s: ind[s].loc[t925] for s in uni if t925 in ind[s].index}).T
        for col in ("close", "prev_close", "open_vol_ratio"):
            snap[col] = pd.to_numeric(snap[col], errors="coerce")
        picks = e.rank_universe(snap, n_ret, n_or, vprev[-1] if vprev else None, above)
        if rng is not None and len(picks):
            pool = list(snap.dropna(subset=["open_vol_ratio"]).index)
            chosen = rng.choice(pool, size=min(len(picks), len(pool)), replace=False)
            e.watch = {s: ("long" if rng.random() < 0.5 else "short") for s in chosen}
        if not e.watch:
            continue
        day_bars = {s: ind[s][ind[s].index.date == d] for s in e.watch}
        times = nd.index
        open_pos = {}
        for i, ts in enumerate(times):
            for s, pos in list(open_pos.items()):
                db = day_bars[s]
                if ts not in db.index:
                    continue
                b = db.loc[ts]
                long = pos["sig"].side == "long"
                for tt, p, close in ticks(b, ts, long):
                    act = e.manage_open_position({"symbol": s, "ts": tt, "price": p, "bar_close": close, "atr": b["atr"]})
                    if act["action"] != "exit":
                        continue
                    sg, why = pos["sig"], act["reason"]
                    if why == "stop hit":
                        lvl = act["stop"]
                        px = b["open"] if (long and b["open"] < lvl) or (not long and b["open"] > lvl) else lvl
                    elif why == "target hit":
                        px = b["open"] if (long and b["open"] > sg.target) or (not long and b["open"] < sg.target) else sg.target
                    else:
                        px = p
                    q = pos["qty"]
                    ch = costs.equity_round_trip(pos["entry"], px, q, long, slippage_pct=slippage_pct).total
                    pnl = (px - pos["entry"]) * q * (1 if long else -1) - ch
                    risk.on_close(pnl)
                    trades.append({"date": d, "symbol": s, "side": sg.side, "entry_time": pos["t"], "exit_time": tt,
                                   "entry": round(pos["entry"], 2), "exit": round(px, 2), "qty": q,
                                   "risk_rs": round(abs(pos["entry"] - sg.stop) * q, 0), "charges": round(ch, 2),
                                   "pnl": round(pnl, 2), "reason": why})
                    open_pos.pop(s)
                    break
            if i + 1 >= len(times):
                continue
            for s in list(e.watch):
                if s in open_pos or s in e.done:
                    continue
                db = day_bars[s]
                if ts not in db.index or times[i + 1] not in db.index:
                    continue
                sig = e.generate_signal(db.loc[ts])
                if sig is None:
                    continue
                entry = float(db.loc[times[i + 1], "open"])
                qty = e.compute_position_size(C.CAPITAL, abs(entry - sig.stop), price=entry, room=risk.room())
                ok, _ = risk.can_enter(abs(entry - sig.stop) * qty)
                if qty <= 0 or not ok or (entry - sig.stop) * (1 if sig.side == "long" else -1) <= 0:
                    e.done.add(s)
                    continue
                e.on_entry(sig)
                risk.trades += 0
                open_pos[s] = {"sig": sig, "entry": entry, "qty": qty, "t": times[i + 1]}
    tr = pd.DataFrame(trades)
    s = summary(tr, C.CAPITAL, len(test_days))
    if not tr.empty:
        s["t_stat_per_trade"] = round(t_stat(tr["pnl"]), 2)
        s["avg_R"] = round(float((tr["pnl"] / tr["risk_rs"].replace(0, np.nan)).mean()), 3)
        s["by_side"] = tr.groupby("side")["pnl"].agg(["size", "sum"]).round(0).to_dict("index")
    return tr, s


if __name__ == "__main__":
    frames = load()
    out = {}
    tr, s = run(frames)
    print_summary("Strategy 3 RS + volume breakout - base (intraday, slippage 0.1%/side)", s)
    out["base"] = s
    if not tr.empty:
        tr.to_csv(RESULTS / "s3_trades.csv", index=False)
    keys = ("trades", "win_pct", "avg_pnl", "total_pnl", "avg_R", "max_drawdown", "t_stat_per_trade")
    _, s2 = run(frames, slippage_pct=0.05)
    print_summary("slippage 0.05%/side", {k: s2.get(k) for k in keys})
    out["slippage 0.05%"] = s2
    pl = [run(frames, placebo_seed=k)[1] for k in range(5)]
    avg = {k: round(float(np.mean([p.get(k) or 0 for p in pl])), 2) for k in ("trades", "win_pct", "avg_pnl", "total_pnl", "avg_R")}
    print_summary("placebo: same rules, random stocks and sides (average of 5 runs)", avg)
    out["placebo_avg5"] = avg
    (RESULTS / "s3_summary.json").write_text(json.dumps(out, indent=1, default=str))
