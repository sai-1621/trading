"""PumpML15: paper copy of PumpML with the "1.5% bar, strongest first" rule (see PREREG_forward_1p5.md).

Same model, events, exits and leverage as PumpML. Two differences:
  * it trades only when the better side's predicted return is at least 1.5% (PumpML: 0.5%);
  * when more signals arrive in one hour than there are free slots, the highest predictions get them.
    Freqtrade analyses every coin first and then enters in list order, so confirm_trade_entry lets a coin in only
    if it ranks inside the free slots among this hour's signals not yet open.
It does not write pumpml_events.jsonl (PumpML already logs every event).
"""
import sys
from datetime import datetime
from pathlib import Path

from freqtrade.persistence import Trade
from pandas import DataFrame

sys.path.insert(0, str(Path(__file__).resolve().parent))
from PumpML import PumpML  # noqa: E402


class PumpML15(PumpML):
    MIN_PRED = 0.015

    def bot_start(self, **kwargs) -> None:
        super().bot_start(**kwargs)
        self.min_pred = self.MIN_PRED
        self._signals = {}                    # pair -> (candle date, best prediction) for the latest closed candle

    def _log_event(self, df: DataFrame, pair: str) -> None:
        return

    def populate_indicators(self, df: DataFrame, metadata: dict) -> DataFrame:
        df = super().populate_indicators(df, metadata)
        if len(df):
            last = df.iloc[-1]
            best = max(last["pred_long"], last["pred_short"])
            if best == best and best >= self.min_pred:          # not NaN
                self._signals[metadata["pair"]] = (last["date"], float(best))
            else:
                self._signals.pop(metadata["pair"], None)
        return df

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float, time_in_force: str,
                            current_time: datetime, entry_tag, side: str, **kwargs) -> bool:
        try:
            mine = self._signals.get(pair)
            if mine is None:
                return True
            open_pairs = {t.pair for t in Trade.get_trades_proxy(is_open=True)}
            free = int(self.config.get("max_open_trades", 5)) - len(open_pairs)
            if free <= 0:
                return False
            cands = sorted(((p, s[1]) for p, s in self._signals.items()
                            if s[0] == mine[0] and p not in open_pairs), key=lambda x: -x[1])
            rank = next((i for i, (p, _) in enumerate(cands) if p == pair), 0)
            return rank < free
        except Exception:  # noqa: BLE001
            return True
