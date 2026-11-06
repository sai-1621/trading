import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import arb_core as A  # noqa: E402
import backtest_funding_arb as B  # noqa: E402

R = A.Rules()


# ------------------------------------------------------------------ arb_core
def test_rate8_scales_short_intervals():
    assert A.rate8(0.0001, 4) == pytest.approx(0.0002)
    assert A.rate8(0.0001, 1) == pytest.approx(0.0008)
    assert A.rate8(0.0003, 8) == pytest.approx(0.0003)


def test_entry_exit_rules():
    assert A.entry_ok(R, 0.0003, 0.0003) and not A.entry_ok(R, 0.0003, 0.0002) and not A.entry_ok(R, 0.00029, 0.001)
    assert A.exit_ok(R, 0.00004) and A.exit_ok(R, -0.0001) and not A.exit_ok(R, 0.00005)


def test_spot_symbol_mapping():
    bases = {"PEPE", "BTC", "BABYDOGE"}
    assert A.spot_symbol("1000PEPE", bases) == ("PEPE", 1000.0)
    assert A.spot_symbol("1MBABYDOGE", bases) == ("BABYDOGE", 1e6)
    assert A.spot_symbol("BTC", bases) == ("BTC", 1.0)
    assert A.spot_symbol("FARTCOIN", bases) == (None, 0.0)


def test_margin_survives_35pct_spike():
    q, entry = 1.0, 100.0
    margin = R.margin_frac * q * entry
    assert A.liq_price_short(margin, q, entry, R.mmr) >= 1.35 * entry


def test_delta_band_and_rounding():
    assert A.delta_fix(R, 100.0, 99.5, 0.1) == 0.0            # 0.5% gap: inside the 1% band
    assert A.delta_fix(R, 100.0, 97.0, 0.1) == pytest.approx(3.0)
    assert A.delta_fix(R, 100.0, 103.05, 0.1) == pytest.approx(-3.0)


def test_derisk_levels_and_resize_keeps_equity():
    assert A.derisk_action(R, 0.30) == "ok" and A.derisk_action(R, 0.12) == "top_up" and A.derisk_action(R, 0.05) == "cut"
    q2, dq = A.topup_resize(10, 400, 100, 125, 125, R.margin_frac)
    before = 10 * 125 + 400 + 10 * (100 - 125)
    after = q2 * 125 + R.margin_frac * q2 * 125
    assert after == pytest.approx(before) and dq > 0


def test_pair_size_caps():
    assert A.pair_size(R, 10_000, 10_000) == pytest.approx(1000 / 1.4)
    assert A.pair_size(R, 1e9, 1e9) == pytest.approx(R.max_pair_usdt)


# ------------------------------------------------------------------ backtest engine on synthetic data
def coin(n=200, px=None, rate=0.0005, every=8, name="X", top=True):
    px = np.full(n, 100.0) if px is None else np.asarray(px, float)
    stamp = np.zeros(n, bool)
    stamp[::every] = True
    r = np.where(stamp, rate, 0.0)
    r8 = np.full(n, rate * 8 / every)
    return B.Coin(name, 0, px.copy(), px.copy(), px.copy(), px.copy(), px.copy(), px.copy(), np.ones(n, bool), stamp, r, r8,
                  r8.copy(), np.ones(n, bool), np.full(n, top))


def run(c, p=B.Params()):
    gidx = pd.date_range("2022-01-01", periods=len(c.po), freq="h", tz="UTC")
    return B.simulate([c], gidx, p)


def test_flat_price_collects_funding_minus_costs():
    p = B.Params(slots=1)
    eq, tr, tot = run(coin(200, rate=0.0005), p)
    # entry decided at hour 0, filled at hour 1: stamps at 8, 16, ..., 192 = 24 payments
    n_pay = len(range(8, 200, 8))
    notional = 100_000 / (1 + p.margin_ratio + p.fee_spot + p.fee_perp)
    assert tot["funding_in"] == pytest.approx(n_pay * 0.0005 * notional * (1 - p.slip_top), rel=0.01)
    entry_cost = notional * (p.fee_spot + p.fee_perp + 2 * p.slip_top)
    assert eq.iloc[-1] - 100_000 == pytest.approx(tot["funding_in"] - entry_cost, rel=0.02)


def test_negative_funding_triggers_exit_next_hour():
    c = coin(100, rate=0.0005)
    c.rate8[40:] = -0.0001
    c.rate[40::8] = -0.0001
    c.rate[40] = -0.0001
    eq, tr, tot = run(c, B.Params(slots=1))
    assert len(tr) == 1 and tr.iloc[0]["why"] == "funding_low"
    assert tr.iloc[0]["t_out"] == pd.Timestamp("2022-01-01", tz="UTC") + pd.Timedelta(hours=41)


def test_spike_liquidates_and_loss_is_bounded_by_hedge():
    px = np.full(100, 100.0)
    px[20:] = 160.0                       # +60% jump, beyond the 35% design spike
    c = coin(100, px=px)
    eq, tr, tot = run(c, B.Params(slots=1, derisk_at=0.0))
    assert tot["liquidations"] == 1 and tr.iloc[0]["why"] == "liquidated"
    assert -12_000 < eq.iloc[-1] - 100_000 < 0     # spot gain offsets most of the lost margin


def test_derisk_before_liquidation_on_slow_rally():
    px = np.linspace(100, 150, 300)          # slow +50%: de-risk should keep it alive
    c = coin(300, px=px)
    eq, tr, tot = run(c, B.Params(slots=1))
    assert tot["liquidations"] == 0 and tot["derisks"] >= 1


def test_not_eligible_no_trade():
    c = coin(100)
    c.elig[:] = False
    eq, tr, tot = run(c)
    assert len(tr) == 0 and eq.iloc[-1] == pytest.approx(100_000)


def test_mark_proxy_ignores_perp_only_wick():
    c = coin(100)
    c.ph[30] = 200.0                       # perp-only wick, spot stays at 100
    _, tr_last, tot_last = run(c, B.Params(slots=1))
    _, tr_mark, tot_mark = run(c, B.Params(slots=1, liq_trigger="mark_proxy"))
    assert tot_last["liquidations"] == 1 and tot_mark["liquidations"] == 0
