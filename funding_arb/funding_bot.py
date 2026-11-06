"""Async funding-carry bot: long Binance spot + short USDT-M perp, paper mode by default.

    .venv\\Scripts\\python funding_bot.py              paper (real prices, order books and funding; no orders)
    .venv\\Scripts\\python funding_bot.py --status
    .venv\\Scripts\\python funding_bot.py --once       one loop, then exit (smoke test)
    .venv\\Scripts\\python funding_bot.py --live       needs BINANCE_API_KEY / BINANCE_API_SECRET and LIVE_TRADING=true
                                                     in .env; the live order path has never run: use the testnet
                                                     first (BINANCE_TESTNET=true)

Loop (every LOOP_SEC):
  1. scan: top-100 perps by 24h volume with a liquid spot pair; entry when the current rate AND the mean of the last
     24h of settlements are >= 0.03%/8h-equivalent (arb_core.entry_ok)
  2. open a pair: buy spot (IOC limit, capped slippage), then short exactly the filled quantity on the perp
     (isolated, 3x, then extra margin added up to 40% of notional)
  3. manage every pair: delta band 1% (arb_core.delta_fix), margin ratio -> top up or cut (arb_core.derisk_action),
     exit when the rate drops below 0.005%/8h, funding ledger from the exchange's income history (live) or from the
     settled rate x mark x quantity (paper)
  4. KILL file in this folder -> unwind every pair and stop
State: state/status.json, positions.json, ledger.jsonl (funding, fees, fills), bot.log
"""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import ccxt.async_support as ccxt

import arb_core as A

HERE = Path(__file__).resolve().parent
SD = HERE / "state"
SD.mkdir(exist_ok=True)
log = logging.getLogger("farb")


def env():
    e = {}
    f = HERE / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                e[k.strip()] = v.strip()
    e.update({k: v for k, v in os.environ.items() if k.startswith(("BINANCE_", "LIVE_", "PAPER_", "LOOP_"))})
    return e


E = env()
RULES = A.Rules()
LOOP_SEC = int(E.get("LOOP_SEC", 60))
PAPER_CAPITAL = float(E.get("PAPER_CAPITAL", 10_000))
MAX_SLIP = 0.002                      # IOC limit: at most 0.2% through the touch


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def jdump(path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)


