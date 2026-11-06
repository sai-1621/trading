"""Strategy 3: NSE F&O stocks - relative strength + opening volume (+ optional delivery) breakout.
09:30 scan of the 50 most liquid F&O stocks: rank by strength vs NIFTY 50 and by opening volume ratio, then
trade an opening-range breakout in the direction of the ranking, intraday (or 3-day swing for longs).
Spec: STRATEGIES.md, strategy 3.
"""
import math

import numpy as np
import pandas as pd

import config as C
import costs
import indicators as ind
from engines.base import Signal, State, Trade, in_window, manage_directional


class StrategyEngine:
    name = "fno_rs_breakout"

    def __init__(self, cfg: dict | None = None, capital: float = C.CAPITAL):
        self.cfg = dict(C.S3 if cfg is None else cfg)
        self.capital = capital
        self.watch: dict[str, str] = {}        # symbol -> "long" | "short" chosen at 09:30
        self.trades: dict[str, Trade] = {}
        self.done: set[str] = set()            # one trade per stock per day
        self.state = State.FLAT

    # ------------------------------------------------------------------ 1. indicators
    def calculate_indicators(self, df: pd.DataFrame, daily: pd.DataFrame | None = None) -> pd.DataFrame:
        """df: 5-minute bars of ONE stock (or NIFTY). Adds VWAP, ATR, opening range, prior close and the
        opening-volume ratio (09:15-09:30 volume / average of the same window over the previous 10 sessions)."""
        out = df.copy()
        out["vwap"] = ind.session_vwap(df)
        out["atr"] = ind.atr(df, 14)
        out = out.join(ind.opening_range(df, 15))
        out["prev_close"] = ind.prior_close(df)
        mins = df.index.hour * 60 + df.index.minute - (9 * 60 + 15)
        ov = df["volume"].where(mins < 15).groupby(df.index.date).sum()
        ratio = ov / ov.shift(1).rolling(10, min_periods=5).mean()
        r = pd.Index(df.index.date).map(ratio).astype(float)
        out["open_vol_ratio"] = np.where(mins >= 10, r, np.nan)   # known when the 09:25 bar closes (09:30)
        out["datr"] = ind.prior_daily_atr(df, daily, 14)
        return out

    # ------------------------------------------------------------------ 09:30 scan
    def rank_universe(self, snap: pd.DataFrame, nifty_ret_pct: float, nifty_or_pct: float,
                      vix: float | None = None, nifty_above_vwap: bool | None = None,
                      delivery_ratio: pd.Series | None = None) -> pd.DataFrame:
        """snap: one row per stock at the 09:25 bar close (columns close, prev_close, open_vol_ratio, ...).
        RS = stock % change since yesterday's close minus NIFTY's. score = z(RS) * sign + z(log volume ratio).
        Returns the picks with a 'side' column; empty in a bad regime."""
        c = self.cfg
        self.watch = {}
        if nifty_or_pct > c["nifty_or_max_pct"] or (vix is not None and vix > c["vix_max"]):
            return snap.iloc[0:0]
        s = snap.dropna(subset=["close", "prev_close", "open_vol_ratio"]).copy()
        s["ret"] = (s["close"] / s["prev_close"] - 1) * 100
        s["rs"] = s["ret"] - nifty_ret_pct
        s["lvr"] = np.log(s["open_vol_ratio"].clip(lower=1e-6))
        if c["use_delivery"] and delivery_ratio is not None:
            s["deliv"] = delivery_ratio.reindex(s.index)
        z = lambda x: (x - x.mean()) / (x.std() or 1)  # noqa: E731
        s["z_rs"], s["z_vol"] = z(s["rs"]), z(s["lvr"])
        vol_ok = s["open_vol_ratio"] >= c["min_vol_ratio"]
        longs = s[vol_ok & (s["rs"] >= c["min_rs_pct"])].assign(score=lambda x: x["z_rs"] + x["z_vol"])
        shorts = s[vol_ok & (s["rs"] <= -c["min_rs_pct"])].assign(score=lambda x: -x["z_rs"] + x["z_vol"])
        if c["use_delivery"] and "deliv" in s:
            longs = longs[longs["deliv"] >= c["delivery_ratio_min"]]
        if nifty_above_vwap is False:
            longs = longs.iloc[0:0]
        if nifty_above_vwap is True:
            shorts = shorts.iloc[0:0]
        if c["mode"] == "swing3":
            shorts = shorts.iloc[0:0]          # no overnight shorts in cash equity
        picks = pd.concat([longs.nlargest(c["picks_long"], "score").assign(side="long"),
                           shorts.nlargest(c["picks_short"], "score").assign(side="short")])
        self.watch = picks["side"].to_dict()
        return picks

    # ------------------------------------------------------------------ 2. signal
    def generate_signal(self, current_bar: pd.Series, option_chain=None) -> Signal | None:
        """current_bar: a row of calculate_indicators() for one stock, with current_bar['symbol'] set.
        option_chain is unused (stocks trade in cash/MIS); kept for the common interface."""
        c, b, ts = self.cfg, current_bar, current_bar.name
        sym = b["symbol"]
        side = self.watch.get(sym)
        if side is None or sym in self.trades or sym in self.done:
            return None
        if not in_window(ts + pd.Timedelta(minutes=5), "09:35", c["last_entry"]):
            return None
        if any(pd.isna(b.get(k)) for k in ("or_high", "or_low", "vwap", "atr")):
            return None
        if side == "long" and not (b["close"] > b["or_high"] and b["close"] > b["vwap"]):
            return None
        if side == "short" and not (b["close"] < b["or_low"] and b["close"] < b["vwap"]):
            return None
        d = 1 if side == "long" else -1
        px = float(b["close"])
        stop_pts = float(np.clip(c["stop_atr"] * b["atr"], c["stop_min_pct"] / 100 * px, c["stop_max_pct"] / 100 * px))
        or_stop = b["or_low"] if side == "long" else b["or_high"]
        stop_pts = min(stop_pts, max(abs(px - or_stop), c["stop_min_pct"] / 100 * px))   # tighter of ATR / OR
        sig = Signal(side, ts, px, px - d * stop_pts, px + d * c["target_r"] * stop_pts,
                     f"RS breakout {side}", instrument={"symbol": sym}, risk_per_unit=stop_pts)
        sig.qty = self.compute_position_size(self.capital, stop_pts, price=px)
        return sig if sig.qty > 0 else None

    # ------------------------------------------------------------------ 3. sizing
    def compute_position_size(self, capital: float, stop_loss_points: float, price: float = 0.0,
                              room: float | None = None) -> int:
        """Shares. Risk per trade = daily loss limit / max trades per day, charges included; the stop is
        ATR-based, so position size shrinks automatically when the stock is more volatile.
        Notional capped at max_position_capital_pct of (capital x MIS leverage)."""
        c = self.cfg
        budget = capital * C.MAX_DAILY_LOSS_PCT / 100 / C.MAX_TRADES_PER_DAY
        if room is not None:
            budget = min(budget, room)
        if stop_loss_points <= 0 or price <= 0:
            return 0
        lev = c["mis_leverage"] if c["mode"] == "intraday" else 1.0
        cap_qty = math.floor(capital * lev * c["max_position_capital_pct"] / 100 / price)
        cost_unit = costs.equity_round_trip(price, price, 100, delivery=c["mode"] != "intraday").total / 100
        qty = math.floor(budget / (stop_loss_points + cost_unit))
        return max(min(qty, cap_qty), 0)

    # ------------------------------------------------------------------ 4. open positions
    def on_entry(self, sig: Signal, bar_index: int = 0):
        sym = sig.instrument["symbol"]
        self.trades[sym] = Trade(sig, bar_index, sig.stop, (1 if sig.side == "long" else -1) * sig.underlying)

    def manage_open_position(self, tick_data: dict) -> dict:
        """tick_data: {'symbol', 'ts', 'price', 'bar_close', 'atr'}. Intraday: square-off 15:15.
        swing3: a long that is >= 1R up at 15:10 is carried (see carry_decision); others exit at 15:15."""
        sym = tick_data["symbol"]
        tr = self.trades.get(sym)
        if tr is None:
            return {"action": "none", "reason": "no open position"}
        sq = "23:59" if tick_data.get("carried") else C.SQUARE_OFF
        act = manage_directional(tr, tick_data["ts"], tick_data["price"], tick_data.get("bar_close", False),
                                 tick_data.get("atr", math.nan), self.cfg, square_off=sq)
        if act["action"] == "exit":
            self.trades.pop(sym)
            self.done.add(sym)
        return act

    def carry_decision(self, sym: str, price: float, day_high: float, day_low: float) -> bool:
        """swing3 only: carry a long overnight when it is >= 1R in profit and closes in the top 25% of its range."""
        tr = self.trades.get(sym)
        if self.cfg["mode"] != "swing3" or tr is None or tr.sig.side != "long":
            return False
        r = abs(tr.sig.underlying - tr.sig.stop)
        top = day_low + 0.75 * (day_high - day_low)
        return price - tr.sig.underlying >= r and price >= top

    def new_day(self, keep_carried: bool = False):
        self.watch, self.done = {}, set()
        if not keep_carried:
            self.trades = {}
