"""Bybit 5-minute momentum bot: flat base -> volume ignition (leg 1) and high-tight bull flag (leg 2).

    .venv\\Scripts\\python bot.py            paper mode (default; real Bybit prices, no orders)
    .venv\\Scripts\\python bot.py --status   print state/status.json

Live orders need ALL of: BYBIT_API_KEY / BYBIT_API_SECRET in .env, LIVE_TRADING=true, and the --live flag.
Start on the testnet (BYBIT_TESTNET=true). Files: state/status.json, positions.json, trades.jsonl, signals.jsonl,
stalkers.json, bot.log.
"""
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone

import ccxt.pro as ccxtpro
import numpy as np

import config as C
import strategy as S
from execution import LiveExecutor, PaperExecutor
from feed import CandleBook, stream, warm_up
from risk import Guard, size
from universe import load_universe

log = logging.getLogger("momo")
SD = C.STATE_DIR


def setup_logging():
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for h in (logging.FileHandler(SD / "bot.log", encoding="utf-8"), logging.StreamHandler(sys.stdout)):
        h.setFormatter(fmt)
        log.addHandler(h)


def jdump(path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)


def jappend(path, obj):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, default=str) + "\n")


class Bot:
    def __init__(self, live: bool):
        opts = {"enableRateLimit": True, "options": {"defaultType": "swap" if C.MARKET == "linear" else "spot"}}
        if live:
            opts.update({"apiKey": C.API_KEY, "secret": C.API_SECRET})
        self.ex = ccxtpro.bybit(opts)
        if C.TESTNET:
            self.ex.set_sandbox_mode(True)
        self.execu = LiveExecutor(self.ex) if live else PaperExecutor(self.ex)
        self.book = CandleBook()
        self.universe: dict = {}
        self.guard = Guard()
        state = {}
        try:
            state = json.loads((SD / "positions.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        self.positions: dict = state.get("positions", {})
        self.guard.equity = state.get("equity", C.CAPITAL)
        self.guard.open_count = len(self.positions)
        self.pending: set = set()
        self.stalkers: dict = {}
        self.last_signals: list = []
        self.last_close_ts = None
        self.closes = 0                      # closed candles processed (heartbeat)
        self.stop_event = asyncio.Event()

    # ------------------------------------------------------------------ persistence
    def save(self):
        jdump(SD / "positions.json", {"positions": self.positions, "equity": self.guard.equity})
        jdump(SD / "stalkers.json", self.stalkers)
        jdump(SD / "status.json", {
            "mode": self.execu.mode, "market": C.MARKET, "testnet": C.TESTNET,
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "universe": len(self.universe), "equity": round(self.guard.equity, 2), "start_capital": C.CAPITAL,
            "day_pnl": round(self.guard.day_pnl, 2), "open": self.positions, "pending_entries": sorted(self.pending),
            "stalking": len(self.stalkers), "stalkers": sorted(self.stalkers), "last_candle": self.last_close_ts,
            "candles_processed": self.closes, "recent_signals": self.last_signals[-20:],
            "settings": {"risk_pct": C.RISK_PCT, "max_open": C.MAX_OPEN, "max_position_usdt": C.MAX_POSITION_USDT,
                         "legs": C.LEGS, "chase_max_pct": C.CHASE_MAX_PCT}})

    # ------------------------------------------------------------------ candle events
    async def on_close(self, sym: str):
        d = self.book.arrays(sym)
        if d is None:
            return
        self.closes += 1
        x = S.indicators(d)
        last = len(d["close"]) - 1
        self.last_close_ts = datetime.fromtimestamp(d["ts"][last] / 1000, timezone.utc).isoformat()
        if sym in self.positions:
            await self.manage(sym, d, x, last)
        logs = []
        sigs = [s for s in S.scan(d, x, universe=False, stalk_log=logs) if s.i == last and s.leg in C.LEGS]
        active = [e for e in logs if e["end"] >= len(d["close"]) and e["result"] == "expired"]
        if active:
            self.stalkers[sym] = {"since": datetime.fromtimestamp(d["ts"][active[-1]["start"]] / 1000, timezone.utc).isoformat(),
                                  "impulse_low": active[-1]["impulse_low"]}
        else:
            self.stalkers.pop(sym, None)
        for s in sigs:
            await self.on_signal(sym, s, d)

    async def on_signal(self, sym, s: S.Signal, d):
        rec = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "symbol": sym, "leg": s.leg,
               "close": s.close, "stop": s.stop, "stop_pct": round((s.close - s.stop) / s.close * 100, 2), **s.info}
        if sym in self.positions or sym in self.pending:
            rec["decision"] = "SKIPPED: already in this coin"
        else:
            ok, why = self.guard.can_open()
            avg_turn = float(np.mean(d["turnover"][-51:-1]))
            sz = size(self.guard.equity, s.close, s.stop, avg_turn, (self.universe.get(sym) or {}).get("min_cost"))
            rec["size"] = sz
            if not ok:
                rec["decision"] = f"SKIPPED: {why}"
            elif sz["qty"] <= 0:
                rec["decision"] = f"SKIPPED: {sz['why']}"
            else:
                rec["decision"] = "ENTERING (limit chase)"
                self.pending.add(sym)
                asyncio.create_task(self.enter(sym, s, sz))
        log.info("SIGNAL %s %s close %.6g stop %.6g -> %s", sym, s.leg, s.close, s.stop, rec["decision"])
        jappend(SD / "signals.jsonl", rec)
        self.last_signals.append(rec)

    async def enter(self, sym, s: S.Signal, sz):
        try:
            r = await self.execu.entry(sym, sz["qty"], s.close, s.stop)
            if r.get("filled", 0) <= 0:
                log.info("ENTRY %s not filled: %s", sym, r.get("why"))
                jappend(SD / "signals.jsonl", {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                               "symbol": sym, "leg": s.leg, "decision": f"NOT FILLED: {r.get('why')}"})
                return
            stop_order = await self.execu.place_stop(sym, r["filled"], s.stop)
            self.positions[sym] = {"leg": s.leg, "qty": r["filled"], "qty0": r["filled"], "entry": r["avg"],
                                   "stop": s.stop, "fee_in": r.get("fee", 0.0), "partial": False, "left": 1.0,
                                   "opened": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                   "stop_order": {"id": (stop_order or {}).get("id")}, "booked": 0.0}
            self.guard.open_count = len(self.positions)
            log.info("ENTRY %s %s filled %.6g @ %.6g, stop %.6g", sym, s.leg, r["filled"], r["avg"], s.stop)
            self.save()
        except Exception as e:  # noqa: BLE001
            log.error("ENTRY %s failed: %s", sym, e)
        finally:
            self.pending.discard(sym)

    async def manage(self, sym, d, x, last):
        p = self.positions[sym]
        if self.execu.mode == "live":
            still = await self.execu.still_open(sym)
            if still is False:                               # the exchange stop filled
                await self.close(sym, p["qty"], "stop (exchange)", p["stop"])
                return
        pos = {"stop": p["stop"], "left": p["left"], "partial": p["partial"]}
        for frac, px, why in S.exit_event(d, x, last, pos):
            qty = p["qty0"] * frac
            await self.close(sym, qty, why, px)
            if sym not in self.positions:
                return
        p["partial"], p["left"] = pos["partial"], pos["left"]

    async def on_tick(self, sym: str):
        """Intrabar stop for paper mode (live relies on the exchange stop order)."""
        p = self.positions.get(sym)
        cur = self.book.live.get(sym)
        if p and cur and self.execu.mode == "paper" and cur[3] <= p["stop"]:
            await self.close(sym, p["qty"], "stop", p["stop"])

    async def close(self, sym, qty, why, px_hint):
        p = self.positions.get(sym)
        if not p:
            return
        qty = min(qty, p["qty"])
        r = await self.execu.exit(sym, qty, "stop" if why.startswith("stop") else why, p.get("stop_order"), p["stop"])
        fee_in_part = p["fee_in"] * qty / p["qty0"]
        pnl = (r["avg"] - p["entry"]) * r["filled"] - r.get("fee", 0) - fee_in_part
        p["qty"] -= r["filled"]
        p["booked"] += pnl
        self.guard.booked(pnl)
        risk = (p["entry"] - p["stop"]) * p["qty0"]
        jappend(SD / "trades.jsonl", {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "symbol": sym,
                                      "leg": p["leg"], "entry": p["entry"], "exit": r["avg"], "qty": r["filled"],
                                      "reason": why, "pnl": round(pnl, 4), "R_part": round(pnl / risk, 3) if risk else None,
                                      "opened": p["opened"], "mode": self.execu.mode})
        log.info("EXIT %s %s qty %.6g @ %.6g pnl %.3f USDT", sym, why, r["filled"], r["avg"], pnl)
        if p["qty"] * max(r["avg"], 1e-12) < 1 or why.startswith("stop") or why.startswith("close below"):
            self.positions.pop(sym, None)
        elif self.execu.mode == "live":                      # half sold: move the exchange stop to the rest
            p["stop_order"] = {"id": (await self.execu.place_stop(sym, p["qty"], p["stop"]) or {}).get("id")}
        self.guard.open_count = len(self.positions)
        self.save()

    # ------------------------------------------------------------------ main
    async def run(self):
        log.info("Bybit momentum bot starting in %s mode (%s%s)", self.execu.mode.upper(), C.MARKET,
                 ", TESTNET" if C.TESTNET else "")
        while not self.stop_event.is_set():
            self.universe = await load_universe(self.ex)
            syms = sorted(set(self.universe) | set(self.positions))
            await warm_up(self.ex, self.book, [s for s in syms if s not in self.book.bars])
            self.save()

            async def close_cb(sym):
                await self.on_close(sym)

            async def tick_loop():
                n = 0
                while True:
                    for s in list(self.positions):
                        await self.on_tick(s)
                    await asyncio.sleep(5)
                    n += 1
                    if n % 12 == 0:                         # heartbeat every minute
                        self.save()
                        log.info("heartbeat: %d candle closes processed, last %s, %d open, %d stalking",
                                 self.closes, self.last_close_ts, len(self.positions), len(self.stalkers))

            tasks = [asyncio.create_task(stream(self.ex, self.book, syms, close_cb, self.stop_event)),
                     asyncio.create_task(tick_loop())]
            await asyncio.sleep(C.UNIVERSE_REFRESH_MIN * 60)        # then refresh the universe
            for t in tasks:
                t.cancel()
            self.save()
        await self.ex.close()


def main():
    if "--status" in sys.argv:
        p = SD / "status.json"
        print(p.read_text(encoding="utf-8") if p.exists() else "no status yet")
        return
    live = "--live" in sys.argv
    if live and not (C.LIVE_TRADING and C.API_KEY and C.API_SECRET):
        print("Refusing live mode: needs BYBIT_API_KEY, BYBIT_API_SECRET and LIVE_TRADING=true in .env (try the testnet first).")
        sys.exit(4)
    setup_logging()
    bot = Bot(live)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
