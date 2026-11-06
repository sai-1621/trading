"""Shared helper: BTC daily trend filter (only buy coins while BTC is above its 200-day average)."""
import pandas as pd


def add_btc_filter(strategy, df: pd.DataFrame) -> pd.DataFrame:
    btc = strategy.dp.get_pair_dataframe("BTC/USDT", "1d")[["date", "close"]].copy()
    btc["btc_ok"] = (btc["close"] > btc["close"].rolling(200).mean()).astype(int)
    # Daily candle is only known after it closes: shift one day so a 4h candle never sees today's close.
    btc["date"] = (btc["date"] + pd.Timedelta(days=1)).astype(df["date"].dtype)
    df = pd.merge_asof(df.sort_values("date"), btc[["date", "btc_ok"]].sort_values("date"), on="date", direction="backward")
    df["btc_ok"] = df["btc_ok"].fillna(0)
    return df
