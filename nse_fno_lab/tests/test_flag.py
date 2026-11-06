"""Leg-2 flag continuation (strategy 1): fires after impulse + orderly flag, not after a deep pullback."""
import pytest
import numpy as np
import pandas as pd

from tests.conftest import make_bars


def _day_with_flag(deep: bool):
    df = make_bars(days=25, seed=5)
    last = np.where(df.index.normalize() == df.index.normalize()[-1])[0]
    o, h, l, c, v = (df.columns.get_loc(x) for x in ("open", "high", "low", "close", "volume"))
    px = df.iloc[last[0], o]

    def put(i, op, cl, wick):
        df.iloc[i, o], df.iloc[i, c] = op, cl
        df.iloc[i, h], df.iloc[i, l] = max(op, cl) + wick, min(op, cl) - wick

    # 09:15-09:55 quiet (8 bars), 09:55-10:35 impulse (8 bars, +0.25%/bar with wide ranges)
    for j in range(8):
        put(last[j], px, px + (3 if j % 2 else -3), 4)
    p = px
    for j in range(8, 16):
        put(last[j], p, p * 1.0025, 25)
        p *= 1.0025
    top = p
    impulse_size = top - px
    # flag: 9 tight bars drifting down a little (or a deep fall below the 50% retracement)
    for j in range(16, 25):
        drop = (0.65 if deep else 0.15) * impulse_size * (j - 15) / 9
        put(last[j], top - drop + 2, top - drop, 3)
    # breakout bar: closes above the flag high (orderly case) / above the last flag bar (deep case)
    flag_high = df.iloc[last[16]:last[25], h].max()
    put(last[25], df.iloc[last[24], c], (flag_high + 10) if not deep else df.iloc[last[24], c] + 40, 5)
    # volume: heavy on the impulse, light in the flag (the same in both cases)
    for j in range(8, 16):
        df.iloc[last[j], v] = 9000.0
    for j in range(16, 25):
        df.iloc[last[j], v] = 2000.0
    df.iloc[last[25], v] = 8000.0
    for j in range(26, len(last)):
        put(last[j], df.iloc[last[j - 1], c], df.iloc[last[j - 1], c], 3)
    return df, last


def _signals(df, last):
    from engines.opt_buy import StrategyEngine
    vix = pd.Series(13.0, index=pd.date_range("2026-09-01", periods=90).date)
    e = StrategyEngine(require_oi=False)
    x = e.calculate_indicators(df, vix=vix)
    out = []
    for t in x.index[x.index.date == x.index[-1].date()]:
        s = e.generate_signal(x.loc[t])
        if s:
            out.append(s)
            e.on_entry(s)
            e.state = e.state.__class__.COOLDOWN   # exit at once so later signals are not blocked
            e.cooldown_left = 1
    return out


def test_flag_fires_after_orderly_consolidation():
    df, last = _day_with_flag(deep=False)
    kinds = [s.meta["kind"] for s in _signals(df, last)]
    assert "leg1" in kinds and "leg2_flag" in kinds


def test_no_flag_after_deep_pullback():
    df, last = _day_with_flag(deep=True)
    kinds = [s.meta["kind"] for s in _signals(df, last)]
    assert "leg2_flag" not in kinds


def test_band_pullback_turns_climax_bar_into_limit_order():
    from engines.opt_buy import StrategyEngine
    import config as C
    df, last = _day_with_flag(deep=False)
    vix = pd.Series(13.0, index=pd.date_range("2026-09-01", periods=90).date)
    e = StrategyEngine(dict(C.S1, gt_mode="band_pullback"), require_oi=False)
    x = e.calculate_indicators(df, vix=vix)
    sigs = [s for s in (e.generate_signal(x.loc[t]) for t in x.index[x.index.date == x.index[-1].date()]) if s]
    first = sigs[0]
    # the impulse bars score well above 1.9, so the first signal is a limit order back inside the bar
    assert first.meta["kind"] == "leg1_pullback" and first.meta["entry"] == "limit"
    b = x.loc[first.ts]
    assert first.meta["limit"] == pytest.approx(b["close"] - 0.3 * (b["high"] - b["low"]))
    assert first.meta["stop_level"] < first.meta["limit"]
    filled = e.fill_limit(first, first.meta["limit"], 100.0)
    assert filled.target - filled.underlying == pytest.approx(2 * (filled.underlying - filled.stop))
