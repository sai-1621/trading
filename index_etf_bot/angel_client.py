"""Angel One SmartAPI wrapper (pip package smartapi-python). Docs: https://smartapi.angelone.in/docs

Logs in with client code + PIN + a TOTP generated from your authenticator secret, so no one has to type a code.
Sessions expire daily; the bot logs in again each trading day. Orders must come from the static IP you
registered with Angel One (SEBI rule for API trading, effective April 2026).
"""
import json
import logging
import time
from datetime import datetime, timedelta

import pyotp
import requests
from SmartApi import SmartConnect

import config
from market import IST, now

log = logging.getLogger("bot")
SCRIP_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"


class AngelError(Exception):
    pass


def load_tokens() -> dict:
    """{"RELIANCE": {"token": "2885", "tradingsymbol": "RELIANCE-EQ", "tick": 0.05}} for NSE equities, cached daily."""
    f = config.CACHE_DIR / "angel_tokens.json"
    if f.exists() and time.time() - f.stat().st_mtime < 86400:
        return json.loads(f.read_text())
    rows = requests.get(SCRIP_URL, timeout=120).json()
    out = {}
    for r in rows:
        if r.get("exch_seg") == "NSE" and r.get("symbol", "").endswith("-EQ"):
            out[r["name"]] = {"token": r["token"], "tradingsymbol": r["symbol"],
                              "tick": float(r.get("tick_size") or 5) / 100}
    f.write_text(json.dumps(out))
    return out


class AngelClient:
    def __init__(self):
        missing = [k for k in ("ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET") if not getattr(config, k)]
        if missing:
            raise AngelError("Missing in .env: " + ", ".join(missing))
        self.tokens = load_tokens()
        self.api = None
        self.login_day = None
        self.login()

    def login(self):
        self.api = SmartConnect(api_key=config.ANGEL_API_KEY)
        totp = pyotp.TOTP(config.ANGEL_TOTP_SECRET).now()
        res = self.api.generateSession(config.ANGEL_CLIENT_CODE, config.ANGEL_PIN, totp)
        if not res or not res.get("status"):
            raise AngelError(f"Angel One login failed: {res}")
        self.login_day = now().date()
        log.info("Angel One: logged in as %s", config.ANGEL_CLIENT_CODE)

    def ensure_session(self):
        if self.login_day != now().date():
            self.login()

    def _ok(self, res, what):
        if not res or not res.get("status"):
            raise AngelError(f"{what} failed: {res}")
        return res["data"]

    def scrip(self, symbol: str) -> dict:
        s = self.tokens.get(symbol)
        if not s:
            raise AngelError(f"{symbol} not found in Angel One's NSE equity list")
        return s

    # ---- market data ----
    def ltp(self, symbol: str) -> float:
        self.ensure_session()
        s = self.scrip(symbol)
        d = self._ok(self.api.ltpData("NSE", s["tradingsymbol"], s["token"]), f"LTP {symbol}")
        return float(d["ltp"])

    def candles(self, symbol: str, interval: str = "FIVE_MINUTE", days: int = 5) -> list[dict]:
        self.ensure_session()
        s = self.scrip(symbol)
        end = now()
        start = end - timedelta(days=days + 3)  # weekends and holidays
        params = {"exchange": "NSE", "symboltoken": s["token"], "interval": interval,
                  "fromdate": start.strftime("%Y-%m-%d 09:15"), "todate": end.strftime("%Y-%m-%d %H:%M")}
        for attempt in range(3):
            res = self.api.getCandleData(params)
            if res and res.get("status"):
                break
            time.sleep(1 + attempt)  # rate limit: a few requests per second
        rows = self._ok(res, f"candles {symbol}") or []
        return [{"t": datetime.fromisoformat(r[0]).astimezone(IST), "o": float(r[1]), "h": float(r[2]),
                 "l": float(r[3]), "c": float(r[4]), "v": float(r[5])} for r in rows]

    # ---- account ----
    def available_cash(self) -> float:
        self.ensure_session()
        d = self._ok(self.api.rmsLimit(), "funds")
        return float(d.get("availablecash") or d.get("net") or 0)

    def positions(self) -> list:
        self.ensure_session()
        return self.api.position().get("data") or []

    # ---- orders ----
    def market_order(self, symbol: str, side: str, qty: int, tag: str = "bot") -> dict:
        """Places an intraday market order and waits for the fill. Returns {"status", "avg_price", "filled"}."""
        self.ensure_session()
        s = self.scrip(symbol)
        params = {
            "variety": "NORMAL", "tradingsymbol": s["tradingsymbol"], "symboltoken": s["token"],
            "transactiontype": side.upper(), "exchange": "NSE", "ordertype": "MARKET",
            "producttype": config.PRODUCT, "duration": "DAY", "price": "0", "quantity": str(int(qty)),
            "ordertag": tag[:20],
        }
        res = self.api.placeOrderFullResponse(params)
        d = self._ok(res, f"{side} {qty} {symbol}")
        uid = d.get("uniqueorderid")
        st = {}
        for _ in range(15):
            st = (self.api.individual_order_details(uid) or {}).get("data") or {}
            if st.get("status") in ("complete", "rejected", "cancelled"):
                break
            time.sleep(1)
        return {"status": st.get("status"), "avg_price": float(st.get("averageprice") or 0),
                "filled": int(float(st.get("filledshares") or 0)), "orderid": d.get("orderid"),
                "text": st.get("text", ""), "raw": st}
