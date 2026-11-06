"""Order execution. Entries: post-only limit chase. Exits: reduce-only market orders.

Limit chase (both modes): place a post-only buy at the best bid; every CHASE_SECONDS, if not filled, cancel and
re-place at the new best bid, up to CHASE_STEPS times; give up (keep any partial fill) if the best bid has run more
than CHASE_MAX_PCT above the signal close. Post-only means we pay the maker fee and never cross the spread.

PaperExecutor : real order book and real trades from Bybit, no orders sent. A paper bid is filled when a trade
                prints at or below it during the wait (queue position is ignored, so paper fills are optimistic).
LiveExecutor  : real orders. After an entry fill it places an exchange-side reduce-only stop-market order, so the
                stop works even if the bot or the internet stops. UNTESTED without keys: try BYBIT_TESTNET=true first.
"""
import asyncio
import logging
import time

import config as C

log = logging.getLogger("momo")


class PaperExecutor:
    mode = "paper"

    def __init__(self, ex):
        self.ex = ex

    async def _best(self, sym):
        ob = await self.ex.fetch_order_book(sym, limit=5)
        return (ob["bids"][0][0] if ob["bids"] else None), (ob["asks"][0][0] if ob["asks"] else None)

    async def entry(self, sym: str, qty: float, signal_close: float, stop: float) -> dict:
        cap = signal_close * (1 + C.CHASE_MAX_PCT / 100)
        for step in range(C.CHASE_STEPS):
            bid, ask = await self._best(sym)
            if bid is None:
                return {"filled": 0.0, "why": "empty book"}
            if bid > cap:
                return {"filled": 0.0, "why": f"ran away: bid {bid} > chase limit {cap:.6g}"}
            if bid <= stop:
                return {"filled": 0.0, "why": "price back at the stop"}
            t_end = time.time() + C.CHASE_SECONDS
            since = int(time.time() * 1000)
            while time.time() < t_end:
                await asyncio.sleep(2)
                try:
                    trades = await self.ex.fetch_trades(sym, since=since, limit=50)
                except Exception:  # noqa: BLE001
                    trades = []
                if any(t["price"] <= bid for t in trades):
                    return {"filled": qty, "avg": bid, "fee": qty * bid * C.FEE_MAKER, "steps": step + 1}
        return {"filled": 0.0, "why": f"not filled after {C.CHASE_STEPS} re-prices"}

    async def place_stop(self, sym, qty, stop):
        return {"id": "paper-stop"}

    async def exit(self, sym: str, qty: float, reason: str, stop_order=None, stop_px: float | None = None) -> dict:
        bid, _ = await self._best(sym)
        px = bid if bid else 0.0
        if reason == "stop" and stop_px:
            px = min(px, stop_px) if px else stop_px
        px *= 1 - C.PAPER_EXIT_SLIP
        return {"filled": qty, "avg": px, "fee": qty * px * C.FEE_TAKER}

    async def still_open(self, sym) -> bool | None:
        return None


class LiveExecutor(PaperExecutor):
    mode = "live"

    async def setup(self, sym):
        if C.MARKET == "linear":
            try:
                await self.ex.set_margin_mode("isolated", sym)
            except Exception:  # noqa: BLE001 - already isolated
                pass
            try:
                await self.ex.set_leverage(C.LEVERAGE, sym)
            except Exception:  # noqa: BLE001
                pass

    async def entry(self, sym: str, qty: float, signal_close: float, stop: float) -> dict:
        await self.setup(sym)
        cap = signal_close * (1 + C.CHASE_MAX_PCT / 100)
        filled, cost, fee, left = 0.0, 0.0, 0.0, qty
        for step in range(C.CHASE_STEPS):
            bid, _ = await self._best(sym)
            if bid is None or bid > cap or bid <= stop:
                break
            amt = float(self.ex.amount_to_precision(sym, left))
            if amt <= 0:
                break
            o = await self.ex.create_order(sym, "limit", "buy", amt, bid, {"postOnly": True})
            await asyncio.sleep(C.CHASE_SECONDS)
            o = await self.ex.fetch_order(o["id"], sym)
            if o["status"] != "closed":
                try:
                    await self.ex.cancel_order(o["id"], sym)
                except Exception:  # noqa: BLE001 - filled meanwhile
                    pass
                o = await self.ex.fetch_order(o["id"], sym)
            f = float(o.get("filled") or 0)
            filled += f
            cost += f * float(o.get("average") or bid)
            fee += float((o.get("fee") or {}).get("cost") or f * bid * C.FEE_MAKER)
            left = qty - filled
            if left * bid < 5:
                break
        if filled <= 0:
            return {"filled": 0.0, "why": "limit chase not filled"}
        return {"filled": filled, "avg": cost / filled, "fee": fee}

    async def place_stop(self, sym, qty, stop):
        amt = float(self.ex.amount_to_precision(sym, qty))
        params = {"stopLossPrice": stop, "reduceOnly": True} if C.MARKET == "linear" else {"triggerPrice": stop}
        return await self.ex.create_order(sym, "market", "sell", amt, None, params)

    async def exit(self, sym: str, qty: float, reason: str, stop_order=None, stop_px=None) -> dict:
        if stop_order and stop_order.get("id"):
            try:
                await self.ex.cancel_order(stop_order["id"], sym, {"trigger": True})
            except Exception:  # noqa: BLE001 - already triggered
                pass
        amt = float(self.ex.amount_to_precision(sym, qty))
        params = {"reduceOnly": True} if C.MARKET == "linear" else {}
        o = await self.ex.create_order(sym, "market", "sell", amt, None, params)
        o = await self.ex.fetch_order(o["id"], sym)
        return {"filled": float(o.get("filled") or amt), "avg": float(o.get("average") or 0),
                "fee": float((o.get("fee") or {}).get("cost") or 0)}

    async def still_open(self, sym) -> bool | None:
        if C.MARKET != "linear":
            return None
        ps = await self.ex.fetch_positions([sym])
        return any(abs(float(p.get("contracts") or 0)) > 0 for p in ps)
