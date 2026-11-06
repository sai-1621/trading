"""Shared pieces of the strategy engines: the state machine states, signals, and the directional trade manager
(hard stop, break-even move, ATR trailing stop, target, time stop, square-off)."""
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum

import config as C
from nse_calendar import hm


class State(Enum):
    FLAT = "flat"
    OPEN = "open"
    COOLDOWN = "cooldown"
    DONE_FOR_DAY = "done_for_day"


@dataclass
class Signal:
    side: str                    # "long" | "short" (direction of the underlying view)
    ts: datetime
    underlying: float
    stop: float                  # underlying stop level
    target: float                # underlying target level
    reason: str
    instrument: dict = field(default_factory=dict)   # option leg(s) or stock details
    risk_per_unit: float = 0.0   # rupees lost per unit (share / option unit) if the stop is hit
    qty: int = 0
    meta: dict = field(default_factory=dict)


@dataclass
class Trade:
    sig: Signal
    entry_bar: int
    stop: float
    best: float                  # best underlying close since entry (direction-adjusted)
    bars: int = 0
    moved_to_be: bool = False


def in_window(ts: datetime, start: str, end: str) -> bool:
    t = ts.timetz().replace(tzinfo=None) if ts.tzinfo else ts.time()
    return hm(start) <= t <= hm(end)


def manage_directional(tr: Trade, ts: datetime, price: float, bar_close: bool, atr_now: float,
                       cfg: dict, square_off: str = C.SQUARE_OFF) -> dict:
    """One update of an open directional trade on the UNDERLYING price.
    Hard stop / target / square-off are checked on every tick; trailing and the time stop only on bar closes.
    Returns {'action': 'hold'|'exit'|'update_stop', 'reason', 'stop'}."""
    s = tr.sig
    d = 1 if s.side == "long" else -1
    r_pts = abs(s.underlying - s.stop)
    t = ts.timetz().replace(tzinfo=None) if ts.tzinfo else ts.time()
    if t >= hm(square_off):
        return {"action": "exit", "reason": f"square-off {square_off}", "stop": tr.stop}
    if d * (price - tr.stop) <= 0:
        return {"action": "exit", "reason": "stop hit", "stop": tr.stop}
    if d * (price - s.target) >= 0:
        return {"action": "exit", "reason": "target hit", "stop": tr.stop}
    if not bar_close:
        return {"action": "hold", "reason": "", "stop": tr.stop}
    tr.bars += 1
    tr.best = max(tr.best, d * price)
    fav = tr.best - d * s.underlying
    new = tr.stop
    if not tr.moved_to_be and fav >= cfg["breakeven_at_r"] * r_pts:
        new = s.underlying
        tr.moved_to_be = True
    if tr.moved_to_be and atr_now == atr_now:  # not NaN
        trail = d * (tr.best - cfg["trail_atr"] * atr_now)
        new = max(new, trail) if d == 1 else min(new, trail)
    if tr.bars >= cfg["time_stop_bars"] and fav < cfg.get("time_stop_min_r", 0.5) * r_pts:
        return {"action": "exit", "reason": f"time stop: under {cfg.get('time_stop_min_r', 0.5)}R after {tr.bars} bars",
                "stop": tr.stop}
    if new != tr.stop:
        tr.stop = new
        return {"action": "update_stop", "reason": "break-even / trailing", "stop": new}
    return {"action": "hold", "reason": "", "stop": tr.stop}
