"""Go-live checks for the SIP bot. With LIVE_TRADING=true it refuses to start until all pass.

    python readiness.py

  1. Angel One login works (API key, client code, PIN, TOTP secret in .env)
  2. This computer's public IP is the STATIC_IP registered with Angel One (SEBI rule for API orders)
  3. A real 1-unit test order of the first ETF was filled: python test_order.py (market hours)
"""
import json

import requests

import config


def _angel():
    if not all([config.ANGEL_API_KEY, config.ANGEL_CLIENT_CODE, config.ANGEL_PIN, config.ANGEL_TOTP_SECRET]):
        return False, "Angel One keys missing in .env"
    try:
        from angel_client import AngelClient
        return True, f"login works, available cash Rs {AngelClient().available_cash():,.2f}"
    except Exception as e:
        return False, f"login failed: {e}"


def _ip():
    if not config.STATIC_IP:
        return False, "STATIC_IP missing in .env"
    try:
        ip = requests.get("https://api.ipify.org", timeout=10).text.strip()
    except requests.RequestException as e:
        return False, f"cannot read public IP: {e}"
    return ip == config.STATIC_IP, f"public IP {ip}, registered {config.STATIC_IP}"


def _test():
    f = config.TRADER_DIR / "test_order.json"
    if not f.exists():
        return False, "not done: python test_order.py during market hours"
    d = json.loads(f.read_text(encoding="utf-8"))
    return bool(d.get("ok")), d.get("detail", "")


def check():
    return [("Angel One login", *_angel()), ("Static IP matches", *_ip()), ("Test order filled", *_test())]


def report(results):
    lines = [f"  [{'PASS' if ok else 'FAIL'}] {n}: {d}" for n, ok, d in results]
    ok = all(r[1] for r in results)
    lines.append("  READY for real money." if ok else "  NOT READY. Keep paper mode.")
    return ok, "\n".join(lines)


if __name__ == "__main__":
    print(report(check())[1])