def ledger(**kw):
    with (SD / "ledger.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"time": now(), **kw}, default=str) + "\n")


# --------------------------------------------------------------------------------------------- execution venues
class Venue:
    """Spot + perp access. Paper mode fills against the live order book (walking the levels) and keeps balances."""

    def __init__(self, live: bool):
        keys = {"apiKey": E.get("BINANCE_API_KEY"), "secret": E.get("BINANCE_API_SECRET")} if live else {}
        self.spot = ccxt.binance({"enableRateLimit": True, **keys})
        self.perp = ccxt.binanceusdm({"enableRateLimit": True, **keys})
        if E.get("BINANCE_TESTNET", "").lower() == "true":
            self.spot.set_sandbox_mode(True)
            self.perp.set_sandbox_mode(True)
        self.live = live
        self.mode = "live" if live else "paper"
        self.paper = {"spot_usdt": PAPER_CAPITAL, "fut_usdt": 0.0}

    async def close(self):
        await self.spot.close()
        await self.perp.close()

    @staticmethod
    def _walk(levels, qty, limit, side):
        got, cost = 0.0, 0.0
        for px, sz in levels:
            if (side == "buy" and px > limit) or (side == "sell" and px < limit):
                break
            take = min(sz, qty - got)
            got += take
            cost += take * px
            if got >= qty - 1e-12:
                break
        return got, (cost / got if got else 0.0)

    async def spot_order(self, sym, side, qty):
        ob = await self.spot.fetch_order_book(sym, 50)
        touch = ob["asks"][0][0] if side == "buy" else ob["bids"][0][0]
        limit = touch * (1 + MAX_SLIP) if side == "buy" else touch * (1 - MAX_SLIP)
        qty = float(self.spot.amount_to_precision(sym, qty))
        if self.live:
            o = await self.spot.create_order(sym, "limit", side, qty, float(self.spot.price_to_precision(sym, limit)),
                                             {"timeInForce": "IOC"})
            filled, avg, fee = float(o.get("filled") or 0), float(o.get("average") or 0), (o.get("fee") or {}).get("cost") or 0
        else:
            filled, avg = self._walk(ob["asks"] if side == "buy" else ob["bids"], qty, limit, side)
            fee = filled * avg * 0.001
            self.paper["spot_usdt"] += -filled * avg - fee if side == "buy" else filled * avg - fee
        ledger(kind="fill", market="spot", symbol=sym, side=side, qty=filled, avg=avg, fee=fee, mode=self.mode)
        return filled, avg, fee

    async def perp_order(self, sym, side, qty, reduce=False):
        ob = await self.perp.fetch_order_book(sym, 50)
        qty = float(self.perp.amount_to_precision(sym, qty))
        if qty <= 0:
            return 0.0, 0.0, 0.0
        if self.live:
            o = await self.perp.create_order(sym, "market", side, qty, None, {"reduceOnly": reduce})
            filled, avg = float(o.get("filled") or 0), float(o.get("average") or 0)
            fee = filled * avg * 0.0005
        else:
            touch = ob["asks"][0][0] if side == "buy" else ob["bids"][0][0]
            limit = touch * (1.02 if side == "buy" else 0.98)
            filled, avg = self._walk(ob["asks"] if side == "buy" else ob["bids"], qty, limit, side)
            fee = filled * avg * 0.0005
            self.paper["fut_usdt"] -= fee
        ledger(kind="fill", market="perp", symbol=sym, side=side, qty=filled, avg=avg, fee=fee, reduce=reduce, mode=self.mode)
        return filled, avg, fee

    async def move_to_futures(self, usdt):
        if self.live:
            await self.spot.transfer("USDT", usdt, "spot", "future")
        else:
            self.paper["spot_usdt"] -= usdt
            self.paper["fut_usdt"] += usdt

    async def prepare_perp(self, sym):
        if self.live:
            try:
                await self.perp.set_margin_mode("isolated", sym)
            except ccxt.BaseError as e:          # already isolated
                log.debug("margin mode %s: %s", sym, e)
            await self.perp.set_leverage(RULES.leverage, sym)

    async def lock_margin(self, sym, usdt, auto_part=0.0):
        """Put usdt into the isolated position. Live: the order already took auto_part as initial margin."""
        if self.live:
            extra = usdt - auto_part
            if extra > 0:
                await self.perp.add_margin(sym, extra)
        else:
            self.paper["fut_usdt"] -= usdt


# --------------------------------------------------------------------------------------------- the bot
class Bot:
    def __init__(self, live):
        self.v = Venue(live)
        st = {}
        try:
            st = json.loads((SD / "positions.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        self.pairs: dict = st.get("pairs", {})
        if "paper" in st and not live:
            self.v.paper = st["paper"]
        self.cands: list = []
        self.last_scan = None
        self.intervals: dict = {}             # symbol -> funding interval hours (Binance lists only non-8h ones)

    async def interval(self, sym):
        if not self.intervals:
            try:
                fi = await self.v.perp.fetch_funding_intervals()
                self.intervals = {s: float(x["info"].get("fundingIntervalHours") or 8) for s, x in fi.items()}
            except ccxt.BaseError as e:
                log.warning("funding intervals unavailable (%s): assuming 8h", e)
                self.intervals = {"_": 8.0}
        return self.intervals.get(sym, 8.0)

    # ---- persistence
    def save(self, extra=None):
        jdump(SD / "positions.json", {"pairs": self.pairs, "paper": self.v.paper})
        jdump(SD / "status.json", {"mode": self.v.mode, "updated": now(), "pairs": self.pairs,
                                   "paper_balances": self.v.paper if self.v.mode == "paper" else None,
                                   "equity": round(self.equity(), 2), "candidates": self.cands[:15],
                                   "rules": RULES.__dict__, **(extra or {})})

    def equity(self):
        if self.v.mode != "paper":
            return float("nan")
        eq = self.v.paper["spot_usdt"] + self.v.paper["fut_usdt"]
        for p in self.pairs.values():
            eq += p["spot_qty"] * p.get("spot_px", p["spot_entry"]) + p["margin"] + \
                  p["perp_qty"] * (p["perp_entry"] - p.get("mark", p["perp_entry"]))
        return eq

    # ---- 1. scan
    async def scan(self):
        self.intervals = {}                   # refreshed every scan (Binance changes intervals in volatile markets)
        await self.v.spot.load_markets()
        await self.v.perp.load_markets()
        spot_bases = {m["base"] for m in self.v.spot.markets.values() if m.get("spot") and m["quote"] == "USDT" and m.get("active")}
        perps = {s: m for s, m in self.v.perp.markets.items()
                 if m.get("swap") and m.get("linear") and m["quote"] == "USDT" and m.get("active")}
        pt = await self.v.perp.fetch_tickers(list(perps))
        top = sorted(pt, key=lambda s: -(pt[s].get("quoteVolume") or 0))[:RULES.top_n]
        st = await self.v.spot.fetch_tickers()
        rates = await self.v.perp.fetch_funding_rates(top)
        out = []
        for s in top:
            base = perps[s]["base"]
            sb, mult = A.spot_symbol(base, spot_bases)
            if not sb:
                continue
            ssym = f"{sb}/USDT"
            if (st.get(ssym, {}).get("quoteVolume") or 0) < RULES.min_spot_dvol:
                continue
            fr = rates.get(s) or {}
            iv = await self.interval(s)
            cur8 = A.rate8(float(fr.get("fundingRate") or 0), iv)
            if cur8 < RULES.entry_rate:
                continue
            hist = await self.v.perp.fetch_funding_rate_history(s, since=int((time.time() - 26 * 3600) * 1000), limit=50)
            h = [x for x in hist if x["timestamp"] >= (time.time() - 24 * 3600) * 1000]
            mean8 = sum(A.rate8(x["fundingRate"], iv) for x in h) / len(h) if h else 0.0
            out.append({"perp": s, "spot": ssym, "mult": mult, "rate8_now": cur8, "mean24_8": mean8,
                        "interval_h": iv, "entry_ok": A.entry_ok(RULES, cur8, mean8),
                        "next_funding": fr.get("fundingDatetime"), "mark": fr.get("markPrice")})
        self.cands = sorted(out, key=lambda x: -x["rate8_now"])
        self.last_scan = now()
        return [c for c in self.cands if c["entry_ok"]]

    # ---- 2. open
    async def open_pair(self, c):
        notional = A.pair_size(RULES, self.equity() if self.v.mode == "paper" else RULES.max_pair_usdt * RULES.slots,
                               self.v.paper["spot_usdt"] if self.v.mode == "paper" else RULES.max_pair_usdt * 1.4)
        if notional < 20:
            return
        ob = await self.v.spot.fetch_order_book(c["spot"], 5)
        spot_qty = notional / ob["asks"][0][0]
        filled, s_avg, _ = await self.v.spot_order(c["spot"], "buy", spot_qty)
        if filled <= 0:
            log.info("OPEN %s: spot not filled", c["perp"])
            return
        perp_qty = filled / c["mult"]                         # 1000PEPE perp: 1 contract unit = 1000 PEPE
        await self.v.prepare_perp(c["perp"])
        p_filled, p_avg, _ = await self.v.perp_order(c["perp"], "sell", perp_qty)
        margin = RULES.margin_frac * p_filled * p_avg
        await self.v.move_to_futures(margin)
        await self.v.lock_margin(c["perp"], margin, p_filled * p_avg / RULES.leverage)
        self.pairs[c["perp"]] = {"spot": c["spot"], "mult": c["mult"], "spot_qty": filled, "spot_entry": s_avg,
                                 "perp_qty": p_filled, "perp_entry": p_avg, "margin": margin, "opened": now(),
                                 "funding": 0.0, "last_funding_ts": int(time.time() * 1000), "derisks": 0,
                                 "entry_rate8": c["rate8_now"], "next_funding": c["next_funding"]}
        log.info("OPEN %s: spot %.6g @ %.6g, perp short %.6g @ %.6g, margin %.2f", c["perp"], filled, s_avg,
                 p_filled, p_avg, margin)
        self.save()

    # ---- 3. manage
    async def manage(self, sym):
        p = self.pairs[sym]
        fr = await self.v.perp.fetch_funding_rate(sym)
        mark = float(fr.get("markPrice") or p["perp_entry"])
        sp = await self.v.spot.fetch_ticker(p["spot"])
        p["mark"], p["spot_px"] = mark, float(sp["last"]) * p["mult"]
        # funding ledger
        if self.v.mode == "live":
            inc = await self.v.perp.fetch_funding_history(sym, since=p["last_funding_ts"] + 1)
            for x in inc:
                p["funding"] += float(x["amount"])
                p["margin"] += float(x["amount"])
                p["last_funding_ts"] = max(p["last_funding_ts"], x["timestamp"])
                ledger(kind="funding", symbol=sym, amount=x["amount"], mode="live")
        elif p.get("next_funding") and now() >= str(p["next_funding"])[:19]:
            hist = await self.v.perp.fetch_funding_rate_history(sym, limit=1)
            if hist and hist[-1]["timestamp"] > p["last_funding_ts"]:
                amt = p["perp_qty"] * mark * float(hist[-1]["fundingRate"])
                p["funding"] += amt
                p["margin"] += amt
                p["last_funding_ts"] = hist[-1]["timestamp"]
                ledger(kind="funding", symbol=sym, rate=hist[-1]["fundingRate"], amount=amt, mode="paper")
            p["next_funding"] = fr.get("fundingDatetime")
        # delta band (spot quantity in perp units)
        step = (self.v.perp.markets[sym].get("precision") or {}).get("amount") or 0.001
        fix = A.delta_fix(RULES, p["spot_qty"] / p["mult"], p["perp_qty"], float(step))
        if fix:
            side = "sell" if fix > 0 else "buy"
            q, _, _ = await self.v.perp_order(sym, side, abs(fix), reduce=fix < 0)
            p["perp_qty"] += q if fix > 0 else -q
            log.info("DELTA %s: perp %s %.6g", sym, side, q)
        # margin guard
        ratio = A.margin_ratio(p["margin"], p["perp_qty"], p["perp_entry"], mark)
        act = A.derisk_action(RULES, ratio)
        if act != "ok":
            q2, dq = A.topup_resize(p["perp_qty"], p["margin"], p["perp_entry"], p["spot_px"], mark, RULES.margin_frac)
            if act == "cut":
                dq = max(dq, p["perp_qty"] * 0.5)
            log.warning("DERISK %s (%s): margin ratio %.3f, cutting %.6g of %.6g", sym, act, ratio, dq, p["perp_qty"])
            s_q, s_avg, _ = await self.v.spot_order(p["spot"], "sell", dq * p["mult"])
            p_q, p_avg, _ = await self.v.perp_order(sym, "buy", s_q / p["mult"], reduce=True)
            realized = p_q * (p["perp_entry"] - p_avg)
            p["margin"] += realized
            p["spot_qty"] -= s_q
            p["perp_qty"] -= p_q
            want = RULES.margin_frac * p["perp_qty"] * mark
            unreal = p["perp_qty"] * (p["perp_entry"] - mark)
            add = max(0.0, want - (p["margin"] + unreal))
            if add:
                await self.v.move_to_futures(add)
                await self.v.lock_margin(sym, add)
                p["margin"] += add
            p["derisks"] += 1
            ledger(kind="derisk", symbol=sym, action=act, ratio=ratio, cut_qty=p_q, margin_added=add, mode=self.v.mode)
        # exit
        iv = await self.interval(sym)
        if A.exit_ok(RULES, A.rate8(float(fr.get("fundingRate") or 0), iv)):
            await self.close_pair(sym, "funding below exit level")

    async def close_pair(self, sym, why):
        p = self.pairs[sym]
        p_q, p_avg, _ = await self.v.perp_order(sym, "buy", p["perp_qty"], reduce=True)
        s_q, s_avg, _ = await self.v.spot_order(p["spot"], "sell", p["spot_qty"])
        perp_pnl = p_q * (p["perp_entry"] - p_avg)
        if self.v.mode == "paper":
            self.v.paper["fut_usdt"] += p["margin"] + perp_pnl
        ledger(kind="close", symbol=sym, why=why, funding=p["funding"], perp_pnl=perp_pnl,
               spot_pnl=s_q * (s_avg - p["spot_entry"]), mode=self.v.mode)
        log.info("CLOSE %s (%s): funding %.3f, perp %.3f, spot %.3f", sym, why, p["funding"], perp_pnl,
                 s_q * (s_avg - p["spot_entry"]))
        if self.v.mode == "paper":                         # sweep futures wallet back to spot
            self.v.paper["spot_usdt"] += self.v.paper["fut_usdt"]
            self.v.paper["fut_usdt"] = 0.0
        del self.pairs[sym]
        self.save()

    # ---- main loop
    async def run(self, once=False):
        log.info("funding-carry bot starting in %s mode", self.v.mode.upper())
        try:
            while True:
                if (HERE / "KILL").exists():
                    log.warning("KILL file: unwinding %d pairs", len(self.pairs))
                    for s in list(self.pairs):
                        await self.close_pair(s, "KILL file")
                    break
                try:
                    for s in list(self.pairs):
                        await self.manage(s)
                    ready = await self.scan()
                    for c in ready:
                        if len(self.pairs) >= RULES.slots:
                            break
                        if c["perp"] not in self.pairs:
                            await self.open_pair(c)
                    self.save()
                    log.info("heartbeat: %d pairs, %d candidates (%d pass), equity %.2f", len(self.pairs),
                             len(self.cands), len(ready), self.equity())
                except (ccxt.NetworkError, ccxt.ExchangeError) as e:
                    log.error("exchange error: %s", e)
                if once:
                    break
                await asyncio.sleep(LOOP_SEC)
        finally:
            self.save()
            await self.v.close()


def main():
    if "--status" in sys.argv:
        f = SD / "status.json"
        print(f.read_text(encoding="utf-8") if f.exists() else "no status yet")
        return
    live = "--live" in sys.argv
    if live and not (E.get("LIVE_TRADING", "").lower() == "true" and E.get("BINANCE_API_KEY") and E.get("BINANCE_API_SECRET")):
        print("Refusing live mode: needs BINANCE_API_KEY, BINANCE_API_SECRET and LIVE_TRADING=true in .env "
              "(use BINANCE_TESTNET=true first).")
        sys.exit(4)
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for h in (logging.FileHandler(SD / "bot.log", encoding="utf-8"), logging.StreamHandler(sys.stdout)):
        h.setFormatter(fmt)
        log.addHandler(h)
    try:
        asyncio.run(Bot(live).run(once="--once" in sys.argv))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
