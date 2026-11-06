import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
import features as F  # noqa: E402
import model  # noqa: E402
import primary  # noqa: E402


def _bars(n=600, seed=0):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + rng.uniform(0, 0.01, n))
    lo = np.minimum(o, c) * (1 - rng.uniform(0, 0.01, n))
    idx = pd.date_range("2022-01-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": rng.uniform(1e5, 2e5, n)}, index=idx)


CFG = dict(signal="donchian", donch=20, stop_atr=2.0, target_atr=3.0, max_bars=30, exit="barrier")
CFG2 = dict(signal="donchian", donch=20, stop_atr=2.0, target_atr=None, trail_atr=3.0, max_bars=60, exit="chandelier")


def test_features_have_no_lookahead():
    b = _bars()
    fund = pd.Series(0.0001, index=pd.date_range("2022-01-01", periods=300, freq="8h", tz="UTC"))
    full = F.coin_frame(b, fund, CFG)
    cut = F.coin_frame(b.iloc[:400], fund, CFG)
    cols = [c for c in full.columns if c.startswith("f_")] + ["dh", "atr", "liquid"]
    pd.testing.assert_frame_equal(full[cols].iloc[:400], cut[cols], check_freq=False)


def test_trade_uses_next_open_and_costs():
    b = _bars()
    d = F.coin_frame(b, pd.Series(dtype=float), CFG)
    d["liquid"] = True
    idx = primary.signals(d, CFG)
    tr = primary.simulate(d, pd.Series(dtype=float), idx[:5], CFG)
    for t in tr:
        assert t["e"] == t["i"] + 1 and t["entry"] == d["open"].iloc[t["e"]]
        assert t["net"] < t["gross"]              # friction always costs
        assert t["t_out"] > t["t_in"]


def test_stop_before_target_same_bar():
    b = _bars(300)
    d = F.coin_frame(b, pd.Series(dtype=float), CFG)
    i = 250
    e = i + 1
    d.iloc[e, d.columns.get_loc("high")] = d["open"].iloc[e] * 2
    d.iloc[e, d.columns.get_loc("low")] = d["open"].iloc[e] * 0.5
    tr = primary.simulate(d, pd.Series(dtype=float), np.array([i]), CFG)
    assert tr[0]["why"] == "stop"


def test_walk_forward_purges_overlapping_labels():
    rng = np.random.default_rng(1)
    n = 6000
    t_in = pd.date_range("2022-01-01", periods=n, freq="6h", tz="UTC")
    df = pd.DataFrame({"t_sig": t_in - pd.Timedelta(hours=4), "t_in": t_in, "t_out": t_in + pd.Timedelta(days=5),
                       "net": rng.normal(0, 0.05, n), "f_a": rng.normal(size=n), "f_b": rng.normal(size=n)})
    df["y"] = (df["net"] > 0).astype(int)
    seen = {}
    orig = model._fit

    def spy(X, y):
        seen.setdefault("max_out", []).append(df.loc[X.index, "t_out"].max())
        return orig(X, y)

    model._fit = spy
    try:
        res, log, _ = model.walk_forward(df, ["f_a", "f_b"], pd.Timestamp("2024-01-01", tz="UTC"),
                                         pd.Timestamp("2024-07-01", tz="UTC"))
    finally:
        model._fit = orig
    assert set(res["fold"]) == {"2024-01", "2024-04"}
    # the last model fit for the 2024-04 fold must not include any trade closing within a day of 2024-04-01
    assert max(seen["max_out"]) < pd.Timestamp("2024-04-01", tz="UTC") - C.EMBARGO


def test_chandelier_only_ratchets_up_and_uses_past_bars():
    b = _bars(400, seed=3)
    d = F.coin_frame(b, pd.Series(dtype=float), CFG2)
    d["liquid"] = True
    idx = primary.signals(d, CFG2)
    full = primary.simulate(d, pd.Series(dtype=float), idx[:10], CFG2)
    for t in full:
        assert t["why"] in ("stop", "stop_gap", "trail_stop", "time")
        # truncating data right after the exit bar must not change the trade
        cut = primary.simulate(d.iloc[:t["x"] + 1 + 60], pd.Series(dtype=float), np.array([t["i"]]), CFG2)
        if cut:
            assert cut[0]["x"] == t["x"] and cut[0]["exit"] == t["exit"]
