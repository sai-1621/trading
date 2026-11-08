"""PumpML15: paper copy of PumpML with the "1.5% bar, strongest first" rule (see PREREG_forward_1p5.md).

Same model, events, exits and leverage as PumpML. Two differences:
  * it trades only when the better side's predicted return is at least 1.5% (PumpML: 0.5%);
  * when more signals arrive in one hour than there are free slots, the highest predictions get them.
    Freqtrade analyses every coin first and then enters in list order, so confirm_trade_entry lets a coin in only
    if it ranks inside the free slots among this hour's signals not yet open.
It does not write pumpml_events.jsonl (PumpML already logs every event).

Live-market trial (from 6 Nov 2026, paper), features from the freqtrade docs, settings fixed in advance, not tuned:
  * stop-loss held on the exchange (config-pumpml15.json: stoploss_on_exchange), so a stop still works if the PC or
    the bot stops; freqtrade simulates these orders in dry-run;
  * protections below: pause one side after 3 stop-outs on it in 24 h, pause everything after an 8% drawdown in a
    week, and no re-entry into a coin for 2 hours after leaving it.
These make the bot differ from the pre-registered shadow test (forward_shadow.py), which stays the official measure.
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

    @property
    def protections(self):
        return [
            # 3 losing stop-outs on one side (long or short) within 24 h -> that side pauses for 12 h
            {"method": "StoplossGuard", "lookback_period_candles": 24, "trade_limit": 3, "stop_duration_candles": 12,
             "required_profit": 0.0, "only_per_pair": False, "only_per_side": True},
            # account down 8% from its peak over the last 7 days (at least 5 trades) -> all trading pauses for 24 h
            {"method": "MaxDrawdown", "calculation_mode": "equity", "lookback_period_candles": 168, "trade_limit": 5,
             "stop_duration_candles": 24, "max_allowed_drawdown": 0.08},
            # no new trade in a coin for 2 h after a trade in it closed
            {"method": "CooldownPeriod", "stop_duration_candles": 2},
        ]

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
