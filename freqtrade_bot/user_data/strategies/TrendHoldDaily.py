"""Slow trend: hold a coin while it is above its 50- and 200-day averages and BTC is above its 200-day average.
Checked once a day. Typical hold: weeks to months."""
from freqtrade.strategy import IStrategy
from pandas import DataFrame

from _common import add_btc_filter


class TrendHoldDaily(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1d"
    can_short = False
    minimal_roi = {"0": 100}          # no fixed profit target: let winners run
    stoploss = -0.30                  # disaster stop only
    startup_candle_count = 210
    process_only_new_candles = True

    def informative_pairs(self):
        return [("BTC/USDT", "1d")]

    def populate_indicators(self, df: DataFrame, metadata: dict) -> DataFrame:
        df["sma50"] = df["close"].rolling(50).mean()
        df["sma200"] = df["close"].rolling(200).mean()
        return add_btc_filter(self, df)

    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        df.loc[(df["close"] > df["sma50"]) & (df["sma50"] > df["sma200"]) & (df["btc_ok"] == 1)
               & (df["volume"] > 0), "enter_long"] = 1
        return df

    def populate_exit_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        df.loc[(df["close"] < df["sma50"]) | (df["btc_ok"] == 0), "exit_long"] = 1
        return df
