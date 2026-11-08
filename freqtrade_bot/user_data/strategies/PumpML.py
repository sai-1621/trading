"""PumpML: paper experiment. On every closed hour, any Binance USDT futures coin that moved at least 5% on at least
3x its normal volume is scored by two trained models (user_data/models/pumpml.joblib, built by
user_data/ml_pumpdump.py --save): the expected net return of going long and of going short after the move.
It trades the better side only if that prediction is at least 0.5%.

Exits are the ones the model was trained on:
  long : take profit +8%, stop -8%     short: take profit +6% (price -6%), stop -10% (price +10%)
  both : out after 24 hours.
Leverage: "pumpml_leverage" in the config (default 1; capped by the coin's maximum on Binance). Take-profit and
stop levels above are PRICE moves at any leverage, so the bot exits at the same prices the model was trained on;
profit and loss on the margin are that move times the leverage. A leveraged config must also set
"stoploss" to -0.10 x leverage (freqtrade measures the safety stop on the margin). Market mood (share of coins above their 20-day average) is computed live from all coins once an
hour; in backtests it comes from user_data/breadth_1h.feather.
"""
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from freqtrade.enums import RunMode
from freqtrade.strategy import IStrategy, stoploss_from_open
from pandas import DataFrame

USER_DATA = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(USER_DATA))
from ml_features import BREADTH_WINDOW, add_market, breadth_from, btc_features, is_event, pair_features  # noqa: E402

logger = logging.getLogger(__name__)
BTC = "BTC/USDT:USDT"
# Every scored event (traded or not) is appended here, for forward shadow tests (user_data/forward_shadow.py).
EVENT_LOG = USER_DATA / "pumpml_events.jsonl"


