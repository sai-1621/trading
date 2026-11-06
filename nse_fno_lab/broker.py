"""Broker adapters used by risk.OrderGuard.

PaperBroker - fills against quotes you feed it, with optional simulated rejects / partial fills / timeouts
              (used by the tests and by a paper runner).
AngelBroker - Angel One SmartAPI: orders, order status, option chain with open interest, index/VIX quotes.
              NOT exercised yet: no Angel One keys are set. Test with the paper runner first.
"""
import json
import time
from datetime import date, datetime

import requests

import config as C
from risk import Fill, Order

SCRIP_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
INDEX_TOKENS = {"NIFTY": ("NSE", "99926000"), "SENSEX": ("BSE", "99919000"), "INDIAVIX": ("NSE", "99926017"),
                "BANKNIFTY": ("NSE", "99926009"), "FINNIFTY": ("NSE", "99926037")}


class PaperBroker:
    def __init__(self, quotes: dict | None = None, reject: set | None = None, partial: dict | None = None,
                 timeout_once: set | None = None, slippage_pct: float = C.SLIPPAGE_PCT):
        self.quotes = quotes or {}           # symbol -> last price
        self.reject = reject or set()        # symbols whose orders are rejected
        self.partial = partial or {}         # symbol -> fraction filled per order
        self.timeout_once = timeout_once or set()
        self.slip = slippage_pct / 100
        self.orders: dict[str, dict] = {}
        self.n = 0

    def place(self, o: Order) -> str:
        self.n += 1
        oid = f"P{self.n}"
        q = self.quotes[o.symbol]
        px = q * (1 + self.slip) if o.side == "BUY" else q * (1 - self.slip)
        if o.symbol in self.reject:
            st = Fill("rejected", 0, 0.0, oid, "RMS: rejected")
        elif o.price is not None and ((o.side == "BUY" and o.price < q) or (o.side == "SELL" and o.price > q)):
            st = Fill("open", 0, 0.0, oid)             # limit away from the market rests unfilled
        else:
            if o.price is not None:
                px = min(px, o.price) if o.side == "BUY" else max(px, o.price)
            frac = self.partial.get(o.symbol, 1.0)
            filled = int(o.qty * frac)
            st = Fill("complete" if filled == o.qty else ("open" if filled else "open"), filled, px, oid)
        self.orders[oid] = {"order": o, "fill": st}
        if o.symbol in self.timeout_once:
            self.timeout_once.discard(o.symbol)
            raise TimeoutError("simulated timeout")
        return oid

    def status(self, oid: str) -> Fill:
        f = self.orders[oid]["fill"]
        return Fill(f.status, f.filled, f.avg_price, oid, f.reason)

    def cancel(self, oid: str):
        f = self.orders[oid]["fill"]
        if f.status == "open":
            f.status = "cancelled" if f.filled == 0 else "partial"

    def find_by_tag(self, tag: str) -> str | None:
        for oid, d in self.orders.items():
            if d["order"].tag == tag:
                return oid
        return None


def load_option_master(symbol: str) -> list[dict]:
    """Angel One scrip master rows for index options of `symbol` (cached for a day)."""
    f = C.CACHE_DIR / f"options_{symbol}.json"
    if f.exists() and time.time() - f.stat().st_mtime < 86400:
        return json.loads(f.read_text())
    seg = "BFO" if C.EXCHANGE_OF[symbol] == "BSE" else "NFO"
    rows = [r for r in requests.get(SCRIP_URL, timeout=120).json()
            if r.get("exch_seg") == seg and r.get("instrumenttype") == "OPTIDX" and r.get("name") == symbol]
    out = [{"symbol": r["symbol"], "token": r["token"], "exchange": seg,
            "expiry": datetime.strptime(r["expiry"], "%d%b%Y").date().isoformat(),
            "strike": float(r["strike"]) / 100, "cp": 1 if r["symbol"].endswith("CE") else -1,
            "lot": int(r["lotsize"]), "tick": float(r.get("tick_size") or 5) / 100} for r in rows]
    f.write_text(json.dumps(out))
    return out


