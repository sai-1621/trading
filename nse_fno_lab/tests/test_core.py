from datetime import date

import numpy as np
import pandas as pd
import pytest

import config as C
import costs
import greeks
from nse_calendar import expiries, is_expiry_day, nearest_expiry


# ---------------- costs
def test_option_stt_only_on_sell():
    buy = costs.option_order(100, 65, "buy")
    sell = costs.option_order(100, 65, "sell")
    assert buy.stt == 0 and sell.stt == pytest.approx(100 * 65 * 0.0015)
    assert buy.stamp == pytest.approx(100 * 65 * 0.00003) and sell.stamp == 0


def test_gst_on_brokerage_exchange_sebi():
    o = costs.option_order(150, 130, "sell", slippage=False)
    assert o.gst == pytest.approx(0.18 * (o.brokerage + o.exchange + o.sebi))


def test_breakeven_covers_round_trip():
    be = costs.option_breakeven_points(150, 65)
    rt = costs.option_round_trip(150, 150 + be, 65).total
    assert be * 65 == pytest.approx(rt, rel=1e-6)


def test_delivery_stt_both_sides():
    rt = costs.equity_round_trip(1000, 1000, 100, delivery=True, slippage_pct=0)
    assert rt.stt == pytest.approx(200.0)


# ---------------- calendar
def test_nifty_weekly_tuesday_and_holiday_shift():
    e = expiries("NIFTY", date(2026, 11, 2), 4)
    assert e[0] == date(2026, 11, 3) and e[0].weekday() == 1
    assert date(2026, 11, 23) in e          # 24 Nov 2026 is a holiday -> Monday 23 Nov
    assert date(2026, 11, 24) not in e


def test_sensex_thursday_and_banknifty_monthly():
    assert all(d.weekday() == 3 for d in expiries("SENSEX", date(2026, 11, 2), 3))
    bn = expiries("BANKNIFTY", date(2026, 11, 2), 2)
    assert bn == [date(2026, 11, 23), date(2026, 12, 29)]


def test_old_thursday_expiry_before_switch():
    assert is_expiry_day("NIFTY", date(2025, 8, 28))


def test_min_dte():
    assert nearest_expiry("NIFTY", date(2026, 11, 2), 2) == date(2026, 11, 9)   # 10 Nov is a holiday


# ---------------- greeks
def test_put_call_parity_and_iv_roundtrip():
    S, K, T, r, s = 25000, 25100, 5 / 365, 0.065, 0.14
    c, p = greeks.price(S, K, T, r, s, 1), greeks.price(S, K, T, r, s, -1)
    assert c - p == pytest.approx(S - K * np.exp(-r * T), abs=1e-6)
    assert greeks.implied_vol(c, S, K, T, r, 1) == pytest.approx(s, abs=1e-6)
    assert 0.4 < greeks.delta(S, K, T, r, s, 1) < 0.5


# ---------------- no look-ahead: indicators on truncated data must equal the full-data values
@pytest.mark.parametrize("engine", ["opt_buy", "iron_fly", "rs_breakout"])
def test_no_lookahead(engine, bars):
    import importlib
    E = importlib.import_module(f"engines.{engine}").StrategyEngine
    vix = pd.Series(13.0 + np.arange(20) * 0.1, index=pd.date_range("2026-10-01", periods=20).date)
    kw = {} if engine == "rs_breakout" else {"vix": vix}
    full = E().calculate_indicators(bars, **kw)
    for cut in (80, 300, 520, 777):
        part = E().calculate_indicators(bars.iloc[:cut], **kw)
        a, b = full.iloc[:cut], part
        cols = [c for c in b.columns if b[c].dtype.kind in "fi"]
        pd.testing.assert_frame_equal(a[cols], b[cols], check_exact=False, rtol=1e-9, obj=f"{engine} cut={cut}")


# ---------------- sizing
def test_opt_buy_sizing_respects_budget():
    from engines.opt_buy import StrategyEngine
    e = StrategyEngine()
    lots = e.compute_position_size(300000, 15.0, premium=150)
    budget = 300000 * 0.015 / 2
    q = lots * 65
    assert lots >= 1
    assert 15 * q + costs.option_round_trip(150, 135, q).total <= budget
    q2 = (lots + 1) * 65
    assert 15 * q2 + costs.option_round_trip(150, 135, q2).total > budget or 150 * q2 > 0.25 * 300000


def test_stock_sizing_scales_with_volatility():
    from engines.rs_breakout import StrategyEngine
    e = StrategyEngine()
    assert e.compute_position_size(300000, 10, price=1000) > e.compute_position_size(300000, 20, price=1000)


# ---------------- gamma vs theta filter (strategy 1)
def test_gamma_theta_filter_breakout_vs_chop():
    from verify_gamma_theta import rows
    r = {x["case"]: x for x in rows()}
    import math
    assert r["VIX 14 breakout (Thu)"]["pass"] and not r["VIX 14 normal bar (Thu)"]["pass"]
    # in trading time the ratio is about (ATR / VIX-implied 5-minute move)^2
    for case in ("VIX 14 breakout (Thu)", "VIX 22 chop (Tue)", "VIX 22 breakout (Tue)"):
        vix = 14 if "14" in case else 22
        implied = 25000 * vix / 100 / math.sqrt(252 * 75)
        assert r[case]["gamma/theta"] == pytest.approx((r[case]["atr5"] / implied) ** 2, rel=0.15), case
    assert r["VIX 22 breakout (Tue)"]["pass"]
    for case in ("VIX 22 chop (Mon)", "VIX 22 chop (Tue)", "VIX 22 chop, 0-DTE (Tue)"):
        assert not r[case]["pass"], case
    # theta is spread over the 375-minute session
    x = r["VIX 14 breakout (Thu)"]
    assert x["theta_5m"] == pytest.approx(abs(x["theta_day"]) * 5 / 375, abs=1e-3)
