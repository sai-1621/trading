"""TradingView webhook receiver. Runs inside the bot (TV_WEBHOOK=true) on http://0.0.0.0:TV_PORT/tv

TradingView alert -> this server -> a queue -> the bot runs the SAME checks on it (exit plan, Time Machine,
budget, risk) before anything is bought. A sell/exit alert closes an open position (exits are never blocked).

Alert message (JSON), for example:
  {"secret": "<TV_SECRET>", "symbol": "{{ticker}}", "action": "buy", "price": {{close}}}
  {"secret": "<TV_SECRET>", "symbol": "{{ticker}}", "action": "exit"}
Optional fields: "stop" and "target" (prices). Without a stop, the bot's own STOP_RULE is used.

TradingView only posts to ports 80/443 on a public address, so expose this port with a tunnel
(cloudflared or ngrok) and paste that https URL + /tv into the alert's Webhook URL box.
"""
import hmac
import json
import logging
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
from market import now

log = logging.getLogger("bot")
signals: "queue.Queue[dict]" = queue.Queue()


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code, text):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(text.encode())

    def do_POST(self):
        if self.path.rstrip("/") != "/tv":
            return self._reply(404, "not found")
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(min(n, 10_000)) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._reply(400, "bad json")
        if not config.TV_SECRET or not hmac.compare_digest(str(body.get("secret", "")), config.TV_SECRET):
            log.warning("TradingView webhook: rejected (wrong secret) from %s", self.client_address[0])
            return self._reply(403, "forbidden")
        sym = str(body.get("symbol", "")).upper().split(":")[-1].replace("-", ".").strip()
        action = str(body.get("action", "")).lower()
        if not sym or action not in ("buy", "sell", "exit", "close"):
            return self._reply(400, "need symbol and action buy/exit")
        sig = {"symbol": sym, "action": "buy" if action == "buy" else "exit", "received_at": now().isoformat()}
        for k in ("price", "stop", "target"):
            try:
                if body.get(k) not in (None, ""):
                    sig[k] = float(body[k])
            except (TypeError, ValueError):
                pass
        signals.put(sig)
        log.info("TradingView webhook: %s %s queued", sig["action"].upper(), sym)
        return self._reply(200, "ok")

    def do_GET(self):
        self._reply(200, "IBKR US bot webhook is up. POST alerts to /tv")

    def log_message(self, *args):  # keep the bot log clean
        pass


def start():
    if not config.TV_SECRET:
        log.error("TV_WEBHOOK is on but TV_SECRET is empty in .env: webhook not started")
        return None
    server = ThreadingHTTPServer(("0.0.0.0", config.TV_PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("TradingView webhook listening on port %d at /tv", config.TV_PORT)
    return server
