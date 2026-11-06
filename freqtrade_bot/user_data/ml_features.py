"""Features for the pump/dump model. Shared by training (ml_pumpdump.py) and the live bot (strategies/PumpML.py),
so both compute exactly the same inputs. Only hourly candles are needed (no funding, no coin age, no clock time).
"""
import numpy as np
import pandas as pd

FEATURES = ["jump", "vol_x", "closepos", "up_wick", "low_wick", "ret_prev1", "ret_4h", "ret_24h", "ret_72h",
            "ret_168h", "dist_hi24", "dist_lo24", "vola_24", "vola_168", "log_qv24", "qv_surge", "events_7d",
            "btc_1h", "btc_4h", "btc_24h", "breadth", "log_price"]
MIN_MOVE, MIN_VOLX, MIN_QV = 0.05, 3.0, 2_000_000
BREADTH_WINDOW = 480          # 20 days of hourly candles
MIN_CANDLES = 200


def pair_features(df: pd.DataFrame) -> pd.DataFrame:
    """Per-coin features on hourly candles (columns date, open, high, low, close, volume)."""
    o, h, lo, c, v = (df[x].to_numpy(float) for x in ("open", "high", "low", "close", "volume"))
    qv = v * c
    s = pd.Series
    f = pd.DataFrame({"date": df["date"].to_numpy()}, index=df.index)
    cols = {}
    cols["jump"] = c / o - 1
    cols["vol_x"] = qv / s(qv).rolling(48).mean().shift(1).to_numpy()
    rng = np.where(h > lo, h - lo, np.nan)
    cols["closepos"] = (c - lo) / rng
    cols["up_wick"] = (h - np.maximum(o, c)) / c
    cols["low_wick"] = (np.minimum(o, c) - lo) / c
    cs = s(c)
    cols["ret_prev1"] = (cs.shift(1) / cs.shift(2) - 1).to_numpy()
    for n in (4, 24, 72, 168):
        cols[f"ret_{n}h"] = (cs / cs.shift(n) - 1).to_numpy()
    cols["dist_hi24"] = c / s(h).rolling(24).max().shift(1).to_numpy() - 1
    cols["dist_lo24"] = c / s(lo).rolling(24).min().shift(1).to_numpy() - 1
    r1 = cs.pct_change()
    cols["vola_24"] = r1.rolling(24).std().to_numpy()
    cols["vola_168"] = r1.rolling(168).std().to_numpy()
    qv24 = s(qv).rolling(24).sum()
    cols["qv24"] = qv24.to_numpy()
    cols["log_qv24"] = np.log10(qv24.clip(lower=1)).to_numpy()
    cols["qv_surge"] = (qv24 / qv24.rolling(168).mean()).to_numpy()
    big = s((np.abs(cols["jump"]) >= MIN_MOVE).astype(float))
    cols["events_7d"] = big.rolling(168).sum().shift(1).to_numpy()
    cols["log_price"] = np.log10(np.maximum(c, 1e-12))
    return pd.concat([f, pd.DataFrame(cols, index=df.index)], axis=1)


def is_event(f: pd.DataFrame) -> pd.Series:
    return (f["jump"].abs() >= MIN_MOVE) & (f["vol_x"] >= MIN_VOLX) & (f["qv24"] >= MIN_QV)


def btc_features(btc: pd.DataFrame) -> pd.DataFrame:
    b = btc[["date", "close"]].copy()
    b["btc_1h"] = b["close"].pct_change(1)
    b["btc_4h"] = b["close"].pct_change(4)
    b["btc_24h"] = b["close"].pct_change(24)
    return b[["date", "btc_1h", "btc_4h", "btc_24h"]]


def breadth_from(closes: dict) -> pd.DataFrame:
    """Share of coins above their own 20-day average, per hour. closes: {pair: Series(close, index=date)}."""
    above, valid = None, None
    for c in closes.values():
        sma = c.rolling(BREADTH_WINDOW).mean()
        a = (c > sma).astype(float).where(sma.notna()).fillna(0)
        v = sma.notna().astype(float)
        above = a if above is None else above.add(a, fill_value=0)
        valid = v if valid is None else valid.add(v, fill_value=0)
    if above is None:
        return pd.DataFrame(columns=["date", "breadth"])
    out = pd.DataFrame({"breadth": above / valid, "coins": valid})
    out = out[out["coins"] >= 30].dropna()
    out.index.name = "date"
    return out.reset_index()[["date", "breadth"]]


def add_market(f: pd.DataFrame, btc: pd.DataFrame, breadth: pd.DataFrame) -> pd.DataFrame:
    """Merge BTC moves and market breadth (both known at the close of each candle) onto per-coin features."""
    dt = f["date"].dtype
    b = btc.copy()
    b["date"] = b["date"].astype(dt)
    br = breadth.copy()
    br["date"] = br["date"].astype(dt)
    f = pd.merge_asof(f.sort_values("date"), b.sort_values("date"), on="date", direction="backward")
    f = pd.merge_asof(f, br.sort_values("date"), on="date", direction="backward")
    return f
