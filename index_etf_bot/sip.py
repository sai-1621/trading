"""Index ETF SIP bot: buys index ETFs every month on NSE through Angel One (paper by default) and holds them.

    python sip.py              run (paper unless LIVE_TRADING=true; one copy at a time)
    python sip.py --status     holdings, invested, value, XIRR
    python sip.py --buy-now    do this month's SIP now, if it is not done yet (market hours)
    python backtest.py         the 2016-to-today backtest

Each month, on the first trading day on or after SIP_DAY, after SIP_AFTER (9:45), it buys whole units of the ETFs in
ETFS, new money going to whatever is furthest below its target weight (portfolio.py). It never sells.
After each close it values the holdings, so the dashboard can show the growth.
"""
import argparse
import json
import logging
import os
import sys
import time
import traceback
from datetime import date, timedelta
from logging.handlers import RotatingFileHandler

import config
import data
import market
import notifier
from portfolio import plan_buys, buy_charges, xirr

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(errors="replace")
    except AttributeError:
        pass
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[RotatingFileHandler(config.LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8"),
                              logging.StreamHandler(sys.stdout)])
for noisy in ("SmartApi", "logzero", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("bot")
_lock = None


def acquire_lock():
    global _lock
    _lock = open(config.LOCK_FILE, "a+")
    try:
        import msvcrt
        _lock.seek(0)
        msvcrt.locking(_lock.fileno(), msvcrt.LK_NBLCK, 1)
    except (OSError, ImportError):
        return False
    config.PID_FILE.write_text(f"pid {os.getpid()} since {market.now().isoformat()}", encoding="utf-8")
    return True


def load_state():
    s = {"holdings": {}, "cash": 0.0, "invested": 0.0, "charges": 0.0, "purchases": [], "months": {}, "values": [],
         "mode": "LIVE" if config.LIVE_TRADING else "PAPER"}
    if config.STATE_FILE.exists():
        s.update(json.loads(config.STATE_FILE.read_text(encoding="utf-8")))
    return s


def save_state(s):
    tmp = config.STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, config.STATE_FILE)


def sip_day(d):
    """The first trading day on or after SIP_DAY in d's month."""
    x = date(d.year, d.month, min(config.SIP_DAY, 28))
    while not market.is_trading_day(x):
        x += timedelta(days=1)
    return x


def nifty_above_200dma():
    rows = data.daily("^NSEI")
    closes = [c for _, _, c in rows]
    return closes[-1] > sum(closes[-200:]) / 200


