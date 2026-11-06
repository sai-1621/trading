import pandas as pd

import config as C
from broker import PaperBroker
from nse_calendar import IST
from risk import DailyRisk, KillSwitch, Mode, Order, OrderGuard


def guard(**kw):
    b = PaperBroker(**kw)
    k = KillSwitch(DailyRisk())
    return b, k, OrderGuard(b, k, timeout_s=0.1, wait_s=0)


def test_partial_entry_keeps_filled_part():
    b, k, g = guard(quotes={"X": 100.0}, partial={"X": 0.5})
    f = g.execute(Order("X", "1", "NFO", "BUY", 130), lambda: 100.0)
    assert f.status == "partial" and 0 < f.filled < 130


def test_exit_falls_back_to_market_and_completes():
    b, k, g = guard(quotes={"X": 100.0})
    # limit sells priced above the market never fill; the market fallback must finish the exit
    g.reprice_steps = 0
    f = g.execute(Order("X", "1", "NFO", "SELL", 65), lambda: 101.0, must_complete=True)
    assert f.status == "complete" and f.filled == 65


def test_timeout_is_looked_up_not_resent():
    b, k, g = guard(quotes={"X": 100.0}, timeout_once={"X"})
    f = g.execute(Order("X", "1", "NFO", "BUY", 65), lambda: 100.0)
    assert f.filled == 65 and len(b.orders) == 1        # found by tag, not sent twice


def test_spread_rolls_back_when_short_leg_rejected():
    b, k, g = guard(quotes={"W1": 10.0, "W2": 10.0, "S1": 100.0, "S2": 100.0}, reject={"S1"})
    legs = [Order("S1", "1", "NFO", "SELL", 65), Order("S2", "2", "NFO", "SELL", 65),
            Order("W1", "3", "NFO", "BUY", 65), Order("W2", "4", "NFO", "BUY", 65)]
    res = g.open_spread(legs, {s: (lambda s=s: b.quotes[s]) for s in b.quotes})
    assert res is None
    net = {}
    for d in b.orders.values():
        o, f = d["order"], d["fill"]
        net[o.symbol] = net.get(o.symbol, 0) + (f.filled if o.side == "BUY" else -f.filled)
    assert all(v == 0 for v in net.values())             # flat again, no naked leg
    first = list(b.orders.values())[0]["order"]
    assert first.side == "BUY"                           # hedges were bought first


def test_kill_switch_daily_loss_and_vix():
    r = DailyRisk(capital=300000)
    k = KillSwitch(r)
    assert k.check() == Mode.OK
    k2 = KillSwitch(DailyRisk(capital=300000))
    assert k2.check(vix_now=15.0, vix_open=13.0, flatten_on_vix=True) == Mode.FLATTEN_ALL
    r.realized = -4600
    assert k.check() == Mode.FLATTEN_ALL


def test_daily_risk_limits():
    r = DailyRisk(capital=300000)
    assert r.can_enter(2000)[0]
    r.on_close(-2300)
    r.on_close(-1000)
    ok, why = r.can_enter(100)
    assert not ok


def test_iron_fly_recenter_then_exit():
    from engines.iron_fly import StrategyEngine
    e = StrategyEngine()
    ts = pd.Timestamp("2026-11-03 09:40", tz=IST)        # a Tuesday expiry
    fly = e.build_fly(ts, 25010, None, 0.13)
    fly.qty = 65
    from engines.base import Signal
    e.on_entry(Signal("neutral", ts, 25010, fly.be_dn, fly.be_up, "t", instrument={"fly": fly}))
    p = {"sc": fly.legs["sc"]["premium"], "sp": fly.legs["sp"]["premium"],
         "lc": fly.legs["lc"]["premium"], "lp": fly.legs["lp"]["premium"]}
    a = e.manage_open_position({"ts": ts, "spot": fly.be_up + 1, "prices": p, "bar_close": True})
    assert a["action"] == "recenter"
    e.recenter(ts, fly.be_up + 1, None, 0.13, p)
    f2 = e.fly
    p2 = {k: v["premium"] for k, v in f2.legs.items()}
    a2 = e.manage_open_position({"ts": ts, "spot": f2.be_up + 1, "prices": p2, "bar_close": True})
    assert a2["action"] == "exit"


def test_opt_buy_trend_day_signal():
    from engines.opt_buy import StrategyEngine
    from tests.conftest import make_bars
    import numpy as np
    df = make_bars(days=25, seed=3)
    last = df.index.normalize() == df.index.normalize()[-1]
    # make the last day a strong trend day after 10:00 with growing ranges
    idx = np.where(last)[0]
    for j, i in enumerate(idx[9:]):
        df.iloc[i, df.columns.get_loc("close")] = df.iloc[idx[8], 3] * (1 + 0.0015 * (j + 1))
        df.iloc[i, df.columns.get_loc("open")] = df.iloc[idx[8], 3] * (1 + 0.0015 * j)
        df.iloc[i, df.columns.get_loc("high")] = df.iloc[i, 3] * 1.001
        df.iloc[i, df.columns.get_loc("low")] = df.iloc[i, 0] * 0.999
    vix = pd.Series(14.0, index=pd.date_range("2026-09-01", periods=90).date)
    e = StrategyEngine(require_oi=False)
    x = e.calculate_indicators(df, vix=vix)
    sigs = [s for s in (e.generate_signal(x.loc[t]) for t in x.index[idx]) if s]
    assert sigs and sigs[0].side == "long" and sigs[0].qty % C.LOT["NIFTY"] == 0
