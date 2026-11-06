"""Strategy 3 variants: causal indicators and the 3A scan rules."""
import numpy as np
import pandas as pd
import pytest

from engines.rs_variants import SwingBreakoutEngine


def daily(n=80, seed=2):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.015, n)))
    o = c * (1 + rng.normal(0, 0.004, n))
    h, l = np.maximum(o, c) * 1.01, np.minimum(o, c) * 0.99
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": rng.integers(1e5, 2e5, n).astype(float)},
                        index=pd.bdate_range("2026-01-01", periods=n))


def test_3a_indicators_causal():
    df = daily()
    e = SwingBreakoutEngine()
    full = e.calculate_indicators(df)
    part = e.calculate_indicators(df.iloc[:50])
    cols = ["hh20", "vol_sma", "vol_ratio", "atr", "ema", "low10", "traded_value20"]
    pd.testing.assert_frame_equal(full.iloc[:50][cols], part[cols])


def test_3a_signal_needs_breakout_range_and_volume():
    df = daily()
    i = df.index[-1]
    df.loc[i, ["low", "high", "close", "volume"]] = [df["high"].iloc[-21:-1].max(), df["high"].iloc[-21:-1].max() * 1.03,
                                                    df["high"].iloc[-21:-1].max() * 1.029, 6e5]
    e = SwingBreakoutEngine()
    x = e.calculate_indicators(df).assign(symbol="X")
    s = e.generate_signal(x.iloc[-1])
    assert s is not None and s.stop < s.underlying
    x2 = x.copy()
    x2.loc[i, "vol_ratio"] = 1.5
    assert e.generate_signal(x2.iloc[-1]) is None          # volume below 2x
    x3 = x.copy()
    x3.loc[i, "range_pos"] = 60
    assert e.generate_signal(x3.iloc[-1]) is None          # close not in the top 20% of the range


def test_3a_regime_and_exit_rules():
    df = daily()
    e = SwingBreakoutEngine({"exit_rule": "low10", "hold_days": None})
    x = e.calculate_indicators(df)
    b = x.iloc[-1].copy()
    e.trades = {"X": {"stop": float(b["low"]) * 0.5}}
    b["close"] = b["low10"] - 1
    b["ema"] = b["close"] - 5                       # above EMA but below the 10-day low
    assert e.manage_open_position({"symbol": "X", "bar": b, "days_held": 30})["reason"] == "close below 10-day low"
    b["close"] = b["low10"] + 1
    assert e.manage_open_position({"symbol": "X", "bar": b, "days_held": 30})["action"] == "hold"   # no time cap
    sig_row = x.iloc[-1].copy()
    sig_row[["hh20", "range_pos", "vol_ratio"]] = [sig_row["close"] - 1, 90, 3]
    sig_row["regime_ok"] = 0.0                      # NIFTY below its 50-day SMA
    assert SwingBreakoutEngine().generate_signal(sig_row) is None