class Bot:
    def __init__(self):
        self.state = load_state()
        self.angel = None
        if config.LIVE_TRADING or all([config.ANGEL_API_KEY, config.ANGEL_CLIENT_CODE, config.ANGEL_PIN, config.ANGEL_TOTP_SECRET]):
            from angel_client import AngelClient
            try:
                self.angel = AngelClient()
            except Exception as e:
                if config.LIVE_TRADING:
                    raise
                log.warning("Angel One login failed (%s): paper mode uses Yahoo prices", e)

    def price(self, etf):
        return self.angel.ltp(etf) if self.angel else data.last_price(etf)

    def do_sip(self, force=False):
        s, t = self.state, market.now()
        key = t.strftime("%Y-%m")
        if s["months"].get(key):
            return log.info("SIP: %s already done", key)
        if not market.is_open(t):
            return log.info("SIP: market closed")
        s["cash"] += config.SIP_AMOUNT
        s["invested"] += config.SIP_AMOUNT
        if config.PAUSE_BELOW_200DMA and not nifty_above_200dma():
            s["months"][key] = {"status": "paused", "cash": round(s["cash"], 2)}
            log.info("SIP %s: NIFTY below its 200-day average, holding Rs %.0f as cash", key, s["cash"])
            notifier.notify(f"SIP {key} paused: NIFTY below its 200-day average. Cash waiting: Rs {s['cash']:.0f}")
            return save_state(s)
        if config.LIVE_TRADING:
            avail = self.angel.available_cash()
            if avail < s["cash"]:
                log.error("SIP %s: only Rs %.0f available, need Rs %.0f. Add money to Angel One.", key, avail, s["cash"])
                notifier.notify(f"SIP {key} NOT done: only Rs {avail:.0f} in Angel One, need Rs {s['cash']:.0f}")
                s["cash"] -= config.SIP_AMOUNT
                s["invested"] -= config.SIP_AMOUNT
                return save_state(s)
        prices = {e: self.price(e) * (1 + config.ASSUMED_SPREAD_PCT / 200) for e in config.ETFS}
        buys = plan_buys(s["cash"], s["holdings"], prices, config.ETFS)
        bought = []
        for e, units in buys.items():
            if config.LIVE_TRADING:
                res = self.angel.market_order(e, "buy", units, tag="sip")
                if res["status"] != "complete":
                    log.error("SIP: buy %s %s not filled: %s %s", units, e, res["status"], res["text"])
                    notifier.notify(f"SIP buy {units} {e} NOT filled: {res['status']}")
                    continue
                px, units = res["avg_price"], res["filled"]
            else:
                px = prices[e]
            cost = units * px
            ch = buy_charges(cost)
            s["holdings"][e] = s["holdings"].get(e, 0) + units
            s["cash"] -= cost + ch
            s["charges"] += ch
            rec = {"time": t.isoformat(), "etf": e, "units": units, "price": round(px, 2), "value": round(cost, 2), "charges": round(ch, 2)}
            s["purchases"].append(rec)
            bought.append(f"{units} {e} @ {px:.2f}")
        s["months"][key] = {"status": "done", "bought": bought, "cash_left": round(s["cash"], 2)}
        msg = f"SIP {key}: {', '.join(bought) or 'nothing (price above the amount)'} | cash left Rs {s['cash']:.0f}"
        log.info("%s [%s]", msg, "LIVE" if config.LIVE_TRADING else "PAPER")
        notifier.notify(msg)
        save_state(s)

    def mark_to_market(self):
        s = self.state
        today = market.now().date().isoformat()
        if s["values"] and s["values"][-1][0] == today:
            return
        value = s["cash"] + sum(u * self.price(e) for e, u in s["holdings"].items())   # after the close: the closing price
        s["values"].append([today, round(s["invested"], 2), round(value, 2)])
        log.info("VALUE %s: invested Rs %.0f, worth Rs %.0f", today, s["invested"], value)
        save_state(s)

    def step(self):
        t = market.now()
        if market.is_trading_day(t.date()):
            m = market.minutes(t)
            if t.date() >= sip_day(t.date()) and m >= market.hhmm(config.SIP_AFTER) and market.is_open(t) \
                    and not self.state["months"].get(t.strftime("%Y-%m")):
                self.do_sip()
            if m >= market.CLOSE_MIN + 5 and self.state["holdings"]:
                self.mark_to_market()

    def status(self):
        s = self.state
        value = s["cash"] + sum(u * self.price(e) for e, u in s["holdings"].items())
        flows = [(date.fromisoformat(k + "-01"), -config.SIP_AMOUNT) for k, v in sorted(s["months"].items())]
        x = xirr(flows + [(market.now().date(), value)]) if flows else None
        print(f"Mode:        {'LIVE' if config.LIVE_TRADING else 'PAPER'} | SIP Rs {config.SIP_AMOUNT:,.0f} a month on day {config.SIP_DAY}"
              f" | {', '.join(f'{e} {w:.0%}' for e, w in config.ETFS.items())}")
        print(f"Invested:    Rs {s['invested']:,.0f} | worth Rs {value:,.0f} | gain Rs {value - s['invested']:,.0f}"
              f" | XIRR {f'{x*100:.1f}%' if x is not None else '–'} | charges Rs {s['charges']:,.0f} | cash Rs {s['cash']:,.0f}")
        for e, u in s["holdings"].items():
            print(f"  {e}: {u} units")
        nxt = max(sip_day(market.now().date()), market.now().date())
        if s["months"].get(market.now().strftime("%Y-%m")):
            n = market.now().date().replace(day=1) + timedelta(days=32)
            nxt = sip_day(n.replace(day=1))
        print(f"Next SIP:    {nxt}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--buy-now", action="store_true")
    a = ap.parse_args()
    if a.status:
        return Bot().status()
    if config.LIVE_TRADING:
        import readiness
        ok, text = readiness.report(readiness.check())
        if not ok:
            print("LIVE_TRADING=true, but not ready:\n" + text)
            log.error("Not ready for real money:\n%s", text)
            sys.exit(4)
    if not acquire_lock():
        print("Another copy is already running. Not starting a second one.")
        sys.exit(3)
    bot = Bot()
    if a.buy_now:
        return bot.do_sip()
    log.info("SIP bot started [%s] | Rs %.0f a month | %s", "LIVE" if config.LIVE_TRADING else "PAPER", config.SIP_AMOUNT,
             ", ".join(f"{e} {w:.0%}" for e, w in config.ETFS.items()))
    try:
        while True:
            try:
                bot.step()
            except Exception as e:
                log.error("Error: %s", e)
                notifier.notify(f"Error: {e}", key="err", every_seconds=3600)
            time.sleep(config.POLL_SECONDS)
    except KeyboardInterrupt:
        log.info("Stopped by user.")
    except Exception as e:
        log.critical("CRASH: %s\n%s", e, traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
