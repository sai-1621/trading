"""Strategy 2: expiry-day defined-risk iron fly (NIFTY Tuesday / SENSEX Thursday).
Sell the ATM call + put, buy protective wings, collect the expiry-day time decay when implied volatility
is rich versus recent realised moves. Re-centre once if a breakeven is breached, otherwise get out.
Spec: STRATEGIES.md, strategy 2.
"""
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import config as C
import costs
import greeks
import indicators as ind
from engines.base import Signal, State, in_window
from nse_calendar import hm, is_expiry_day, years_to_expiry


@dataclass
class Fly:
    K: float
    W: float                     # wing distance in index points
    credit: float                # net credit per unit at entry
    legs: dict                   # name -> {'strike','cp','premium', ...} for sc, sp (short), lc, lp (long wings)
    qty: int
    adjustments: int = 0
    realized: float = 0.0        # rupees booked by earlier (re-centred) flies today
    charges: float = 0.0
    history: list = field(default_factory=list)

    @property
    def be_up(self):
        return self.K + self.credit

    @property
    def be_dn(self):
        return self.K - self.credit

    @property
    def max_loss_unit(self):
        return self.W - self.credit


class StrategyEngine:
    name = "expiry_iron_fly"

    def __init__(self, cfg: dict | None = None, capital: float = C.CAPITAL):
        self.cfg = dict(C.S2 if cfg is None else cfg)
        self.sym = self.cfg["symbol"]
        self.lot = C.LOT[self.sym]
        self.step = C.STRIKE_STEP[self.sym]
        self.exch = C.EXCHANGE_OF[self.sym]
        self.capital = capital
        self.state = State.FLAT
        self.fly: Fly | None = None
        self.bar_min = 5                        # set to 60 for hourly bars

    # ------------------------------------------------------------------ 1. indicators
    def calculate_indicators(self, df: pd.DataFrame, daily: pd.DataFrame | None = None,
                             vix: pd.Series | None = None) -> pd.DataFrame:
        out = df.copy()
        day = df.index.date
        out["day_high"] = df["high"].groupby(day).cummax()      # range from the open up to this bar (causal)
        out["day_low"] = df["low"].groupby(day).cummin()
        out["prev_close"] = ind.prior_close(df) if daily is None else _map_prior(df, daily["close"])
        day_open = df.groupby(df.index.date)["open"].transform("first")
        out["day_open"] = day_open
        out["gap_pct"] = (day_open / out["prev_close"] - 1) * 100
        d = daily if daily is not None else ind.daily_from_intraday(df)
        oc = (d["close"] - d["open"]).abs().rolling(10).mean()            # recent realised open->close move
        out["avg_oc_move"] = _map_prior(df, oc)
        out["atr5"] = ind.atr(df, 14)
        if vix is not None:
            out["vix_prev"] = _map_prior(df, vix)
        return out

    # ------------------------------------------------------------------ helpers
    def smile_iv(self, strike: float, spot: float, atm_iv: float, w_ref: float) -> float:
        """Model smile for same-day options: ATM IV at the money, rising linearly to wing_iv_mult x ATM IV at the
        wing distance w_ref and flat beyond. Used only when there is no live chain (backtests)."""
        m = self.cfg.get("wing_iv_mult", 1.0)
        return atm_iv * (1 + (m - 1) * min(abs(strike - spot) / w_ref, 1.0)) if w_ref > 0 else atm_iv

    def model_prices(self, fly: "Fly", spot: float, ts, atm_iv: float) -> dict:
        """Model (no chain) prices of the four legs at spot / time ts, with the smile anchored to the fly's width."""
        T = years_to_expiry(ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts, ts.date())
        return {k: float(greeks.price(spot, v["strike"], T, C.RISK_FREE,
                                      self.smile_iv(v["strike"], spot, atm_iv, fly.W), v["cp"]))
                for k, v in fly.legs.items()}

    def _price_legs(self, ts, spot, K, W, chain, iv, smile: bool = True):
        exp = ts.date()
        T = years_to_expiry(ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts, exp)
        spec = {"sc": (K, 1), "sp": (K, -1), "lc": (K + W, 1), "lp": (K - W, -1)}
        legs = {}
        for name, (k, cp) in spec.items():
            row = None
            if chain is not None and "ltp" in chain:
                m = chain[(chain["strike"] == k) & (chain["cp"] == cp)]
                row = m.iloc[0] if len(m) else None
            if row is not None:
                legs[name] = {"strike": k, "cp": cp, "premium": float(row["ltp"]), "symbol": row.get("symbol"),
                              "token": row.get("token"), "tick": row.get("tick", 0.05)}
            else:
                sig = self.smile_iv(k, spot, iv, W) if smile else iv
                legs[name] = {"strike": k, "cp": cp, "premium": float(greeks.price(spot, k, T, C.RISK_FREE, sig, cp))}
        return legs, T

    def build_fly(self, ts, spot: float, chain: pd.DataFrame | None, iv: float) -> Fly:
        """ATM strike; wings at ATM +/- ceil(wing_mult x straddle / step) x step (beyond both breakevens)."""
        K = round(spot / self.step) * self.step
        legs, _ = self._price_legs(ts, spot, K, self.step, chain, iv, smile=False)   # ATM straddle only
        straddle = legs["sc"]["premium"] + legs["sp"]["premium"]
        W = max(math.ceil(self.cfg["wing_mult"] * straddle / self.step), 1) * self.step
        legs, _ = self._price_legs(ts, spot, K, W, chain, iv)
        credit = legs["sc"]["premium"] + legs["sp"]["premium"] - legs["lc"]["premium"] - legs["lp"]["premium"]
        return Fly(K, W, credit, legs, 0)

    def margin_per_lot(self, fly: Fly, spot: float, expiry_day: bool = True) -> float:
        """Rough hedged margin: max loss (SPAN on a defined-risk spread) + 2% exposure on the short notional
        + 2% extra ELM on short options on expiry day (SEBI, from Nov 2024).
        Ask the broker's margin API before trading; this is for sizing only."""
        return fly.max_loss_unit * self.lot + (0.04 if expiry_day else 0.02) * spot * self.lot

    def close_cost_unit(self, fly: Fly, prices: dict) -> float:
        """Cost per unit to close the fly now (buy back shorts, sell wings)."""
        return prices["sc"] + prices["sp"] - prices["lc"] - prices["lp"]

    # ------------------------------------------------------------------ 2. signal
    def generate_signal(self, current_bar: pd.Series, option_chain: pd.DataFrame | None = None,
                        vix_now: float | None = None, expiry_day: bool | None = None,
                        iv: float | None = None) -> Signal | None:
        """Call on the bar that closes at entry_time (09:45) on an expiry day. Returns the fly as a Signal."""
        c, b, ts = self.cfg, current_bar, current_bar.name
        bar_end = ts + pd.Timedelta(minutes=self.bar_min)
        exp_today = is_expiry_day(self.sym, ts.date()) if expiry_day is None else expiry_day
        if self.state != State.FLAT or not exp_today:
            return None
        if bar_end.time() != hm(c["entry_time"]):
            return None
        if ts.date().isoformat() in c["event_days"]:
            return None
        vix = vix_now if vix_now is not None else b.get("vix_prev")
        if vix is None or pd.isna(vix) or vix > c["vix_max"]:
            return None
        if vix_now is not None and b.get("vix_prev") and vix_now > b["vix_prev"] * (1 + c["vix_jump_max_pct"] / 100):
            return None
        if pd.isna(b.get("gap_pct")) or abs(b["gap_pct"]) > c["gap_max_pct"]:
            return None
        exp_move = b["close"] * vix / 100 / math.sqrt(252)
        elapsed = bar_end.hour * 60 + bar_end.minute - (9 * 60 + 15)
        if (b["day_high"] - b["day_low"]) > c["or_max_expected_move"] * exp_move * math.sqrt(elapsed / 30):
            return None
        # price at the bar close (decision time); iv overrides VIX for model pricing in backtests
        fly = self.build_fly(bar_end, b["close"], option_chain, iv if iv is not None else vix / 100)
        straddle = fly.legs["sc"]["premium"] + fly.legs["sp"]["premium"]
        remaining = (hm(C.MARKET_CLOSE).hour * 60 + 30 - (bar_end.hour * 60 + bar_end.minute)) / 375
        realized_ref = b["avg_oc_move"] * math.sqrt(max(remaining, 0.05))
        if pd.isna(realized_ref) or straddle < c["min_premium_vs_realized"] * realized_ref:
            return None
        stop_unit = c["stop_pct_of_max_loss"] / 100 * fly.max_loss_unit
        lots = self.compute_position_size(self.capital, stop_unit, fly=fly, spot=b["close"])
        if lots <= 0:
            return None
        fly.qty = lots * self.lot
        return Signal("neutral", ts, float(b["close"]), fly.be_dn, fly.be_up,
                      f"iron fly K={fly.K:.0f} wings +/-{fly.W:.0f} credit {fly.credit:.1f} VIX {vix:.1f}",
                      instrument={"fly": fly}, risk_per_unit=stop_unit, qty=fly.qty,
                      meta={"straddle": straddle, "realized_ref": realized_ref, "exp_move": exp_move})

    # ------------------------------------------------------------------ 3. sizing
    def compute_position_size(self, capital: float, stop_loss_points: float, fly: Fly | None = None,
                              spot: float = 0.0, room: float | None = None) -> int:
        """Lots. stop_loss_points = per-unit loss at the MTM stop (60% of max loss).
        Limited by (a) the daily loss limit incl. charges for the entry, one re-centre and the exit,
        (b) worst case at expiry (max loss) <= 2x the daily limit, (c) estimated margin <= 60% of capital."""
        budget = capital * C.MAX_DAILY_LOSS_PCT / 100
        if room is not None:
            budget = min(budget, room)
        if stop_loss_points <= 0:
            return 0
        lots = 0
        while True:
            n = lots + 1
            q = n * self.lot
            ch = 0.0
            if fly is not None:
                sp = fly.legs["sc"]["premium"]
                wp = fly.legs["lc"]["premium"]
                ch = costs.iron_fly_charges(sp, wp, q, self.exch).total * 2   # allow for one re-centre
            if stop_loss_points * q + ch > budget:
                break
            if fly is not None:
                if fly.max_loss_unit * q > 2 * capital * C.MAX_DAILY_LOSS_PCT / 100:
                    break
                if self.margin_per_lot(fly, spot) * n > 0.6 * capital:
                    break
            lots = n
        return lots

    # ------------------------------------------------------------------ 4. open position
    def on_entry(self, sig: Signal):
        self.fly = sig.instrument["fly"]
        sp, wp = self.fly.legs["sc"]["premium"], self.fly.legs["lc"]["premium"]
        self.fly.charges += costs.iron_fly_charges(sp, wp, self.fly.qty, self.exch).total
        self.state = State.OPEN

    def manage_open_position(self, tick_data: dict) -> dict:
        """tick_data: {'ts', 'spot', 'prices': {'sc','sp','lc','lp'} (current leg LTPs), 'bar_close',
        'vix_spike' (bool from the kill switch), 'chain' (optional, needed to re-centre live), 'iv' (backtest)}.
        Returns {'action': 'hold'|'exit'|'recenter', 'reason', 'mtm' (rupees, after charges so far)}."""
        if self.state != State.OPEN or self.fly is None:
            return {"action": "none", "reason": "no open position"}
        f, c, ts, spot = self.fly, self.cfg, tick_data["ts"], tick_data["spot"]
        pnl_unit = f.credit - self.close_cost_unit(f, tick_data["prices"])
        mtm = pnl_unit * f.qty + f.realized - f.charges
        t = ts.timetz().replace(tzinfo=None) if ts.tzinfo else ts.time()
        out = {"mtm": mtm, "pnl_unit": pnl_unit}
        if t >= hm(C.SQUARE_OFF):
            return {**out, "action": "exit", "reason": f"square-off {C.SQUARE_OFF}"}
        if tick_data.get("vix_spike"):
            return {**out, "action": "exit", "reason": "India VIX spike"}
        if pnl_unit >= c["profit_take_pct"] / 100 * f.credit:
            return {**out, "action": "exit", "reason": f"profit take {c['profit_take_pct']}% of credit"}
        if -pnl_unit >= c["stop_pct_of_max_loss"] / 100 * f.max_loss_unit:
            return {**out, "action": "exit", "reason": f"stop: loss {c['stop_pct_of_max_loss']}% of max loss"}
        if tick_data.get("bar_close") and (spot >= f.be_up or spot <= f.be_dn):
            side = "upper" if spot >= f.be_up else "lower"
            ok = (f.adjustments < c["max_adjustments"] and t <= hm(c["last_adjust"])
                  and -pnl_unit < 0.5 * f.max_loss_unit)
            if ok:
                return {**out, "action": "recenter", "reason": f"{side} breakeven breached: re-centre at new ATM"}
            return {**out, "action": "exit", "reason": f"{side} breakeven breached again / too late to adjust"}
        return {**out, "action": "hold", "reason": ""}

    def recenter(self, ts, spot: float, chain: pd.DataFrame | None, iv: float, prices: dict):
        """Close the current fly at `prices` and open a new one at the new ATM with the same lot count."""
        f = self.fly
        booked = (f.credit - self.close_cost_unit(f, prices)) * f.qty
        new = self.build_fly(ts, spot, chain, iv)
        new.qty, new.adjustments = f.qty, f.adjustments + 1
        new.realized = f.realized + booked
        new.charges = f.charges + costs.iron_fly_charges(new.legs["sc"]["premium"], new.legs["lc"]["premium"],
                                                         f.qty, self.exch).total
        new.history = f.history + [{"ts": str(ts), "K": f.K, "booked": round(booked, 2)}]
        self.fly = new
        return new

    def new_day(self):
        self.state, self.fly = State.FLAT, None


def _map_prior(df: pd.DataFrame, daily_series: pd.Series) -> pd.Series:
    s = daily_series.copy()
    s.index = pd.to_datetime(s.index).date
    s = s.shift(1)
    return pd.Series(pd.Index(df.index.date).map(s), index=df.index, dtype=float)