class AngelBroker:
    def __init__(self):
        import pyotp
        from SmartApi import SmartConnect
        missing = [k for k in ("ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET")
                   if not getattr(C, k)]
        if missing:
            raise RuntimeError("Missing in .env: " + ", ".join(missing))
        self.api = SmartConnect(api_key=C.ANGEL_API_KEY)
        res = self.api.generateSession(C.ANGEL_CLIENT_CODE, C.ANGEL_PIN, pyotp.TOTP(C.ANGEL_TOTP_SECRET).now())
        if not res or not res.get("status"):
            raise RuntimeError(f"Angel One login failed: {res}")

    # ---- orders ----
    def place(self, o: Order) -> str:
        if not C.LIVE_TRADING:
            raise RuntimeError("LIVE_TRADING is off: use PaperBroker")
        p = {"variety": "NORMAL", "tradingsymbol": o.symbol, "symboltoken": o.token, "transactiontype": o.side,
             "exchange": o.exchange, "ordertype": "MARKET" if o.price is None else "LIMIT",
             "producttype": "CARRYFORWARD" if o.exchange in ("NFO", "BFO") else "INTRADAY",
             "duration": "DAY", "price": "0" if o.price is None else f"{o.price:.2f}", "quantity": str(o.qty),
             "ordertag": o.tag[:20]}
        res = self.api.placeOrderFullResponse(p)
        if not res or not res.get("status"):
            raise RuntimeError(str(res))
        return res["data"]["uniqueorderid"]

    def status(self, uid: str) -> Fill:
        d = (self.api.individual_order_details(uid) or {}).get("data") or {}
        st, filled = d.get("status", "unknown"), int(float(d.get("filledshares") or 0))
        if st not in ("complete", "rejected", "cancelled"):
            st = "open"
        if st == "cancelled" and filled:
            st = "partial"
        return Fill(st, filled, float(d.get("averageprice") or 0), uid, d.get("text", ""))

    def cancel(self, uid: str):
        d = (self.api.individual_order_details(uid) or {}).get("data") or {}
        if d.get("orderid"):
            self.api.cancelOrder(d["orderid"], "NORMAL")

    def find_by_tag(self, tag: str) -> str | None:
        for r in (self.api.orderBook() or {}).get("data") or []:
            if r.get("ordertag") == tag[:20]:
                return r.get("uniqueorderid")
        return None

    # ---- market data ----
    def quotes(self, exchange: str, tokens: list[str]) -> dict:
        """token -> {'ltp', 'oi', 'bid', 'ask', 'volume'} (FULL mode, max 50 tokens per call)."""
        out = {}
        for i in range(0, len(tokens), 50):
            res = self.api.getMarketData("FULL", {exchange: tokens[i:i + 50]})
            for r in ((res or {}).get("data") or {}).get("fetched", []):
                dp = r.get("depth") or {}
                out[r["symbolToken"]] = {"ltp": float(r["ltp"]), "oi": float(r.get("opnInterest") or 0),
                                         "volume": float(r.get("tradeVolume") or 0),
                                         "bid": float((dp.get("buy") or [{}])[0].get("price") or 0),
                                         "ask": float((dp.get("sell") or [{}])[0].get("price") or 0)}
            time.sleep(0.35)
        return out

    def option_chain(self, symbol: str, expiry: date, spot: float, strikes_each_side: int = 8) -> list[dict]:
        step = C.STRIKE_STEP[symbol]
        atm = round(spot / step) * step
        want = {atm + k * step for k in range(-strikes_each_side, strikes_each_side + 1)}
        rows = [r for r in load_option_master(symbol) if r["expiry"] == expiry.isoformat() and r["strike"] in want]
        q = self.quotes(rows[0]["exchange"], [r["token"] for r in rows]) if rows else {}
        return [{**r, **q.get(r["token"], {})} for r in rows]

    def index_ltp(self, name: str) -> float:
        exch, tok = INDEX_TOKENS[name]
        return float(self.api.ltpData(exch, name, tok)["data"]["ltp"])
