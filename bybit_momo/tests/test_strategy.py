import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import strategy as S  # noqa: E402


def flat(n=400, px=1.0, seed=1, vol=1000.0):
    r = np.random.default_rng(seed)
    c = px * (1 + r.normal(0, 0.0015, n))
    o = np.concatenate([[px], c[:-1]])
    h = np.maximum(o, c) * 1.001
    lo = np.minimum(o, c) * 0.999
    v = r.uniform(0.8, 1.2, n) * vol
    return {"open": o, "high": h, "low": lo, "close": c, "volume": v, "turnover": v * c * 400}


def put(d, i, o, h, lo, c, v):
    d["open"][i], d["high"][i], d["low"][i], d["close"][i], d["volume"][i] = o, h, lo, c, v


def test_ignition_fires_on_flat_base_volume_spike():
    d = flat()
    put(d, 300, 1.0, 1.06, 0.999, 1.058, 20000)          # +6% candle, close near the high, 20x volume
    sigs = [s for s in S.scan(d, universe=False) if s.leg == "ignition"]
    assert [s.i for s in sigs] == [300] and sigs[0].stop == pytest.approx(0.999)


def test_no_ignition_when_close_in_middle_of_candle():
    d = flat()
    put(d, 300, 1.0, 1.10, 0.999, 1.05, 20000)           # big upper wick: close at ~50% of the range
    assert not [s for s in S.scan(d, universe=False) if s.leg == "ignition"]


def flag_day():
    d = flat()
    # impulse: bars 300-305 climb +20% on huge volume
    p = 1.0
    for k, i in enumerate(range(300, 306)):
        put(d, i, p, p * 1.035, p * 0.998, p * 1.033, 30000 if k == 1 else 12000)
        p *= 1.033
    peak = d["high"][305]
    # flag: 14 quiet bars drifting slightly lower, well above the 0.618 retracement
    for i in range(306, 320):
        q = peak * (0.97 - 0.001 * (i - 306))
        put(d, i, q, q * 1.004, q * 0.996, q * 0.999, 1500)
    top = max(d["high"][312:320])
    put(d, 320, d["close"][319], top * 1.02, d["close"][319] * 0.999, top * 1.015, 6000)   # breakout, 4x flag volume
    return d


def test_flag_triggers_after_quiet_consolidation():
    d = flag_day()
    log = []
    sigs = [s for s in S.scan(d, universe=False, stalk_log=log) if s.leg == "flag"]
    assert sigs and sigs[0].i == 320
    assert sigs[0].stop == pytest.approx(min(d["low"][312:320]))
    assert log and log[0]["result"] == "triggered"


def test_flag_cancelled_below_fib():
    d = flag_day()
    put(d, 310, d["close"][309], d["close"][309], 1.0, 1.01, 1500)        # deep wick below the 0.618 level
    log = []
    assert not [s for s in S.scan(d, universe=False, stalk_log=log) if s.leg == "flag"]
    assert log[0]["result"] == "broke the 0.618 level"


def test_indicators_no_lookahead():
    d = flag_day()
    full = S.indicators(d)
    cut = {k: v[:310] for k, v in d.items()}
    part = S.indicators(cut)
    for k in full:
        np.testing.assert_allclose(full[k][:310], part[k], equal_nan=True, err_msg=k)
    early = [s.i for s in S.scan(cut, universe=False)]
    later = [s.i for s in S.scan(d, universe=False) if s.i < 310]
    assert early == later


def test_climax_sells_half_then_trail_exits():
    d = flat()
    x = S.indicators(d)
    pos = {"stop": 0.5, "left": 1.0, "partial": False}
    j = 350
    x["atr"][j], x["ema9"][j], x["ema20"][j] = 0.01, 1.0, 0.9
    d["open"][j], d["high"][j], d["low"][j], d["close"][j] = 1.0, 1.10, 0.99, 1.01
    x["upper_wick"][j] = (1.10 - 1.01) / (1.10 - 0.99)
    ev = S.exit_event(d, x, j, pos)
    assert ev[0][2].startswith("climax") and pos["left"] == pytest.approx(0.5)
    x["ema20"][j + 1] = 2.0
    d["low"][j + 1] = 0.9
    ev2 = S.exit_event(d, x, j + 1, pos)
    assert ev2[0][2] == "close below EMA20" and ev2[0][0] == pytest.approx(0.5)
