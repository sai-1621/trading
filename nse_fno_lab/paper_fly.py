"""Real-price paper iron flies for expiry day (strategy 2), used by scripts/log_expiry_chain.py --loop.

Every price is a real quote from the option chain; no Black-Scholes on entry or exit:
  open   : sell the ATM call + put at the BID, buy the wings at the ASK   (executable credit)
  close  : buy the shorts back at the ASK, sell the wings at the BID      (executable cost to close)
Rules (same as engines/iron_fly.py), checked at every poll (default 60 s); the re-centre only on 5-minute closes:
  profit >= 40% of credit -> close | loss >= 60% of max loss -> close | India VIX >= 8% above its open -> close
  spot beyond a breakeven -> re-centre once (before 13:30, loss < 50% of max loss), otherwise close
  15:15 -> close
Charges: real per-order charges on the actual fill prices (no extra slippage: bid/ask already pays the spread).
A 5-minute mark can miss a stop that is crossed and recovered in between, and fills a breached stop at the next
mark's price: the paper P&L is a close, not exact, picture of a live stop.
Also records a shadow "hold to 15:15" P&L of the original strikes, to show what the rules add or cost.
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime

import config as C
import costs

LEGS = {"sc": ("CE", "sell"), "sp": ("PE", "sell"), "lc": ("CE", "buy"), "lp": ("PE", "buy")}


def quote(chain: dict, strike: float, side: str) -> dict | None:
    return (chain.get(strike) or {}).get(side)


def exec_px(q: dict, action: str) -> tuple[float, bool]:
    """Price we would actually trade at now. action 'buy' pays the ask, 'sell' gets the bid.
    Falls back to the last price (flagged) when that side of the book is empty."""
    p = q.get("ask" if action == "buy" else "bid", 0) or 0
    if p > 0:
        return float(p), False
    return float(q.get("ltp") or 0), True


def mid_px(q: dict) -> float:
    b, a = q.get("bid", 0) or 0, q.get("ask", 0) or 0
    return (b + a) / 2 if b > 0 and a > 0 else float(q.get("ltp") or 0)


@dataclass
class PaperFly:
    fid: str
    symbol: str
    lots: int
    K: float
    W: float
    entry_time: str
    spot_in: float
    vix_open: float
    strikes: dict = field(default_factory=dict)        # leg -> strike
    entry_px: dict = field(default_factory=dict)       # leg -> executable fill
    credit: float = 0.0                                # executable credit per unit
    credit_mid: float = 0.0
    realized: float = 0.0                              # Rs booked by flies closed at a re-centre
    charges: float = 0.0
    adjustments: int = 0
    status: str = "open"
    exit_time: str = ""
    exit_reason: str = ""
    final_net: float = 0.0
    shadow_strikes: dict = field(default_factory=dict) # original strikes, held to 15:15 regardless of rules
    shadow_credit: float = 0.0
    shadow_close: float | None = None
    flags: list = field(default_factory=list)
    history: list = field(default_factory=list)
    filters: dict = field(default_factory=dict)
    rule_width: bool = False

    @property
    def qty(self) -> int:
        return self.lots * C.LOT[self.symbol]

    @property
    def max_loss(self) -> float:
        return self.W - self.credit

    @property
    def be(self) -> tuple[float, float]:
        return self.K - self.credit, self.K + self.credit

    # ---------------------------------------------------------------- open / close at real prices
    @classmethod
    def open(cls, fid, symbol, chain, spot, K, W, now: datetime, vix, lots=1, filters=None, rule_width=False):
        f = cls(fid, symbol, lots, K, W, now.strftime("%H:%M"), spot, vix, filters=filters or {}, rule_width=rule_width)
        if not f._fill_new(chain, K, W):
            return None
        f.shadow_strikes, f.shadow_credit = dict(f.strikes), f.credit
        return f

    def _strikes_for(self, K, W):
        return {"sc": K, "sp": K, "lc": K + W, "lp": K - W}

    def _fill_new(self, chain, K, W) -> bool:
        st = self._strikes_for(K, W)
        px, mids = {}, {}
        for leg, (side, action) in LEGS.items():
            q = quote(chain, st[leg], side)
            if q is None:
                return False
            px[leg], fb = exec_px(q, action)
            mids[leg] = mid_px(q)
            if fb:
                self.flags.append(f"{self.entry_time} {leg} {st[leg]} filled at last price (empty book)")
        exch = C.EXCHANGE_OF[self.symbol]
        # hedges first, then shorts (order matters for margin live; charges are the same)
        for leg in ("lc", "lp", "sc", "sp"):
            self.charges += costs.option_order(px[leg], self.qty, LEGS[leg][1], exch, slippage=False).total
        self.K, self.W, self.strikes, self.entry_px = K, W, st, px
        self.credit = px["sc"] + px["sp"] - px["lc"] - px["lp"]
        self.credit_mid = mids["sc"] + mids["sp"] - mids["lc"] - mids["lp"]
        return True

    def close_cost(self, chain, strikes=None) -> tuple[float | None, float | None, dict]:
        """(executable cost to close per unit, mid cost, exit prices) for the current (or given) strikes."""
        st = strikes or self.strikes
        px, mids = {}, {}
        for leg, (side, action) in LEGS.items():
            q = quote(chain, st[leg], side)
            if q is None:
                return None, None, {}
            rev = "buy" if action == "sell" else "sell"
            px[leg], _ = exec_px(q, rev)
            mids[leg] = mid_px(q)
        return (px["sc"] + px["sp"] - px["lc"] - px["lp"], mids["sc"] + mids["sp"] - mids["lc"] - mids["lp"], px)

    def _book_close(self, px: dict):
        exch = C.EXCHANGE_OF[self.symbol]
        for leg in ("sc", "sp", "lc", "lp"):   # buy back shorts first, then sell wings
            rev = "buy" if LEGS[leg][1] == "sell" else "sell"
            self.charges += costs.option_order(max(px[leg], 0.05), self.qty, rev, exch, slippage=False).total
        cost = px["sc"] + px["sp"] - px["lc"] - px["lp"]
        self.realized += (self.credit - cost) * self.qty

    # ---------------------------------------------------------------- the 5-minute mark
    def mark(self, now: datetime, spot: float, chain: dict, vix: float | None, step: int, rule_width_fn=None,
             bar_close: bool = True) -> dict:
        """One check. Stops, profit-take, VIX and square-off on every call; the breakeven re-centre only when
        bar_close is True (a 5-minute close, as the strategy defines it)."""
        t = now.strftime("%H:%M")
        cost, cost_mid, px = self.close_cost(chain)
        row = {"time": t, "fid": self.fid, "spot": spot, "K": self.K, "W": self.W, "status": self.status}
        if self.shadow_close is None and now.hour * 60 + now.minute >= 15 * 60 + 15:
            sc, _, _ = self.close_cost(chain, self.shadow_strikes)
            if sc is not None:
                self.shadow_close = sc
        if self.status != "open":
            return row
        if cost is None:
            self.flags.append(f"{t} missing quotes for current strikes; no mark")
            return {**row, "action": "no quotes"}
        pnl_unit = self.credit - cost
        mtm = pnl_unit * self.qty + self.realized - self.charges
        row.update({"close_cost_exec": round(cost, 2), "close_cost_mid": round(cost_mid, 2),
                    "pnl_unit": round(pnl_unit, 2), "mtm_rs_after_charges": round(mtm, 2)})
        mins = now.hour * 60 + now.minute
        action, why = "hold", ""
        lo, hi = self.be
        if mins >= 15 * 60 + 15:
            action, why = "exit", "square-off 15:15"
        elif vix and self.vix_open and vix >= self.vix_open * 1.08:
            action, why = "exit", f"India VIX {vix:.2f} >= 8% above open {self.vix_open:.2f}"
        elif pnl_unit >= C.S2["profit_take_pct"] / 100 * self.credit:
            action, why = "exit", f"profit take {C.S2['profit_take_pct']}% of credit"
        elif -pnl_unit >= C.S2["stop_pct_of_max_loss"] / 100 * self.max_loss:
            action, why = "exit", f"stop: loss {C.S2['stop_pct_of_max_loss']}% of max loss"
        elif bar_close and (spot >= hi or spot <= lo):
            side = "upper" if spot >= hi else "lower"
            ok = self.adjustments < C.S2["max_adjustments"] and mins <= 13 * 60 + 30 and -pnl_unit < 0.5 * self.max_loss
            action, why = ("recenter", f"{side} breakeven breached") if ok else ("exit", f"{side} breakeven breached, no adjustment left")
        if action == "recenter":
            newK = round(spot / step) * step
            newW = rule_width_fn(chain, newK) if (self.rule_width and rule_width_fn) else self.W
            old = {"time": t, "K": self.K, "W": self.W, "credit": round(self.credit, 2), "close_cost": round(cost, 2)}
            self._book_close(px)
            if not self._fill_new(chain, newK, newW):
                self.status, self.exit_time, self.exit_reason = "closed", t, why + "; re-centre failed (no quotes)"
                self.credit = 0.0
            else:
                self.adjustments += 1
                self.history.append(old)
        elif action == "exit":
            self._book_close(px)
            self.status, self.exit_time, self.exit_reason = "closed", t, why
            self.credit = 0.0      # everything booked into realized
        if self.status == "closed":
            self.final_net = round(self.realized - self.charges, 2)
        return {**row, "action": action, "reason": why}

    def result_row(self, date_s: str, bsm_credit_flat: float | None = None) -> dict:
        qty = self.qty
        shadow = None
        if self.shadow_close is not None:
            shadow = round((self.shadow_credit - self.shadow_close) * qty, 2)
        return {"date": date_s, "fid": self.fid, "symbol": self.symbol, "lots": self.lots, "entry_time": self.entry_time,
                "spot_in": self.spot_in, "K_first": self.shadow_strikes.get("sc"), "W": self.W,
                "credit_exec_first": round(self.shadow_credit, 2), "bsm_credit_flat": bsm_credit_flat,
                "credit_ratio": round(self.shadow_credit / bsm_credit_flat, 4) if bsm_credit_flat else None,
                "exit_time": self.exit_time, "exit_reason": self.exit_reason, "adjustments": self.adjustments,
                "net_rs_after_charges": self.final_net if self.status == "closed" else None,
                "charges_rs": round(self.charges, 2), "hold_to_1515_gross_rs": shadow,
                "filters_pass": self.filters.get("pass"), "filter_notes": "; ".join(self.filters.get("notes", [])),
                "flags": " | ".join(self.flags)[:500]}

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "PaperFly":
        return cls(**d)
