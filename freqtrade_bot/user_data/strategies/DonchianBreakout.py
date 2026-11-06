"""Turtle-style breakout: buy a close above the previous 55-day high (BTC above its 200-day average),
sell a close below the previous 20-day low. Checked once a day."""
from freqtrade.strategy import IStrategy
from pandas import DataFrame

from _common import add_btc_filter


class DonchianBreakout(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1d"
    can_short = False
    minimal_roi = {"0": 100}
    stoploss = -0.25
    startup_candle_count = 210
    process_only_new_candles = True

    def informative_pairs(self):
        return [("BTC/USDT", "1d")]

    def populate_indicators(self, df: DataFrame, metadata: dict) -> DataFrame:
        df["hi55"] = df["high"].rolling(55).max().shift(1)
        df["lo20"] = df["low"].rolling(20).min().shift(1)
        return add_btc_filter(self, df)

    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        df.loc[(df["close"] > df["hi55"]) & (df["btc_ok"] == 1) & (df["volume"] > 0), "enter_long"] = 1
        return df

    def populate_exit_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        df.loc[df["close"] < df["lo20"], "exit_long"] = 1
        return df
