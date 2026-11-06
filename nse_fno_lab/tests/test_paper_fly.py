"""Real-price paper iron fly (strategy 2 live tracker), on hand-made option chains."""
from datetime import datetime

import pytest

import config as C
from nse_calendar import IST
from paper_fly import PaperFly


def chain(spot, atm_prem=60.0, wing_prem=8.0, spread=0.5, K=25000, W=150):
    """Tiny chain around K: straight-line prices good enough to drive the rules."""
    out = {}
    for k in (K - 2 * W, K - W, K - 50, K, K + 50, K + W, K + 2 * W):
        ce = max(spot - k, 0) + max(atm_prem - abs(k - spot) * (atm_prem - wing_prem) / W, 0.5)
        pe = max(k - spot, 0) + max(atm_prem - abs(k - spot) * (atm_prem - wing_prem) / W, 0.5)
        out[float(k)] = {"CE": {"ltp": ce, "bid": ce - spread / 2, "ask": ce + spread / 2, "oi": 0},
                         "PE": {"ltp": pe, "bid": pe - spread / 2, "ask": pe + spread / 2, "oi": 0}}
    return out


def t(hm):
    h, m = map(int, hm.split(":"))
    return datetime(2026, 11, 3, h, m, 20, tzinfo=IST)


def new_fly():
    return PaperFly.open("0945_W150", "NIFTY", chain(25000), 25000, 25000.0, 150.0, t("09:45"), 13.0, lots=1)


def test_open_uses_bid_for_shorts_and_ask_for_wings():
    f = new_fly()
    c = chain(25000)
    assert f.entry_px["sc"] == c[25000.0]["CE"]["bid"] and f.entry_px["lc"] == c[25150.0]["CE"]["ask"]
    assert f.credit == pytest.approx(2 * (60 - 0.25) - 2 * (8 + 0.25))
    assert f.charges > 0 and f.qty == C.LOT["NIFTY"]


def test_profit_take_on_decay():
    f = new_fly()
    r = f.mark(t("12:00"), 25000, chain(25000, atm_prem=25, wing_prem=2), 13.0, 50)
    assert r["action"] == "exit" and "profit take" in r["reason"] and f.final_net > 0


def test_stop_on_big_move_after_recenter_used():
    f = new_fly()
    f.adjustments = 1   # no re-centre left
    r = f.mark(t("11:00"), 25120, chain(25120, atm_prem=60, wing_prem=8), 13.0, 50)
    assert r["action"] == "exit" and f.final_net < 0


def test_breakeven_breach_recenters_once():
    f = new_fly()
    lo, hi = f.be
    spot = hi + 5
    c = chain(spot, atm_prem=58, wing_prem=10, K=round(spot / 50) * 50)
    c.update({k: v for k, v in chain(spot, atm_prem=58, wing_prem=10).items() if k not in c})
    r = f.mark(t("10:30"), spot, c, 13.0, 50)
    assert r["action"] == "recenter" and f.adjustments == 1 and f.K == round(spot / 50) * 50
    assert f.realized < 0                          # the first fly was closed at a loss


def test_square_off_and_shadow_hold():
    f = new_fly()
    r = f.mark(t("15:15"), 25010, chain(25010, atm_prem=12, wing_prem=0.5), 13.0, 50)
    assert r["action"] == "exit" and r["reason"].startswith("square-off")
    row = f.result_row("2026-11-03", bsm_credit_flat=110.0)
    assert row["hold_to_1515_gross_rs"] is not None and row["credit_ratio"] == pytest.approx(f.shadow_credit / 110, rel=1e-3)


def test_state_roundtrip():
    f = new_fly()
    g = PaperFly.from_json(f.to_json())
    assert g.credit == f.credit and g.strikes == f.strikes
