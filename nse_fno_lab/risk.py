"""Bot safety layer shared by all three strategies.

DailyRisk   - daily loss limit, trades per day, losses per day.
KillSwitch  - one place that decides OK / NO_NEW_ENTRIES / FLATTEN_ALL, with the reason logged.
OrderGuard  - places orders safely: idempotent tags, timeout -> look up before resending, partial fills,
              rejected limit orders (re-price ladder, then a protected market order), and multi-leg
              sequencing that never leaves a naked short option.
"""
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path

import config as C

log = logging.getLogger("fno")


class Mode(IntEnum):
    OK = 0
    NO_NEW_ENTRIES = 1
    FLATTEN_ALL = 2


@dataclass
class DailyRisk:
    capital: float = C.CAPITAL
    max_loss_pct: float = C.MAX_DAILY_LOSS_PCT
    max_trades: int = C.MAX_TRADES_PER_DAY
    max_losses: int = C.MAX_LOSSES_PER_DAY
    realized: float = 0.0
    unrealized: float = 0.0
    trades: int = 0
    losses: int = 0

    @property
    def max_loss(self) -> float:
        return self.capital * self.max_loss_pct / 100

    @property
    def pnl(self) -> float:
        return self.realized + self.unrealized

    def room(self) -> float:
        """Rupees that can still be lost today before the limit."""
        return max(self.max_loss + min(self.pnl, 0.0), 0.0)

    def can_enter(self, risk_rupees: float) -> tuple[bool, str]:
        if self.trades >= self.max_trades:
            return False, f"{self.trades} trades today (max {self.max_trades})"
        if self.losses >= self.max_losses:
            return False, f"{self.losses} losing trades today (max {self.max_losses})"
        if risk_rupees > self.room():
            return False, f"trade risk Rs {risk_rupees:.0f} > room left Rs {self.room():.0f}"
        return True, "ok"

    def on_close(self, pnl: float):
        self.realized += pnl
        self.trades += 1
        self.losses += pnl < 0


@dataclass
class KillSwitch:
    risk: DailyRisk
    kill_file: Path = C.BASE_DIR / "KILL"          # create this file to flatten everything by hand
    max_api_failures: int = 3
    stale_seconds: int = 45
    vix_spike_pct: float = 8.0                     # India VIX up this much vs today's open
    vix_spike_15m_pct: float = 4.0                 # or this much within 15 minutes
    api_failures: int = 0
    last_data: float = field(default_factory=time.time)
    mode: Mode = Mode.OK
    reasons: list = field(default_factory=list)

    def _set(self, mode: Mode, why: str):
        if mode > self.mode:
            self.mode = mode
            self.reasons.append(why)
            log.warning("KILL-SWITCH %s: %s", mode.name, why)

    def api_ok(self):
        self.api_failures = 0

    def api_failed(self, what: str):
        self.api_failures += 1
        if self.api_failures >= self.max_api_failures:
            self._set(Mode.NO_NEW_ENTRIES, f"{self.api_failures} API failures in a row ({what})")

    def data_tick(self):
        self.last_data = time.time()

    def check(self, vix_now: float | None = None, vix_open: float | None = None,
              vix_15m_ago: float | None = None, flatten_on_vix: bool = False) -> Mode:
        """Call every loop. flatten_on_vix=True for option SELLING (short gamma), False for buying."""
        if self.kill_file.exists():
            self._set(Mode.FLATTEN_ALL, "KILL file present")
        if self.risk.pnl <= -self.risk.max_loss:
            self._set(Mode.FLATTEN_ALL, f"daily loss Rs {-self.risk.pnl:.0f} hit the limit Rs {self.risk.max_loss:.0f}")
        if time.time() - self.last_data > self.stale_seconds:
            self._set(Mode.NO_NEW_ENTRIES, f"no market data for {time.time() - self.last_data:.0f}s")
        if vix_now and vix_open and vix_now >= vix_open * (1 + self.vix_spike_pct / 100):
            self._set(Mode.FLATTEN_ALL if flatten_on_vix else Mode.NO_NEW_ENTRIES,
                      f"India VIX {vix_now:.2f} is {(vix_now / vix_open - 1) * 100:.1f}% above the open")
        if vix_now and vix_15m_ago and vix_now >= vix_15m_ago * (1 + self.vix_spike_15m_pct / 100):
            self._set(Mode.FLATTEN_ALL if flatten_on_vix else Mode.NO_NEW_ENTRIES,
                      f"India VIX up {(vix_now / vix_15m_ago - 1) * 100:.1f}% in 15 minutes")
        return self.mode


# ------------------------------------------------------------------ orders
@dataclass
class Order:
    symbol: str            # broker trading symbol, e.g. NIFTY10NOV2625000CE
    token: str
    exchange: str          # NFO | BFO | NSE
    side: str              # BUY | SELL
    qty: int
    price: float | None = None     # None = market
    tag: str = field(default_factory=lambda: "fno" + uuid.uuid4().hex[:12])
    tick: float = 0.05


