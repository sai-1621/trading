"""Backtest: strategy 2, expiry-day iron fly, NIFTY and SENSEX.

Two data sets:
  5m : last ~60 sessions of 5-minute bars (entry 09:45, the real rules)          -> about 12 expiries each
  1h : ~3 years of hourly bars (entry 10:15 bar close, coarser path checks)     -> about 150 expiries each
Option prices: Black-Scholes at IV = yesterday's India VIX close x iv_mult, flat across strikes. Real expiry-day
ATM IV can sit well above or below VIX and OTM wings trade richer (skew), so the IV sensitivity rows matter
more than the base row. Expiry day = the last trading day in the data on or before the nominal weekday
(NIFTY: Thursday until Aug 2025, then Tuesday; SENSEX: Friday in 2024, Tuesday Jan-Aug 2025, then Thursday).

    .venv\\Scripts\\python backtests\\bt_iron_fly.py
"""
import json
from datetime import date, timedelta

import pandas as pd

from common import RESULTS, print_summary, summary, t_stat, ticks

import config as C
import costs
import greeks
from data_yahoo import bars, vix_daily
from engines.base import Signal
from engines.iron_fly import StrategyEngine
from nse_calendar import years_to_expiry

DATA = {"NIFTY": "^NSEI", "SENSEX": "^BSESN"}


def nominal_weekday(sym: str, d: date) -> int:
    if sym == "NIFTY":
        return 3 if d < date(2025, 9, 1) else 1
    if d < date(2025, 1, 1):
        return 4
    return 1 if d < date(2025, 9, 1) else 3


def expiry_days(sym: str, days: list[date]) -> set[date]:
    have = set(days)
    out = set()
    for d in days:
        mon = d - timedelta(days=d.weekday())
        nom = mon + timedelta(days=nominal_weekday(sym, d))
        e = nom
        while e >= mon and e not in have:
            e -= timedelta(days=1)
        if e >= mon and e in have:
            out.add(e)
    return out




def leg_orders(fly, prices, opening: bool, exch: str) -> float:
    """Charges for opening (sell shorts, buy wings) or closing (reverse) all four legs at `prices`."""
    tot = 0.0
    for k, px in prices.items():
        short = k in ("sc", "sp")
        side = ("sell" if short else "buy") if opening else ("buy" if short else "sell")
        tot += costs.option_order(max(px, 0.05), fly.qty, side, exch).total
    return tot


