"""Margin check for the paper iron flies (read-only: no orders).

Asks Angel One's margin calculator (SmartAPI getMarginApi) how much margin 1 lot of a NIFTY iron fly needs on the
next expiry, at the strike nearest to spot, for the bot's two wing widths (150 and 200). Also reads the four legs'
last prices to work out the credit and the most the fly can lose. Writes logs/fly_margin.json for the dashboard.

Uses the Angel One bot's login (angelone_bot/.env), so run it with that bot's Python:
    D:\\PyCharmMiscProject\\begin\\angelone_bot\\.venv\\Scripts\\python scripts\\fly_margin.py
"""
import json
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests

LAB = Path(__file__).resolve().parent.parent
ANGEL_BOT = LAB.parent / "angelone_bot"
sys.path.insert(0, str(ANGEL_BOT))
import angel_client as A  # noqa: E402  (angelone_bot)

try:  # SmartAPI's own error log prints request headers, including the API key: keep it quiet
    import logzero
    logzero.loglevel(logging.CRITICAL)
except ImportError:
    pass

OUT = LAB / "logs" / "fly_margin.json"
CACHE = LAB / "cache" / "options_NIFTY.json"
WIDTHS = (150, 200)
STEP = 50


def option_master():
    if CACHE.exists() and time.time() - CACHE.stat().st_mtime < 86400:
        return json.loads(CACHE.read_text())
    rows = [r for r in requests.get(A.SCRIP_URL, timeout=180).json()
            if r.get("exch_seg") == "NFO" and r.get("instrumenttype") == "OPTIDX" and r.get("name") == "NIFTY"]
    out = [{"symbol": r["symbol"], "token": r["token"], "exchange": "NFO",
            "expiry": datetime.strptime(r["expiry"], "%d%b%Y").date().isoformat(),
            "strike": float(r["strike"]) / 100, "cp": 1 if r["symbol"].endswith("CE") else -1,
            "lot": int(r["lotsize"])} for r in rows]
    CACHE.write_text(json.dumps(out))
    return out


def main():
    cl = A.AngelClient()
    spot = float(cl._ok(cl._request(lambda api: api.ltpData("NSE", "Nifty 50", "99926000"), "NIFTY spot",
                                    gap=A.LTP_GAP), "NIFTY spot")["ltp"])
    master = option_master()
    expiry = min(r["expiry"] for r in master if r["expiry"] >= date.today().isoformat())
    chain = {(r["strike"], r["cp"]): r for r in master if r["expiry"] == expiry}
    K = round(spot / STEP) * STEP
    out = {"checked": datetime.now().isoformat(timespec="seconds"), "expiry": expiry, "spot": spot, "K": K,
           "product": "INTRADAY", "source": "Angel One margin calculator (getMarginApi)", "flies": {}}
    for W in WIDTHS:
        legs = [("SELL", K, 1), ("SELL", K, -1), ("BUY", K + W, 1), ("BUY", K - W, -1)]
        lot = chain[(K, 1)]["lot"]
        pos, prices = [], {}
        for side, k, cp in legs:
            r = chain[(float(k), cp)]
            ltp = float(cl._ok(cl._request(lambda api: api.ltpData("NFO", r["symbol"], r["token"]), r["symbol"],
                                           gap=A.LTP_GAP), r["symbol"])["ltp"])
            prices[r["symbol"]] = ltp
            pos.append({"exchange": "NFO", "qty": lot, "price": 0, "productType": "INTRADAY", "orderType": "MARKET",
                        "token": r["token"], "tradeType": side})
        res = cl._request(lambda api: api.getMarginApi({"positions": pos}), f"margin W{W}")
        d = cl._ok(res, f"margin W{W}")
        credit = sum(p if s == "SELL" else -p for (s, _, _), p in zip(legs, prices.values()))
        out["flies"][str(W)] = {"lot": lot, "legs": prices, "credit_per_unit": round(credit, 2),
                                "credit_rs": round(credit * lot, 2), "max_loss_rs": round((W - credit) * lot, 2),
                                "margin_rs": round(float(d.get("totalMarginRequired") or 0), 2),
                                "components": d.get("marginComponents")}
        f = out["flies"][str(W)]
        print(f"W{W}: K {K} expiry {expiry} lot {lot}  credit {credit:.2f}/unit (Rs {f['credit_rs']:,.0f})  "
              f"max loss Rs {f['max_loss_rs']:,.0f}  margin Rs {f['margin_rs']:,.0f}")
    OUT.write_text(json.dumps(out, indent=1))
    print(f"saved {OUT}")


if __name__ == "__main__":
    main()
