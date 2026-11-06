"""Point-in-time indicators and meta-label features. Everything on row t uses data up to and including bar t's close
(the trade is entered at bar t+1's open). tests/test_lab.py checks this by truncating the data."""
import numpy as np
import pandas as pd

import config as C


def ema(s, n):
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def atr(d, n):
    pc = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


# windows per timeframe. Feature names keep the 4h labels: r6 = "about a day" on 4h / 5 days on 1d,
# r42 = 1 week on 4h / 20 days on 1d, r180 = 1 month on 4h / 90 days on 1d.
TF = {"4h": dict(rets=(1, 6, 42, 180), dvol_win=180, dvol_mult=6, min_bars=C.MIN_BARS),
      "1d": dict(rets=(1, 5, 20, 90), dvol_win=30, dvol_mult=1, min_bars=210)}


def coin_frame(b: pd.DataFrame, fund: pd.Series, cfg: dict) -> pd.DataFrame:
    w = TF[cfg.get("tf", "4h")]
    d = b.copy()
    c = d["close"]
    d["atr"] = atr(d, 14)
    d["atr100"] = atr(d, 100)
    n = cfg["donch"]
    d["dh"] = d["high"].rolling(n).max().shift(1)          # prior n bars, excludes bar t
    d["dl"] = d["low"].rolling(n).min().shift(1)
    d["ema20"], d["ema50"], d["ema200"] = ema(c, 20), ema(c, 50), ema(c, 200)
    d["dvol30"] = (d["volume"] * c).rolling(w["dvol_win"], min_periods=int(w["dvol_win"] * 2 / 3)).mean() * w["dvol_mult"]
    d["bars"] = np.arange(len(d))
    d["liquid"] = (d["dvol30"] >= C.MIN_DVOL) & (d["bars"] >= w["min_bars"])
    d["gate"] = True
    # funding: last rate stamped at or before the bar OPEN (conservative: never uses the rate settled at bar close)
    if len(fund):
        f = fund.reindex(d.index.union(fund.index)).ffill().reindex(d.index)
        f9 = fund.rolling(9, min_periods=3).mean().reindex(d.index.union(fund.index)).ffill().reindex(d.index)
        d["fund"], d["fund9"] = f.values, f9.values
    else:
        d["fund"], d["fund9"] = np.nan, np.nan

    # ---- features (prefix f_)
    lc = np.log(c)
    d["f_brk"] = (c - d["dh"]) / d["atr"]
    d["f_atrp"] = d["atr"] / c
    d["f_atr_ratio"] = d["atr"] / d["atr100"]
    for name, k in zip((1, 6, 42, 180), w["rets"]):
        d[f"f_r{name}"] = lc.diff(k)
    vs = d["volume"].rolling(50, min_periods=30).mean().shift(1)
    d["f_vol_ratio"] = d["volume"] / vs
    d["f_vol_ratio6"] = d["volume"].rolling(6).mean() / vs
    d["f_dvol"] = np.log10(d["dvol30"].clip(lower=1))
    rng = (d["high"] - d["low"]).replace(0, np.nan)
    d["f_clv"] = (c - d["low"]) / rng
    d["f_wick"] = (d["high"] - np.maximum(c, d["open"])) / rng
    d["f_ema50"] = (c - d["ema50"]) / d["atr"]
    d["f_ema200"] = (c - d["ema200"]) / d["atr"]
    d["f_rsi"] = rsi(c)
    d["f_base"] = (d["dh"] - d["dl"]) / c
    d["f_age"] = np.log1p(d["bars"])
    d["f_fund"] = d["fund"]
    d["f_fund9"] = d["fund9"]
    return d


def market_frame(frames: dict, ti: pd.DatetimeIndex) -> pd.DataFrame:
    """Cross-sectional and BTC regime features per bar (all point-in-time)."""
    btc = frames["BTC"].reindex(ti)
    m = pd.DataFrame(index=ti)
    m["m_btc_r42"] = np.log(btc["close"]).diff(42)
    m["m_btc_r180"] = np.log(btc["close"]).diff(180)
    m["m_btc_ema200"] = btc["close"] / btc["ema200"] - 1
    m["m_btc_atrp"] = btc["atr"] / btc["close"]
    above = pd.DataFrame({k: (f["close"] > f["ema50"]) & f["liquid"] for k, f in frames.items()}).reindex(ti)
    liq = pd.DataFrame({k: f["liquid"] for k, f in frames.items()}).reindex(ti).fillna(False)
    m["n_liquid"] = liq.sum(axis=1)
    m["n_liquid"] = m["n_liquid"].astype(float)
    m["m_breadth50"] = (above.fillna(False).sum(axis=1) / m["n_liquid"].replace(0, np.nan)).astype(float)
    # cross-sectional percentile ranks among the liquid coins on the same bar (stationary by construction)
    ranks = {}
    for col in ("f_r42", "f_r180", "f_dvol", "f_atrp"):
        w = pd.DataFrame({k: f[col].where(f["liquid"]) for k, f in frames.items()}).reindex(ti)
        ranks[f"x_rank_{col[2:]}"] = w.rank(axis=1, pct=True)     # wide (bar x coin); looked up per trade
    return m, ranks


FEATURES_BASE = ["f_brk", "f_atrp", "f_atr_ratio", "f_r1", "f_r6", "f_r42", "f_r180", "f_vol_ratio", "f_vol_ratio6",
                 "f_dvol", "f_clv", "f_wick", "f_ema50", "f_ema200", "f_rsi", "f_base", "f_age", "f_fund", "f_fund9",
                 "m_btc_r42", "m_btc_r180", "m_btc_ema200", "m_btc_atrp", "m_breadth50", "m_nsig", "f_rs42"]
# iteration 2+: f_age removed (the data starts Nov 2021, so for old coins "age" is just the calendar date: a regime
# memoriser); cross-sectional ranks added
FEATURES_V2 = [f for f in FEATURES_BASE if f != "f_age"] + ["x_rank_r42", "x_rank_r180", "x_rank_dvol", "x_rank_atrp"]
# iteration 3+: trade-specific only. The BTC-regime / breadth features dominated every model and only taught it which
# past quarters were good; the regime is now a fixed, pre-set gate in the primary signal instead.
FEATURES_V3 = [f for f in FEATURES_V2 if not f.startswith("m_")]
