"""Faster trend: 4-hour EMA 20 crosses above EMA 50 with ADX > 25, only while BTC is above its 200-day average.
Exit when EMA 20 crosses back below EMA 50. Typical hold: days."""
import talib.abstract as ta
from freqtrade.strategy import IStrategy
from pandas import DataFrame

from _common import add_btc_filter


class EmaTrend4h(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "4h"
    can_short = False
    minimal_roi = {"0": 100}
    stoploss = -0.12
    startup_candle_count = 100
    process_only_new_candles = True

    def informative_pairs(self):
        return [("BTC/USDT", "1d")]

    def populate_indicators(self, df: DataFrame, metadata: dict) -> DataFrame:
        df["ema20"] = ta.EMA(df, timeperiod=20)
        df["ema50"] = ta.EMA(df, timeperiod=50)
        df["adx"] = ta.ADX(df, timeperiod=14)
        return add_btc_filter(self, df)

    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        up = (df["ema20"] > df["ema50"]) & (df["ema20"].shift(1) <= df["ema50"].shift(1))
        df.loc[up & (df["adx"] > 25) & (df["btc_ok"] == 1) & (df["volume"] > 0), "enter_long"] = 1
        return df

    def populate_exit_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        df.loc[df["ema20"] < df["ema50"], "exit_long"] = 1
        return df
