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


# Index tokens in Angel One's scrip master (exch_seg NSE, instrumenttype AMXIDX), keyed by the Yahoo symbol the bot uses
INDEX_TOKENS = {"^NSEI": "99926000"}            # Nifty 50
# Longest range Angel One returns in one historical request, per interval (SmartAPI docs)
MAX_DAYS = {"ONE_MINUTE": 30, "THREE_MINUTE": 60, "FIVE_MINUTE": 100, "TEN_MINUTE": 100, "FIFTEEN_MINUTE": 200,
            "THIRTY_MINUTE": 200, "ONE_HOUR": 400, "ONE_DAY": 2000}
SESSION_ERRORS = {"AG8001", "AG8002", "AG8003"}   # invalid / expired / missing session token
FATAL_ERRORS = {"AG8004"}                       # invalid API key
# seconds between requests. Candles: measured on 2 Nov 2026, 0.4 s still hit "exceeding access rate" now and
# then, so 1 s; live prices (LTP) have a looser limit.
HIST_GAP = 1.0
LTP_GAP = 0.4
_last_hist = [0.0]


def _throttle(gap=None):
    wait = (HIST_GAP if gap is None else gap) - (time.time() - _last_hist[0])
    if wait > 0:
        time.sleep(wait)
    _last_hist[0] = time.time()


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
        if not res or not (res.get("status") or res.get("success")) or "data" not in res:
            raise AngelError(f"{what} failed: {res}")
        return res["data"]

    def _request(self, fn, what, tries=5, gap=None):
        """A market-data call with Angel One's rate limit in mind: spaced requests, and on "exceeding access rate"
        (which SmartAPI raises as an exception, not a reply) or a failed reply, wait 1.5, 3, 6, 8 s and retry."""
        res, err = None, None
        for attempt in range(tries):
            _throttle(gap)
            try:
                res, err = self._call(fn), None
            except Exception as e:  # noqa: BLE001 - SmartAPI raises plain exceptions for non-JSON replies
                res, err = None, e
            if res and (res.get("status") or res.get("success")):
                return res
            code = (res.get("errorcode") or res.get("errorCode")) if isinstance(res, dict) else None
            if code in FATAL_ERRORS:
                return res                    # a wrong key or symbol will not fix itself: no point retrying
            if attempt < tries - 1:
                time.sleep(min(8.0, 1.5 * 2 ** attempt))
        if res is None and err is not None:
            raise AngelError(f"{what} failed: {err}")
        return res

    def _call(self, fn):
        """fn(api) -> response. If Angel One says the session token is invalid or expired (it can end a session
        before the day is over), log in once more and retry, instead of failing until tomorrow's login."""
        res = fn(self.api)
        code = (res.get("errorcode") or res.get("errorCode")) if isinstance(res, dict) else None
        if isinstance(res, dict) and not (res.get("status") or res.get("success")) and code in SESSION_ERRORS:
            log.warning("Angel One session ended (%s %s): logging in again", code, res.get("message"))
            self.login()
            res = fn(self.api)
        return res

    def scrip(self, symbol: str) -> dict:
        s = self.tokens.get(symbol)
        if not s:
            raise AngelError(f"{symbol} not found in Angel One's NSE equity list")
        return s

    # ---- market data ----
    def ltp(self, symbol: str) -> float:
        self.ensure_session()
        s = self.scrip(symbol)
        d = self._ok(self._request(lambda api: api.ltpData("NSE", s["tradingsymbol"], s["token"]), f"LTP {symbol}",
                                   gap=LTP_GAP),
                     f"LTP {symbol}")
        return float(d["ltp"])

    def candles(self, symbol: str, interval: str = "FIVE_MINUTE", days: int = 5, tries: int = 5) -> list[dict]:
        """Bars from Angel One's historical API, oldest first. symbol: an NSE equity (RELIANCE) or an index in
        INDEX_TOKENS (^NSEI). Long ranges are split into requests Angel One accepts (MAX_DAYS per interval)."""
        self.ensure_session()
        token = INDEX_TOKENS[symbol] if symbol in INDEX_TOKENS else self.scrip(symbol)["token"]
        end = now()
        start = end - timedelta(days=days + 3)  # weekends and holidays
        step = timedelta(days=MAX_DAYS.get(interval, 30))
        out, a = {}, start
        while a < end:
            b = min(a + step, end)
            params = {"exchange": "NSE", "symboltoken": token, "interval": interval,
                      "fromdate": a.strftime("%Y-%m-%d 09:15"), "todate": b.strftime("%Y-%m-%d %H:%M")}
            res = self._request(lambda api: api.getCandleData(params), f"candles {symbol}", tries=tries)
            for r in self._ok(res, f"candles {symbol}") or []:
                t = datetime.fromisoformat(r[0]).astimezone(IST)
                out[t] = {"t": t, "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]),
                          "v": float(r[5])}
            a = b
        return [out[t] for t in sorted(out)]

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
