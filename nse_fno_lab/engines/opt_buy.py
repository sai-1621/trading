"""Strategy 1: intraday directional index option buying (NIFTY / SENSEX weekly, BANKNIFTY monthly).
Session-anchored VWAP + change in put/call open interest + ATR volatility breakout, and a gamma-vs-theta test:
buy an ATM option only when the realised variance per bar is large enough to beat the option's time decay.
Spec: STRATEGIES.md, strategy 1.
"""
import math

import numpy as np
import pandas as pd

import config as C
import costs
import greeks
import indicators as ind
from engines.base import Signal, State, Trade, in_window, manage_directional
from nse_calendar import nearest_expiry, years_to_expiry

SESSION_MIN = 375   # 09:15-15:30
BAR_MIN = 5


class StrategyEngine:
    name = "nifty_option_buying"

    def __init__(self, cfg: dict | None = None, capital: float = C.CAPITAL, require_oi: bool = True):
        self.cfg = dict(C.S1 if cfg is None else cfg)
        self.capital = capital
        self.lot = C.LOT[self.cfg["symbol"]]
        self.iv_scale = 1.0                     # index vol / NIFTY vol when only India VIX is available
        self.require_oi = require_oi
        self.state = State.FLAT
        self.trade: Trade | None = None
        self.cooldown_left = 0
        self.oi_hist: list[tuple[pd.Timestamp, float, float]] = []   # (ts, put OI, call OI) near ATM
        self.day = None
        self.day_bars: list[dict] = []          # today's completed bars (for the leg-2 flag)
        self.impulse: dict | None = None        # latest qualified morning impulse

    # ------------------------------------------------------------------ 1. indicators
    def calculate_indicators(self, df: pd.DataFrame, daily: pd.DataFrame | None = None,
                             vix: pd.Series | None = None) -> pd.DataFrame:
        """df: 5-minute NIFTY bars (volume from NIFTY futures if available; index data falls back to TWAP).
        daily: completed daily bars (for the daily ATR). vix: India VIX, daily closes indexed by date
        (yesterday's close is used, so no look-ahead) or an intraday series aligned to df."""
        c = self.cfg
        out = df.copy()
        out["vwap"] = ind.session_vwap(df)
        out["atr_fast"] = ind.atr(df, c["atr_fast"])
        out["atr_slow"] = ind.atr(df, c["atr_slow"])
        out["adx"] = ind.adx(df, 14)
        out = out.join(ind.opening_range(df, 15))
        out["datr"] = ind.prior_daily_atr(df, daily, 14)
        out["ret_std"] = ind.bar_return_std(df, 20)
        out["ema20"] = df["close"].ewm(span=20, adjust=False, min_periods=20).mean()
        out["tr"] = ind.true_range(df)
        out["hh"] = ind.rolling_high(df["high"], c["breakout_bars"])
        out["ll"] = ind.rolling_low(df["low"], c["breakout_bars"])
        if vix is not None:
            if isinstance(vix.index, pd.DatetimeIndex) and len(vix) and vix.index[0].hour != 0:
                out["vix"] = vix.reindex(df.index, method="ffill")
            else:  # daily closes: use the previous day's close
                v = vix.copy()
                v.index = pd.to_datetime(v.index).date
                v = v.shift(1)
                out["vix"] = pd.Index(df.index.date).map(v).astype(float)
        return out

    # ------------------------------------------------------------------ helpers
    def _oi_shift(self, chain: pd.DataFrame, spot: float, ts) -> float | None:
        """(change in put OI - change in call OI) over the last oi_window_bars, divided by total OI, ATM +/- n strikes.
        Positive = put writers adding / call writers covering = support under the market (bullish)."""
        if chain is None or "oi" not in chain or chain["oi"].isna().all():
            return None
        step = C.STRIKE_STEP[self.cfg["symbol"]]
        atm = round(spot / step) * step
        near = chain[(chain["strike"] - atm).abs() <= self.cfg["oi_strikes"] * step]
        put, call = near.loc[near["cp"] < 0, "oi"].sum(), near.loc[near["cp"] > 0, "oi"].sum()
        self.oi_hist.append((ts, put, call))
        n = self.cfg["oi_window_bars"]
        if len(self.oi_hist) <= n:
            return None
        _, p0, c0 = self.oi_hist[-1 - n]
        tot = put + call
        return ((put - p0) - (call - c0)) / tot if tot else None

    def _pick_option(self, chain: pd.DataFrame | None, ts, spot: float, cp: int, vix: float) -> dict:
        """ATM option (|delta| closest to 0.50) of the nearest expiry at least min_dte_days away.
        Without a live chain (backtests) the premium is a Black-Scholes price at IV = India VIX."""
        sym = self.cfg["symbol"]
        exp = nearest_expiry(sym, ts.date(), self.cfg["min_dte_days"])
        T = years_to_expiry(ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts, exp)
        step = C.STRIKE_STEP[sym]
        if chain is not None and "ltp" in chain:
            ch = chain[(chain["cp"] == cp) & (chain["expiry"].astype(str) == exp.isoformat())].copy()
            if not ch.empty:
                iv = greeks.implied_vol(ch["ltp"].to_numpy(), spot, ch["strike"].to_numpy(), T, C.RISK_FREE, cp)
                ch["iv"] = np.where(np.isnan(iv), vix / 100, iv)
                ch["delta"] = greeks.delta(spot, ch["strike"], T, C.RISK_FREE, ch["iv"], cp)
                r = ch.iloc[(ch["delta"].abs() - 0.5).abs().argmin()]
                K, prem, sigma = float(r["strike"]), float(r["ltp"]), float(r["iv"])
                extra = {"symbol": r.get("symbol"), "token": r.get("token"), "tick": r.get("tick", 0.05)}
            else:
                chain = None
        if chain is None or "ltp" not in chain:
            K, sigma = round(spot / step) * step, vix / 100 * self.iv_scale
            prem = float(greeks.price(spot, K, T, C.RISK_FREE, sigma, cp))
            extra = {}
        return {"expiry": exp, "strike": K, "cp": cp, "premium": prem, "iv": sigma, "T": T,
                "delta": float(greeks.delta(spot, K, T, C.RISK_FREE, sigma, cp)),
                "gamma": float(greeks.gamma(spot, K, T, C.RISK_FREE, sigma)),
                "theta": float(greeks.theta(spot, K, T, C.RISK_FREE, sigma, cp)), **extra}

    @staticmethod
    def intraday_theta(daily_theta: float, minutes: float) -> float:
        """Theta is per trading day; one day's decay is spread over the 375-minute session (09:15-15:30)."""
        return abs(daily_theta) * minutes / SESSION_MIN

    def gamma_theta_ratio(self, opt: dict, atr_5m: float) -> float:
        """Convexity filter (the trade filter): gamma P&L of a typical 5-minute move vs 5 minutes of theta.
            0.5 * gamma * ATR_5m^2  /  (|daily theta| * 5 / 375)
        Both sides are in premium points over the SAME horizon. Variance grows with the number of bars and theta
        with time, so the ratio is the same for any holding window. > 1: realised movement pays for the decay."""
        theta_bar = self.intraday_theta(opt["theta"], BAR_MIN)
        return 0.5 * opt["gamma"] * atr_5m ** 2 / theta_bar if theta_bar > 0 else 0.0

    def edge_ratio(self, opt: dict, atr_5m: float) -> dict:
        """Directional payoff vs cost over the expected hold (N bars), logged with every signal:
            move = k * ATR_5m * sqrt(N);  gain = delta * move + 0.5 * gamma * move^2
            cost = intraday theta over N bars + premium * STT + slippage points
        The delta term assumes the breakout direction is right, so this is optimistic by design; it is used as a
        sanity check (cfg edge_ratio_min, off by default), not as the gamma-vs-theta filter."""
        c = self.cfg
        n = c["hold_bars_expected"]
        move = c["edge_k"] * atr_5m * math.sqrt(n)
        gain = abs(opt["delta"]) * move + 0.5 * opt["gamma"] * move ** 2
        theta = self.intraday_theta(opt["theta"], n * BAR_MIN)
        friction = opt["premium"] * C.STT_OPT_SELL_PCT / 100 + c["slippage_pts"]
        return {"move": move, "gain": gain, "theta_hold": theta, "friction": friction,
                "ratio": gain / (theta + friction) if theta + friction > 0 else 0.0}

    # ------------------------------------------------------------------ 2. signal
    def _leg1_direction(self, b) -> tuple[str, int] | None:
        """Morning-breakout (leg 1) price conditions; regime checks are done by the caller."""
        c = self.cfg
        dist = c["vwap_dist_atr"] * b["atr_slow"]
        if b["atr_fast"] < c["atr_expansion"] * b["atr_slow"]:
            return None
        if b["close"] > b["vwap"] + dist and b["close"] > b["hh"] and b["close"] > b["or_high"]:
            return "long", 1
        if b["close"] < b["vwap"] - dist and b["close"] < b["ll"] and b["close"] < b["or_low"]:
            return "short", -1
        return None

    def _clip_stop(self, pts: float, spot: float) -> float:
        """Stop distance in index points, kept within stop_min_pct..stop_max_pct of spot (works for any index)."""
        c = self.cfg
        return float(np.clip(pts, spot * c["stop_min_pct"] / 100, spot * c["stop_max_pct"] / 100))

    def _oi_ok(self, side, oi) -> bool:
        if not self.require_oi:
            return True
        m = self.cfg["oi_min_shift"]
        return oi is not None and ((side == "long" and oi >= m) or (side == "short" and oi <= -m))

    def _flag_trigger(self, b, k: int):
        """Leg 2: bull (bear) flag after a qualified impulse. Uses only bars before k plus bar k's close.
        Consolidation = the bars after the impulse extreme H, 6-15 of them, every close beyond the 50% retracement
        of the impulse and beyond EMA(20), with volume (true range for index data without volume) below
        flag_contraction x the impulse average. Trigger: bar k closes beyond the consolidation's high (low).
        Returns (side, cp, flag_stop_level) or None."""
        imp, c = self.impulse, self.cfg
        if imp is None or imp.get("used"):
            return None
        d = imp["d"]
        cons = self.day_bars[imp["h_idx"] + 1:k]
        if not (c["flag_min_bars"] <= len(cons) <= c["flag_max_bars"]):
            return None
        H, L0 = imp["H"], imp["L0"]
        retr = L0 + 0.5 * (H - L0)          # works for both directions (H below L0 for shorts)
        closes = np.array([x["close"] for x in cons])
        emas = np.array([x["ema20"] for x in cons])
        if d == 1 and not ((closes > retr).all() and (closes > emas).all()):
            return None
        if d == -1 and not ((closes < retr).all() and (closes < emas).all()):
            return None
        span = self.day_bars[imp["base_idx"]:k]
        key = "volume" if all(x["volume"] > 0 for x in span) else "tr"
        imp_bars = self.day_bars[imp["base_idx"]:imp["h_idx"] + 1]
        if np.mean([x[key] for x in cons]) >= c["flag_contraction"] * np.mean([x[key] for x in imp_bars]):
            return None
        top = max(x["high"] for x in cons)
        bot = min(x["low"] for x in cons)
        if d == 1 and b["close"] > top:
            return "long", 1, bot
        if d == -1 and b["close"] < bot:
            return "short", -1, top
        return None

    def _update_impulse(self, b, k: int, leg1_side, qualified: bool):
        """Record a qualified impulse (leg-1 conditions with gamma/theta >= gamma_theta_min); track its extreme."""
        if leg1_side is not None and qualified:
            d = 1 if leg1_side[0] == "long" else -1
            seq = [x["low"] for x in self.day_bars[:k + 1]] if d == 1 else [x["high"] for x in self.day_bars[:k + 1]]
            base_idx = int(np.argmin(seq)) if d == 1 else int(np.argmax(seq))
            self.impulse = {"d": d, "L0": seq[base_idx], "base_idx": base_idx,
                            "H": b["high"] if d == 1 else b["low"], "h_idx": k, "used": False}
            return
        imp = self.impulse
        if imp is None:
            return
        if (imp["d"] == 1 and b["high"] > imp["H"]) or (imp["d"] == -1 and b["low"] < imp["H"]):
            imp["H"], imp["h_idx"] = (b["high"] if imp["d"] == 1 else b["low"]), k

    def generate_signal(self, current_bar: pd.Series, option_chain: pd.DataFrame | None = None) -> Signal | None:
        """current_bar: one row of calculate_indicators() (name = bar start time), evaluated at the bar CLOSE.
        Call it on EVERY completed bar, also while a trade is open (the leg-2 flag needs the whole day's path).
        option_chain: columns strike, cp (+1 CE / -1 PE), expiry, ltp, oi (live) or None (backtest)."""
        c, b, ts = self.cfg, current_bar, current_bar.name
        if self.day is not None and ts.date() != self.day:
            self.new_day()
        self.day = ts.date()
        if self.state == State.COOLDOWN:
            self.cooldown_left -= 1
            if self.cooldown_left <= 0:
                self.state = State.FLAT
        oi = self._oi_shift(option_chain, b["close"], ts) if option_chain is not None else None
        k = len(self.day_bars)
        self.day_bars.append({x: float(b[x]) if x in b and pd.notna(b[x]) else 0.0
                              for x in ("open", "high", "low", "close", "volume", "ema20", "tr")})
        bar_end = ts + pd.Timedelta(minutes=5)
        need = ["vwap", "atr_fast", "atr_slow", "adx", "or_high", "or_low", "datr", "hh", "ll", "vix", "ema20"]
        if any(pd.isna(b.get(x)) for x in need):
            return None
        orw = (b["or_high"] - b["or_low"]) / b["datr"]
        regime = (c["vix_min"] <= b["vix"] <= c["vix_max"]) and (c["or_width_min_atr"] <= orw <= c["or_width_max_atr"])
        # ---- leg 1: morning breakout
        leg1 = self._leg1_direction(b) if regime and b["adx"] >= c["adx_min"] else None
        leg1_opt, gt1 = None, 0.0
        if leg1 is not None and self._oi_ok(leg1[0], oi):
            leg1_opt = self._pick_option(option_chain, ts, b["close"], leg1[1], b["vix"])
            gt1 = self.gamma_theta_ratio(leg1_opt, b["atr_slow"])
        # impulse (for the leg-2 flag) keeps its own definition: leg-1 conditions with gamma/theta >= gamma_theta_min
        qualified = leg1_opt is not None and gt1 >= c["gamma_theta_min"]
        mode = c.get("gt_mode", "min")
        lo, hi = c["gt_band"]
        if mode == "min":
            enter1, limit1 = qualified, False
        else:   # "band" or "band_pullback": 1.5 <= g/t <= 1.9 enters; above 1.9 is a possible climax bar
            enter1 = leg1_opt is not None and lo <= gt1 <= hi
            limit1 = mode == "band_pullback" and leg1_opt is not None and gt1 > hi
        flag = self._flag_trigger(b, k) if regime and not (qualified or enter1 or limit1) else None
        self._update_impulse(b, k, leg1 if leg1_opt is not None else None, qualified)
        if self.state != State.FLAT or not in_window(bar_end, "09:30", c["last_entry"]):
            return None
        entry_meta = {"entry": "market"}
        if enter1 or limit1:
            side, cp = leg1
            opt, gt, kind = leg1_opt, gt1, "leg1"
            stop_pts = self._clip_stop(c["stop_atr"] * b["atr_slow"], b["close"])
            if limit1:
                # climax bar: only a limit order back inside the bar, valid for the next pullback_bars bars
                d0 = 1 if side == "long" else -1
                limit = b["close"] - d0 * c["pullback_frac"] * (b["high"] - b["low"])
                stop_level = b["close"] - d0 * stop_pts
                if d0 * (limit - stop_level) <= 0:
                    return None
                kind = "leg1_pullback"
                entry_meta = {"entry": "limit", "limit": float(limit), "valid_bars": c["pullback_bars"],
                              "stop_level": float(stop_level)}
        elif flag is not None and c["flag_enabled"] and self._oi_ok(flag[0], oi):
            side, cp, flag_stop = flag
            opt = self._pick_option(option_chain, ts, b["close"], cp, b["vix"])
            gt, kind = self.gamma_theta_ratio(opt, b["atr_slow"]), "leg2_flag"
            if gt < c["flag_gamma_theta_min"]:
                return None
            stop_pts = self._clip_stop(abs(b["close"] - flag_stop), b["close"])
            self.impulse["used"] = True
        else:
            return None
        er = self.edge_ratio(opt, b["atr_slow"])
        if er["ratio"] < c["edge_ratio_min"]:
            return None
        d = 1 if side == "long" else -1
        theta_hold = self.intraday_theta(opt["theta"], c["time_stop_bars"] * BAR_MIN)
        risk_unit = min(abs(opt["delta"]) * stop_pts + theta_hold, opt["premium"] * c["premium_stop_pct"] / 100)
        sig = Signal(side, ts, float(b["close"]), b["close"] - d * stop_pts, b["close"] + d * c["target_r"] * stop_pts,
                     f"{kind}: OI shift {oi if oi is None else round(oi, 3)}, gamma/theta {gt:.2f}",
                     instrument=opt, risk_per_unit=risk_unit,
                     meta={"kind": kind, "stop_pts": stop_pts, "gamma_theta": gt, "edge": er, "oi_shift": oi,
                           "premium_stop": opt["premium"] * (1 - c["premium_stop_pct"] / 100), **entry_meta})
        sig.qty = self.compute_position_size(self.capital, risk_unit, premium=opt["premium"]) * self.lot
        return sig if sig.qty > 0 else None

    def fill_limit(self, sig: Signal, fill_price: float, opt_premium: float) -> Signal:
        """A pullback limit order filled at `fill_price`: keep the stop LEVEL from the signal, re-base the target
        (target_r x the new, smaller R) and the per-unit risk on the fill."""
        c = self.cfg
        d = 1 if sig.side == "long" else -1
        r_pts = abs(fill_price - sig.stop)
        sig.underlying = fill_price
        sig.target = fill_price + d * c["target_r"] * r_pts
        theta_hold = self.intraday_theta(sig.instrument["theta"], c["time_stop_bars"] * BAR_MIN)
        sig.risk_per_unit = min(abs(sig.instrument["delta"]) * r_pts + theta_hold, opt_premium * c["premium_stop_pct"] / 100)
        sig.meta["premium_stop"] = opt_premium * (1 - c["premium_stop_pct"] / 100)
        return sig

    # ------------------------------------------------------------------ 3. sizing
    def compute_position_size(self, capital: float, stop_loss_points: float, premium: float = 0.0,
                              room: float | None = None) -> int:
        """Lots to buy. stop_loss_points = PREMIUM points lost per unit if the stop is hit.
        Risk per trade = daily loss limit / losses allowed per day (so two full losses = the daily limit),
        including round-trip charges; premium outlay capped at max_premium_capital_pct of capital."""
        budget = capital * C.MAX_DAILY_LOSS_PCT / 100 / C.MAX_LOSSES_PER_DAY
        if room is not None:
            budget = min(budget, room)
        if stop_loss_points <= 0:
            return 0
        lots = 0
        while True:
            n = lots + 1
            q = n * self.lot
            cost = costs.option_round_trip(premium, max(premium - stop_loss_points, 0.05), q).total if premium else 0
            if stop_loss_points * q + cost > budget:
                break
            if premium and premium * q > capital * self.cfg["max_premium_capital_pct"] / 100:
                break
            lots = n
        return lots

    # ------------------------------------------------------------------ 4. open position
    def on_entry(self, sig: Signal, bar_index: int = 0):
        self.trade = Trade(sig, bar_index, sig.stop, (1 if sig.side == "long" else -1) * sig.underlying)
        self.state = State.OPEN

    def manage_open_position(self, tick_data: dict) -> dict:
        """tick_data: {'ts', 'spot', 'option_ltp' (optional), 'bar_close' (bool), 'atr' (5m ATR slow)}.
        Exits on the underlying stop / target / trail / time stop, or on the premium stop (-30%)."""
        if self.state != State.OPEN or self.trade is None:
            return {"action": "none", "reason": "no open position"}
        ltp = tick_data.get("option_ltp")
        if ltp is not None and ltp <= self.trade.sig.meta["premium_stop"]:
            act = {"action": "exit", "reason": f"premium stop -{self.cfg['premium_stop_pct']}%", "stop": self.trade.stop}
        else:
            act = manage_directional(self.trade, tick_data["ts"], tick_data["spot"], tick_data.get("bar_close", False),
                                     tick_data.get("atr", math.nan), self.cfg)
        if act["action"] == "exit":
            self.state, self.cooldown_left = State.COOLDOWN, self.cfg["cooldown_bars"]
        return act

    def new_day(self):
        self.state, self.trade, self.cooldown_left, self.oi_hist = State.FLAT, None, 0, []
        self.day_bars, self.impulse = [], None
