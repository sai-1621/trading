"""Strategy 3A: swing breakout on NSE F&O stocks (replaces the 09:30 intraday breakout).

Near-close (15:15) daily scan: close above the 20-day high, close in the top 20% of the day's range, volume >= 2x
its 20-day average, and (optional) NIFTY above its 50-day SMA. Buy the top 3 by volume ratio. Delivery (CNC),
long only. Exits: the initial 1.5 x daily ATR stop, plus exit_rule at the close (below EMA(20) / below the
previous 10-day low / either), plus an optional time cap (hold_days; None = let winners run).
Variant 3B (intraday VWAP pullback) was dropped on 1 Nov 2026: a ~0.46% stop leaves charges at 0.64R a trade.
Spec: STRATEGIES.md, strategy 3.
"""
import math

import numpy as np
import pandas as pd

import config as C
import costs
import indicators as ind
from engines.base import Signal

S3A = {"lookback": 20, "top_range_pct": 80, "vol_mult": 2.0, "picks": 3, "max_open": 8,
       "hold_days": None,            # None = no time exit (let winners run); 5 = the first version
       "exit_rule": "ema20",         # ema20 | low10 | either
       "regime": True,               # only buy when NIFTY closes above its 50-day SMA
       "near_52w": None,             # e.g. 0.85: only when close >= 85% of the 252-day high (None = off)
       "stop_atr": 1.5, "ema": 20, "low_days": 10, "risk_pct": 0.5, "max_position_pct": 33}


class SwingBreakoutEngine:
    name = "fno_swing_breakout_3a"

    def __init__(self, cfg: dict | None = None, capital: float = C.CAPITAL):
        self.cfg = dict(S3A, **(cfg or {}))
        self.capital = capital
        self.trades: dict[str, dict] = {}

    def calculate_indicators(self, df: pd.DataFrame, daily=None) -> pd.DataFrame:
        """df: DAILY bars of one stock. Everything uses completed days except the scan day's own bar (known near
        the close: the scan runs at 15:15 on the day's high/low/close/volume so far)."""
        c = self.cfg
        out = df.copy()
        out["hh20"] = df["high"].shift(1).rolling(c["lookback"]).max()
        out["vol_sma"] = df["volume"].shift(1).rolling(20).mean()
        out["vol_ratio"] = df["volume"] / out["vol_sma"]
        rng = (df["high"] - df["low"]).replace(0, np.nan)
        out["range_pos"] = (df["close"] - df["low"]) / rng * 100
        pc = df["close"].shift(1)
        tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
        out["atr"] = ind.rma(tr, 14)
        out["ema"] = df["close"].ewm(span=c["ema"], adjust=False, min_periods=c["ema"]).mean()
        out["low10"] = df["low"].shift(1).rolling(c["low_days"]).min()
        out["traded_value20"] = (df["close"] * df["volume"]).shift(1).rolling(20).mean()
        out["high252"] = df["high"].rolling(252, min_periods=200).max()   # includes today's high (known at 15:15)
        return out

    def generate_signal(self, current_bar: pd.Series, option_chain=None) -> Signal | None:
        c, b = self.cfg, current_bar
        if any(pd.isna(b.get(k)) for k in ("hh20", "vol_ratio", "range_pos", "atr", "ema")):
            return None
        if c["regime"] and not b.get("regime_ok", True):     # NIFTY close <= its 50-day SMA: no new longs
            return None
        if c.get("near_52w"):
            if pd.isna(b.get("high252")) or b["close"] < c["near_52w"] * b["high252"]:
                return None                                  # too far below the 52-week high (overhead supply)
        if not (b["close"] > b["hh20"] and b["range_pos"] >= c["top_range_pct"] and b["vol_ratio"] >= c["vol_mult"]):
            return None
        stop = b["close"] - c["stop_atr"] * b["atr"]
        sig = Signal("long", b.name, float(b["close"]), float(stop), float("inf"), "3A 20-day breakout",
                     instrument={"symbol": b.get("symbol")}, risk_per_unit=float(b["close"] - stop),
                     meta={"vol_ratio": float(b["vol_ratio"])})
        sig.qty = self.compute_position_size(self.capital, sig.risk_per_unit, price=float(b["close"]))
        return sig if sig.qty > 0 else None

    def compute_position_size(self, capital: float, stop_loss_points: float, price: float = 0.0) -> int:
        """Shares: risk_pct of capital per trade incl. delivery charges; value <= max_position_pct of capital."""
        c = self.cfg
        if stop_loss_points <= 0 or price <= 0:
            return 0
        risk = capital * c["risk_pct"] / 100
        cost_unit = costs.equity_round_trip(price, price, 100, delivery=True).total / 100
        qty = math.floor(risk / (stop_loss_points + cost_unit))
        return max(min(qty, math.floor(capital * c["max_position_pct"] / 100 / price)), 0)

    def manage_open_position(self, tick_data: dict) -> dict:
        """Daily update. tick_data: {'symbol', 'bar' (daily row with open/high/low/close/ema/low10), 'days_held'}.
        Order of checks inside a day: gap below the stop -> exit at the open; low through the stop -> exit at the
        stop; exit_rule at the close; time cap (if any) at the close."""
        t = self.trades.get(tick_data["symbol"])
        if t is None:
            return {"action": "none"}
        b = tick_data["bar"]
        if b["open"] <= t["stop"]:
            return {"action": "exit", "price": float(b["open"]), "reason": "gap below stop"}
        if b["low"] <= t["stop"]:
            return {"action": "exit", "price": float(t["stop"]), "reason": "stop hit"}
        rule = self.cfg["exit_rule"]
        below_ema = not pd.isna(b["ema"]) and b["close"] < b["ema"]
        below_low = not pd.isna(b.get("low10")) and b["close"] < b["low10"]
        if (rule in ("ema20", "either") and below_ema) or (rule in ("low10", "either") and below_low):
            why = "close below EMA20" if (below_ema and rule != "low10") else "close below 10-day low"
            return {"action": "exit", "price": float(b["close"]), "reason": why}
        if self.cfg["hold_days"] and tick_data["days_held"] >= self.cfg["hold_days"]:
            return {"action": "exit", "price": float(b["close"]), "reason": f"{self.cfg['hold_days']}-day limit"}
        return {"action": "hold"}
