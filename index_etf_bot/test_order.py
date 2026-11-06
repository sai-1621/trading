"""One REAL delivery buy of 1 unit of the first ETF in ETFS, to check that live orders work. It is kept (not sold):
it simply becomes part of your holdings. Run during market hours (9:15-15:30 IST)."""
import json
import sys

import config
import market

if not market.is_open():
    sys.exit("The market is closed. Run this between 9:15 and 15:30 IST on a trading day.")
from angel_client import AngelClient

etf = next(iter(config.ETFS))
a = AngelClient()
px = a.ltp(etf)
print(f"This places a REAL delivery BUY of 1 {etf} (about Rs {px:.2f}). The unit stays in your demat account.")
if input("Type YES to go ahead: ").strip() != "YES":
    sys.exit("Cancelled.")
res = a.market_order(etf, "buy", 1, tag="sip-test")
print({k: v for k, v in res.items() if k != "raw"})
ok = res["status"] == "complete"
(config.TRADER_DIR / "test_order.json").write_text(json.dumps(
    {"ok": ok, "time": market.now().isoformat(), "detail": f"1 {etf}: {res['status']} @ {res['avg_price']}"}), encoding="utf-8")
print("PASSED" if ok else "FAILED")
