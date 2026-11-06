"""Position sizing from the stop distance, plus the account-level guards."""
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import config as C


def size(equity: float, entry: float, stop: float, avg_turnover_5m: float, min_cost: float | None = None) -> dict:
    """Units to buy so that hitting the stop loses RISK_PCT of equity (including round-trip fees and a stop slip).
    Capped by MAX_POSITION_USDT, LIQ_FRAC x average 5-minute turnover, and equity x LEVERAGE."""
    if entry <= stop or entry <= 0:
        return {"qty": 0.0, "why": "stop not below entry"}
    risk_usdt = equity * C.RISK_PCT / 100
    per_unit = (entry - stop) + entry * (C.FEE_MAKER + C.FEE_TAKER) + stop * 0.001
    qty = risk_usdt / per_unit
    caps = {"risk": qty * entry, "max position": C.MAX_POSITION_USDT, "liquidity": C.LIQ_FRAC * avg_turnover_5m,
            "equity x leverage": equity * C.LEVERAGE}
    why = min(caps, key=caps.get)
    notional = caps[why]
    if min_cost and notional < min_cost:
        return {"qty": 0.0, "why": f"below the exchange minimum ({min_cost} USDT)"}
    if notional < 5:
        return {"qty": 0.0, "why": f"too small ({notional:.2f} USDT, limited by {why})"}
    return {"qty": notional / entry, "notional": notional, "risk_usdt": round((entry - stop) * notional / entry, 2),
            "why": why}


@dataclass
class Guard:
    equity: float = C.CAPITAL
    day: str = ""
    day_pnl: float = 0.0
    open_count: int = 0
    notes: list = field(default_factory=list)

    def roll(self):
        d = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if d != self.day:
            self.day, self.day_pnl = d, 0.0

    def can_open(self) -> tuple[bool, str]:
        self.roll()
        if C.KILL_FILE.exists():
            return False, "KILL file present"
        if self.open_count >= C.MAX_OPEN:
            return False, f"{self.open_count} positions open (max {C.MAX_OPEN})"
        if self.day_pnl <= -self.equity * C.DAILY_LOSS_PCT / 100:
            return False, f"daily loss {self.day_pnl:.2f} USDT hit the limit"
        return True, "ok"

    def booked(self, pnl: float):
        self.roll()
        self.day_pnl += pnl
        self.equity += pnl