@dataclass
class Fill:
    status: str            # complete | partial | rejected | cancelled | open | unknown
    filled: int = 0
    avg_price: float = 0.0
    order_id: str = ""
    reason: str = ""


class OrderGuard:
    """Wraps a broker (see broker.py) with the edge-case rules from STRATEGIES.md section 4."""

    def __init__(self, broker, kill: KillSwitch, timeout_s: float = 8.0, reprice_steps: int = 3,
                 step_ticks: int = 2, wait_s: float = 3.0):
        self.b, self.kill = broker, kill
        self.timeout_s, self.reprice_steps, self.step_ticks, self.wait_s = timeout_s, reprice_steps, step_ticks, wait_s

    def _submit(self, o: Order) -> Fill:
        """Send once. On a timeout or network error, look the order up by its tag before deciding anything."""
        try:
            oid = self.b.place(o)
            self.kill.api_ok()
        except TimeoutError:
            self.kill.api_failed("place timeout")
            found = self.b.find_by_tag(o.tag)
            if found is None:
                return Fill("unknown", reason="timeout, order not in order book")
            oid = found
        except Exception as e:  # noqa: BLE001 - any broker error counts as a failure
            self.kill.api_failed(f"place error {e}")
            return Fill("rejected", reason=str(e))
        t0 = time.time()
        f = self.b.status(oid)
        while f.status == "open" and time.time() - t0 < self.timeout_s:
            time.sleep(0.5)
            f = self.b.status(oid)
        f.order_id = oid
        return f

    def execute(self, o: Order, ltp_fn, must_complete: bool = False) -> Fill:
        """Limit order at LTP +/- 1 tick, re-priced up to `reprice_steps` times, then:
        - entries (must_complete=False): cancel the rest and keep what filled (caller sizes the stop to it);
        - exits   (must_complete=True):  send the rest as a market order, retrying until it is done."""
        total, cost, left = 0, 0.0, o.qty
        sign = 1 if o.side == "BUY" else -1
        for step in range(self.reprice_steps + 1):
            px = ltp_fn() + sign * o.tick * (1 + step * self.step_ticks)
            px = round(round(px / o.tick) * o.tick, 2)
            f = self._submit(Order(o.symbol, o.token, o.exchange, o.side, left, px, f"{o.tag[:14]}{step}", o.tick))
            if f.status in ("open", "partial"):
                self.b.cancel(f.order_id)
                f = self.b.status(f.order_id)
            total += f.filled
            cost += f.filled * f.avg_price
            left = o.qty - total
            if left <= 0:
                break
            if f.status == "rejected" and "margin" in f.reason.lower():
                log.error("order rejected for margin: %s", f.reason)
                break
            time.sleep(self.wait_s)
        if left > 0 and must_complete:
            for attempt in range(5):
                f = self._submit(Order(o.symbol, o.token, o.exchange, o.side, left, None, f"{o.tag[:14]}m{attempt}"))
                total += f.filled
                cost += f.filled * f.avg_price
                left = o.qty - total
                if left <= 0:
                    break
                time.sleep(1 + attempt)
            if left > 0:
                self.kill._set(Mode.FLATTEN_ALL, f"could not exit {left} of {o.symbol}")
        status = "complete" if left <= 0 else ("partial" if total else "rejected")
        return Fill(status, total, cost / total if total else 0.0)

    def open_spread(self, legs: list[Order], ltp_fns: dict) -> list[Fill] | None:
        """Multi-leg entry. BUY legs (hedges) go first so the SELL legs get the hedge margin benefit.
        If any leg cannot be completed, everything already filled is closed again (stay flat, never naked short)."""
        buys = [lg for lg in legs if lg.side == "BUY"]
        sells = [lg for lg in legs if lg.side == "SELL"]
        done: list[tuple[Order, Fill]] = []
        for lg in buys + sells:
            f = self.execute(lg, ltp_fns[lg.symbol])
            if f.filled < lg.qty:
                log.error("leg %s filled %d/%d: rolling the spread back", lg.symbol, f.filled, lg.qty)
                if f.filled:
                    done.append((Order(lg.symbol, lg.token, lg.exchange, lg.side, f.filled, tick=lg.tick), f))
                self.close_spread([d[0] for d in done], ltp_fns)
                return None
            done.append((lg, f))
        return [d[1] for d in done]

    def close_spread(self, legs: list[Order], ltp_fns: dict) -> list[Fill]:
        """Exit: buy back SHORT legs first, then sell the hedges. Exits must complete (market fallback)."""
        shorts = [lg for lg in legs if lg.side == "SELL"]
        longs = [lg for lg in legs if lg.side == "BUY"]
        out = []
        for lg in shorts + longs:
            rev = Order(lg.symbol, lg.token, lg.exchange, "BUY" if lg.side == "SELL" else "SELL", lg.qty, tick=lg.tick)
            out.append(self.execute(rev, ltp_fns[lg.symbol], must_complete=True))
        return out