def run(sym="NIFTY", interval="5m", iv_mult=1.0, extra_slip_pts=0.0, cfg_over=None, no_filters=False, wing_iv_mult=None):
    df = bars(DATA[sym], interval)
    daily = bars(DATA[sym], "1d")
    vix = vix_daily()
    cfg = dict(C.S2, symbol=sym, **(cfg_over or {}))
    if wing_iv_mult is not None:
        cfg["wing_iv_mult"] = wing_iv_mult
    if interval == "1h":
        cfg["entry_time"] = "10:15"
    if no_filters:
        cfg.update(vix_max=99, gap_max_pct=99, or_max_expected_move=99, min_premium_vs_realized=0)
    e = StrategyEngine(cfg)
    e.bar_min = 60 if interval == "1h" else 5
    x = e.calculate_indicators(df, daily=daily, vix=vix)
    days = sorted(set(x.index.date))
    exp = expiry_days(sym, days)
    exch = C.EXCHANGE_OF[sym]
    trades = []
    for d in sorted(exp):
        if d < days[min(15, len(days) - 1)]:
            continue
        day = x[x.index.date == d]
        e.new_day()
        vprev = day["vix_prev"].iloc[0]
        if pd.isna(vprev):
            continue
        iv = vprev / 100 * iv_mult
        sig, charges, reason, adj = None, 0.0, "", 0
        for i, (ts, b) in enumerate(day.iterrows()):
            if e.fly is None:
                # filters see the real VIX; option prices use iv = VIX x iv_mult
                sig = e.generate_signal(b, None, expiry_day=True, iv=iv)
                if sig is not None:
                    tt = ts + pd.Timedelta(minutes=e.bar_min)
                    fly = sig.instrument["fly"]
                    e.on_entry(sig)
                    charges += leg_orders(fly, {k: v["premium"] for k, v in fly.legs.items()}, True, exch)
                    charges += 4 * extra_slip_pts * fly.qty
                    entry = {"time": tt, "spot": b["close"], "K": fly.K, "W": fly.W, "credit": fly.credit,
                             "qty": fly.qty, "lots": fly.qty // e.lot}
                continue
            for tt, p, close in ticks(b, ts, None, e.bar_min):
                pr = e.model_prices(e.fly, p, tt, iv)
                act = e.manage_open_position({"ts": tt, "spot": p, "prices": pr, "bar_close": close})
                if act["action"] == "recenter":
                    charges += leg_orders(e.fly, pr, False, exch) + 4 * extra_slip_pts * e.fly.qty
                    e.recenter(tt, p, None, iv, pr)
                    charges += leg_orders(e.fly, {k: v["premium"] for k, v in e.fly.legs.items()}, True, exch)
                    charges += 4 * extra_slip_pts * e.fly.qty
                    adj += 1
                elif act["action"] == "exit":
                    charges += leg_orders(e.fly, pr, False, exch) + 4 * extra_slip_pts * e.fly.qty
                    gross = (e.fly.credit - e.close_cost_unit(e.fly, pr)) * e.fly.qty + e.fly.realized
                    reason = act["reason"]
                    trades.append({"date": d, **entry, "exit_time": tt, "spot_out": p, "adjustments": adj,
                                   "gross": round(gross, 2), "charges": round(charges, 2),
                                   "pnl": round(gross - charges, 2), "reason": reason, "iv": round(iv, 4)})
                    e.fly, e.state = None, e.state.__class__.DONE_FOR_DAY
                    break
            if e.state == e.state.__class__.DONE_FOR_DAY:
                break
    tr = pd.DataFrame(trades)
    n_exp = len([d for d in exp if d >= days[min(15, len(days) - 1)]])
    s = summary(tr, C.CAPITAL, n_exp)
    s["expiry_days"] = n_exp
    if not tr.empty:
        s["t_stat_per_trade"] = round(t_stat(tr["pnl"]), 2)
        s["by_year"] = tr.groupby(pd.to_datetime(tr["date"]).dt.year)["pnl"].agg(["size", "sum"]).round(0).to_dict("index")
    return tr, s


if __name__ == "__main__":
    out = {}
    keys = ("expiry_days", "trades", "win_pct", "avg_pnl", "total_pnl", "max_drawdown", "charges", "t_stat_per_trade")
    for sym in ("NIFTY", "SENSEX"):
        for interval in ("5m", "1h"):
            tr, s = run(sym, interval)
            print_summary(f"Strategy 2 iron fly {sym} {interval} - base (IV = VIX)", s)
            out[f"{sym} {interval} base"] = s
            if not tr.empty:
                tr.to_csv(RESULTS / f"s2_{sym}_{interval}_trades.csv", index=False)
            for name, kw in {"IV = 0.6 x VIX": {"iv_mult": 0.6}, "IV = 0.7 x VIX": {"iv_mult": 0.7},
                             "IV = 0.8 x VIX": {"iv_mult": 0.8}, "IV = 1.2 x VIX": {"iv_mult": 1.2},
                             "extra slippage 0.5 pt/leg": {"extra_slip_pts": 0.5},
                             "no regime filters": {"no_filters": True}}.items():
                _, s2 = run(sym, interval, **kw)
                print_summary(f"{sym} {interval} - {name}", {k: s2.get(k) for k in keys})
                out[f"{sym} {interval} {name}"] = s2
    (RESULTS / "s2_summary.json").write_text(json.dumps(out, indent=1, default=str))
