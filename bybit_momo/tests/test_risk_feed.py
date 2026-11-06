import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from feed import CandleBook  # noqa: E402
from risk import Guard, size  # noqa: E402


def test_size_risk_based_then_capped():
    s = size(1000, 1.00, 0.95, avg_turnover_5m=1e6)          # 5% stop: 10 USDT risk -> ~190 USDT
    assert s["why"] == "risk" and 150 < s["notional"] < 200
    tight = size(1000, 1.00, 0.995, avg_turnover_5m=1e6)     # 0.5% stop wants ~1,300 USDT -> position cap
    assert tight["why"] == "max position" and tight["notional"] == pytest.approx(C.MAX_POSITION_USDT)
    thin = size(1000, 1.00, 0.95, avg_turnover_5m=800)       # thin coin: 10% of 800 USDT a bar
    assert thin["qty"] == 0.0 or thin["why"] == "liquidity"


def test_size_refuses_bad_stop_and_minimum():
    assert size(1000, 1.0, 1.0, 1e6)["qty"] == 0
    assert size(1000, 1.0, 0.9, 1e6, min_cost=500)["qty"] == 0


def test_guard_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "KILL_FILE", tmp_path / "KILL")
    g = Guard(equity=1000)
    assert g.can_open()[0]
    g.booked(-31)
    assert not g.can_open()[0]                                # 3% daily loss
    g2 = Guard(equity=1000, open_count=C.MAX_OPEN)
    assert not g2.can_open()[0]


def test_candlebook_detects_close():
    b = CandleBook()
    b.seed("X", [[0, 1, 1, 1, 1, 10], [300000, 1, 1.1, 0.9, 1.05, 5]])
    assert not b.update("X", [300000, 1, 1.2, 0.9, 1.1, 8])    # same candle, still forming
    assert b.update("X", [600000, 1.1, 1.1, 1.1, 1.1, 1])      # new candle -> previous closed
    assert list(b.bars["X"])[-1][2] == 1.2
