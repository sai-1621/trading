"""Causal (no look-ahead) indicators on intraday bars.

Input: DataFrame indexed by IST timestamps (bar START times), columns open, high, low, close, volume.
A value on bar t uses only bars <= t, so it is known when bar t closes; act on it from bar t+1.
tests/test_no_lookahead.py checks this by recomputing on truncated data.
"""
import numpy as np
import pandas as pd


def _day(df):
    return pd.Series(df.index.date, index=df.index)


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP anchored at each session's first bar (09:15). Days with no volume (index data) use equal weights."""
    day = _day(df)
    tp = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"].fillna(0).astype(float)
    no_vol = vol.groupby(day).transform("sum") <= 0
    w = vol.where(~no_vol, 1.0)
    return (tp * w).groupby(day).cumsum() / w.groupby(day).cumsum()


def true_range(df: pd.DataFrame) -> pd.Series:
    """True range; the first bar of each day uses its own open instead of yesterday's close (no overnight gap)."""
    day = _day(df)
    pc = df["close"].shift(1).where(day.eq(day.shift(1)), df["open"])
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def rma(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return rma(true_range(df), n)


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    tr = rma(true_range(df), n)
    pdi = 100 * rma(plus, n) / tr
    mdi = 100 * rma(minus, n) / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return rma(dx.fillna(0), n)


def opening_range(df: pd.DataFrame, minutes: int = 15) -> pd.DataFrame:
    """High / low of the first `minutes` of each session, available only on bars that start after the range."""
    day = _day(df)
    mins = df.index.hour * 60 + df.index.minute - (9 * 60 + 15)
    inside = pd.Series(mins < minutes, index=df.index)
    hi = df["high"].where(inside).groupby(day).transform("max")
    lo = df["low"].where(inside).groupby(day).transform("min")
    after = ~inside
    return pd.DataFrame({"or_high": hi.where(after), "or_low": lo.where(after)})


def daily_from_intraday(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby(df.index.date)
    d = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                      "close": g["close"].last(), "volume": g["volume"].sum()})
    d.index = pd.to_datetime(d.index)
    return d


def prior_daily_atr(df: pd.DataFrame, daily: pd.DataFrame | None = None, n: int = 14) -> pd.Series:
    """Daily ATR(n) from completed days only, mapped onto each intraday bar."""
    d = daily if daily is not None else daily_from_intraday(df)
    pc = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1)
    a = rma(tr, n).shift(1)  # yesterday's value: today's range is not known yet
    a.index = pd.to_datetime(a.index).date
    return pd.Series(pd.Index(df.index.date).map(a), index=df.index, dtype=float)


def prior_close(df: pd.DataFrame) -> pd.Series:
    d = df.groupby(df.index.date)["close"].last().shift(1)
    return pd.Series(pd.Index(df.index.date).map(d), index=df.index, dtype=float)


def bar_return_std(df: pd.DataFrame, n: int = 20) -> pd.Series:
    """Rolling std of 5-minute log returns within sessions (first bar of a day excluded)."""
    day = _day(df)
    r = np.log(df["close"]).diff().where(day.eq(day.shift(1)))
    return r.rolling(n, min_periods=n // 2).std()


def rolling_high(s: pd.Series, n: int) -> pd.Series:
    """Highest value of the previous n bars (excludes the current bar)."""
    return s.shift(1).rolling(n).max()


def rolling_low(s: pd.Series, n: int) -> pd.Series:
    return s.shift(1).rolling(n).min()