class PumpML(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = True
    process_only_new_candles = True
    startup_candle_count = BREADTH_WINDOW + 30

    minimal_roi = {"0": 10}
    stoploss = -0.10
    use_custom_stoploss = True
    trailing_stop = False

    LONG_TP, LONG_SL = 0.08, 0.08
    SHORT_TP, SHORT_SL = 0.06, 0.10
    MAX_HOURS = 24

    def bot_start(self, **kwargs) -> None:
        self.lev = float(self.config.get("pumpml_leverage", 1.0))
        b = joblib.load(USER_DATA / "models" / "pumpml.joblib")
        self.feats, self.min_pred = b["features"], b["min_pred"]
        self.m_long, self.m_short = b["long"], b["short"]
        self.model_trained = b.get("trained_at")
        # head-to-head (PREREG_h2h.md): the old frozen model also scores every event, logged only, never traded
        self.m_cmp = None
        try:
            c = joblib.load(USER_DATA / "models" / "compare_old.joblib")
            if c["features"] == self.feats:
                self.m_cmp = (c["long"], c["short"], c.get("trained_at"))
        except Exception:  # noqa: BLE001
            self.m_cmp = None
        self._breadth = None
        self._breadth_key = None
        self._logged = set()
        # 1.5% rule as a virtual wallet (replaces the separate PumpML15 bot, see user_data/virtual_book.py)
        self._vbook = None
        if (type(self).__name__ == "PumpML" and self.dp.runmode in (RunMode.LIVE, RunMode.DRY_RUN)
                and self.config.get("pumpml15_virtual", True)):
            try:
                from virtual_book import VirtualBook
                self._vbook = VirtualBook(str(USER_DATA / "pumpml15_virtual.json"))
                if not self._vbook.s["started"]:
                    self._vbook.s["started"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                self._vbook.save()
            except Exception:  # noqa: BLE001
                logger.exception("virtual 1.5%% book disabled")
                self._vbook = None

    def _vbook_tick(self, now: datetime) -> None:
        """Run the virtual 1.5% book. Never lets an error reach trading."""
        vb = getattr(self, "_vbook", None)
        if vb is None:
            return
        try:
            changed = False
            hour = now.replace(minute=0, second=0, microsecond=0)
            if vb.last_candle_check != hour:          # stop wicks in closed hourly candles
                vb.last_candle_check = hour
                for t in vb.open_trades():
                    df = self.dp.get_pair_dataframe(t["pair"], self.timeframe)
                    if df is None or not len(df):
                        continue
                    for r in df.tail(26).itertuples():
                        if r.date + timedelta(hours=1) <= now:
                            vb.check_candle(t, r.date.to_pydatetime(), float(r.high), float(r.low), now)
                changed = True
            if vb.last_poll is None or (now - vb.last_poll).total_seconds() >= 15:
                vb.last_poll = now
                for t in vb.open_trades():
                    px = (self.dp.ticker(t["pair"]) or {}).get("last")
                    if px:
                        vb.check_price(t, float(px), now)
                        changed = changed or not t["is_open"]
            if vb.signals:
                latest = max(vb.signals)
                cand = datetime.strptime(latest, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                vb.enter(now, cand, lambda p: (self.dp.ticker(p) or {}).get("last"))
                changed = True
            if changed:
                vb.save()
        except Exception:  # noqa: BLE001
            logger.exception("virtual 1.5%% book tick failed")

    def informative_pairs(self):
        return [(BTC, self.timeframe)]

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, entry_tag, side, **kw):
        return max(1.0, min(getattr(self, "lev", 1.0), max_leverage))

    # market mood: once per new candle, from every coin the bot follows
    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
        if self.dp.runmode not in (RunMode.LIVE, RunMode.DRY_RUN):
            return
        self._vbook_tick(current_time)
        key = current_time.replace(minute=0, second=0, microsecond=0)
        if self._breadth_key == key:
            return
        closes = {}
        for pair in self.dp.current_whitelist():
            df = self.dp.get_pair_dataframe(pair, self.timeframe)
            if df is not None and len(df) > BREADTH_WINDOW:
                closes[pair] = df.set_index("date")["close"]
        self._breadth = breadth_from(closes)
        self._breadth_key = key

    def _breadth_now(self):
        if self.dp.runmode in (RunMode.LIVE, RunMode.DRY_RUN):
            return self._breadth if self._breadth is not None else pd.DataFrame(columns=["date", "breadth"])
        if not hasattr(self, "_bt_breadth"):
            self._bt_breadth = pd.read_feather(USER_DATA / "breadth_1h.feather")[["date", "breadth"]]
        return self._bt_breadth

    def populate_indicators(self, df: DataFrame, metadata: dict) -> DataFrame:
        f = pair_features(df)
        btc = btc_features(self.dp.get_pair_dataframe(BTC, self.timeframe))
        f = add_market(f, btc, self._breadth_now())
        f["event"] = is_event(f)
        f["pred_long"] = np.nan
        f["pred_short"] = np.nan
        rows = f.index[f["event"]]
        if len(rows):
            X = f.loc[rows, self.feats]
            f.loc[rows, "pred_long"] = self.m_long.predict(X)
            f.loc[rows, "pred_short"] = self.m_short.predict(X)
        f["pred_long_old"] = np.nan
        f["pred_short_old"] = np.nan
        if getattr(self, "m_cmp", None) is not None and len(rows):
            try:
                last = rows[-1:]                           # only the latest candle is logged
                f.loc[last, "pred_long_old"] = self.m_cmp[0].predict(f.loc[last, self.feats])
                f.loc[last, "pred_short_old"] = self.m_cmp[1].predict(f.loc[last, self.feats])
            except Exception:  # noqa: BLE001
                pass
        for c in ("event", "pred_long", "pred_short", "pred_long_old", "pred_short_old", "jump", "vol_x", "breadth"):
            df[c] = f[c].to_numpy()
        self._log_event(df, metadata["pair"])
        vb = getattr(self, "_vbook", None)
        if vb is not None and len(df):
            try:
                last = df.iloc[-1]
                if bool(last["event"]) and not pd.isna(last["pred_long"]):
                    vb.offer(metadata["pair"], last["date"].to_pydatetime(), last["pred_long"], last["pred_short"],
                             last["close"])
            except Exception:  # noqa: BLE001
                pass
        return df

    def _log_event(self, df: DataFrame, pair: str) -> None:
        """Append the latest closed candle to EVENT_LOG if it is a scored event. Never lets an error reach trading."""
        try:
            if self.dp.runmode not in (RunMode.LIVE, RunMode.DRY_RUN) or not len(df):
                return
            last = df.iloc[-1]
            if not bool(last["event"]) or pd.isna(last["pred_long"]):
                return
            key = (pair, str(last["date"]))
            if key in self._logged:
                return
            self._logged.add(key)
            rec = {"pair": pair, "candle": str(last["date"]), "entry_time": str(last["date"] + timedelta(hours=1)),
                   "close": float(last["close"]), "pred_long": float(last["pred_long"]),
                   "pred_short": float(last["pred_short"]), "logged_at": datetime.utcnow().isoformat() + "Z",
                   "model": getattr(self, "model_trained", None)}
            if "pred_long_old" in last and not pd.isna(last["pred_long_old"]):
                rec |= {"pred_long_old": float(last["pred_long_old"]), "pred_short_old": float(last["pred_short_old"]),
                        "model_old": self.m_cmp[2] if self.m_cmp else None}
            with open(EVENT_LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
        except Exception:  # noqa: BLE001
            pass

    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        pl, ps = df["pred_long"], df["pred_short"]
        df.loc[(pl >= self.min_pred) & (pl >= ps.fillna(-1)), ["enter_long", "enter_tag"]] = (1, "ml_long")
        df.loc[(ps >= self.min_pred) & (ps > pl.fillna(-1)), ["enter_short", "enter_tag"]] = (1, "ml_short")
        return df

    def populate_exit_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        return df

    def custom_stoploss(self, pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs):
        # Fixed stop measured from the ENTRY price, as in the walk-forward test. (Returning a constant here would be
        # measured from the current price, which freqtrade turns into a trailing stop.)
        # stoploss_from_open wants the stop on the margin (price move x leverage)
        lev = trade.leverage or 1.0
        sl = self.SHORT_SL if trade.is_short else self.LONG_SL
        return stoploss_from_open(-sl * lev, current_profit, is_short=trade.is_short, leverage=lev) or None

    def custom_exit(self, pair: str, trade, current_time: datetime, current_rate: float, current_profit: float, **kw):
        # current_profit is on the margin: divide by the leverage to get the price move
        if current_profit / (trade.leverage or 1.0) >= (self.SHORT_TP if trade.is_short else self.LONG_TP):
            return "take_profit"
        if (current_time - trade.open_date_utc).total_seconds() >= self.MAX_HOURS * 3600:
            return "time_limit"
        return None
