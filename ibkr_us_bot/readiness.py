"""Go-live gate. The bot refuses to trade real money until every check passes.

    python readiness.py

  1. Backtest    trader/backtest.json under 7 days old; the best settings made money after charges on the unseen
                 "check" period (>=10 trades) and are the settings in .env
  2. Paper       >= 30 closed paper trades over >= 28 days, total PnL and average R above zero
  3. Test order  bot.py --test-order filled a real buy and sell on the LIVE account
  4. IBKR live   TWS/Gateway reachable on IB_PORT and logged into a live account (paper accounts start with DU)
"""
import json
import time
from datetime import datetime

import config

PAPER_MIN_TRADES, PAPER_MIN_DAYS, BACKTEST_MAX_AGE_DAYS = 30, 28, 7


def _backtest():
    f = config.TRADER_DIR / "backtest.json"
    if not f.exists():
        return False, "no backtest yet: run python backtest.py"
    d = json.loads(f.read_text(encoding="utf-8"))
    age = (time.time() - datetime.fromisoformat(d["run_at"]).timestamp()) / 86400
    if age > BACKTEST_MAX_AGE_DAYS:
        return False, f"backtest is {age:.0f} days old: run python backtest.py again"
    if not d.get("ranked"):
        return False, "backtest had no settings with enough trades"
    best = d["ranked"][0]
    ck = best["check"]
    if not (ck["trades"] >= 10 and (ck["avg_r_net"] or 0) > 0):
        return False, f"no edge on unseen days: {ck['avg_r_net']}R per trade after charges over {ck['trades']} trades"
    if (best["stop"], best["target_r"], best["trend_filter"]) != (config.STOP_RULE, config.TARGET_R, config.TREND_FILTER):
        return False, (f"the settings that worked (STOP_RULE={best['stop']} TARGET_R={best['target_r']} "
                       f"TREND_FILTER={str(best['trend_filter']).lower()}) are not the ones in .env")
    return True, f"{ck['avg_r_net']}R per trade after charges over {ck['trades']} unseen trades"


def _paper():
    f = config.TRADER_DIR / "paper_trades.jsonl"
    trades = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()] if f.exists() else []
    n = len(trades)
    if not n:
        return False, f"0 of {PAPER_MIN_TRADES} paper trades so far"
    days = (datetime.fromisoformat(trades[-1]["time"]) - datetime.fromisoformat(trades[0]["time"])).days
    pnl, avg_r = sum(t["pnl"] for t in trades), sum(t["r"] for t in trades) / n
    ok = n >= PAPER_MIN_TRADES and days >= PAPER_MIN_DAYS and pnl > 0 and avg_r > 0
    return ok, f"{n}/{PAPER_MIN_TRADES} trades over {days}/{PAPER_MIN_DAYS} days, PnL ${pnl:.2f}, avg {avg_r:+.2f}R"


def _test_order():
    f = config.TRADER_DIR / "test_order.json"
    if not f.exists():
        return False, "not done: python bot.py --test-order F (market hours, TWS/Gateway on the live account)"
    d = json.loads(f.read_text(encoding="utf-8"))
    if d.get("paper"):
        return False, "the test order ran on a PAPER account: repeat it on the live account"
    return bool(d.get("ok")), f"{d.get('symbol')} on {d.get('time', '?')[:10]}: {d.get('detail', '')}"


def _ib_live():
    try:
        from ib_client import IBClient
        ib = IBClient()
        acct, paper = ib.account, ib.paper
        funds = ib.available_funds()
        ib.ib.disconnect()
    except Exception as e:
        return False, f"cannot connect: {e}"
    if paper:
        return False, f"TWS/Gateway on port {config.IB_PORT} is logged into PAPER account {acct}"
    return True, f"live account {acct}, available funds ${funds:,.2f}"


def check():
    return [("Backtest shows an edge", *_backtest()), ("Paper trading is profitable", *_paper()),
            ("Live test order worked", *_test_order()), ("IBKR live account connected", *_ib_live())]


def report(results):
    lines = [f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}" for name, ok, detail in results]
    ready = all(ok for _, ok, _ in results)
    lines.append("  READY for real money." if ready else "  NOT READY for real money. Keep paper trading.")
    return ready, "\n".join(lines)


if __name__ == "__main__":
    print("Go-live readiness (Interactive Brokers, US stocks):")
    print(report(check())[1])
