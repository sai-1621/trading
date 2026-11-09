"""Angel One NSE day-trading bot, driven by the day-trader skill pack, fully automated. Long only, intraday (MIS).

Pre-market (once a day):  scanner -> news
Every poll (market open): alerts -> sells open positions at the stop, the target or 15:10
Every 5-minute bar:       entry -> exit_plan -> timemachine -> budget -> risk -> broker, for each stock
Any time (optional):      TradingView alerts -> the same exit_plan -> timemachine -> budget -> risk -> broker

    python bot.py                       run continuously (paper trading unless LIVE_TRADING=true)
    python bot.py --once                one cycle and exit
    python bot.py --status              cash, PnL, positions, today
    python bot.py --scan                scanner + news only
    python bot.py --test-order SBIN     one REAL 1-share intraday buy then sell (market hours)
    python backtest.py / research.py    test the rules on past data;   python readiness.py   go-live check
"""
import argparse
import json
import logging
import os
import sys
import time
import traceback
from datetime import date
from logging.handlers import RotatingFileHandler

import config
import costs
import journal
import market
import notifier
import tv_webhook
import data as data_mod
from data import Data
from skills import write_json, scanner, news, entry, exit_plan, timemachine, budget, risk, broker, alerts

for stream in (sys.stdout, sys.stderr):  # Windows consoles cannot print some symbols (e.g. the rupee sign)
    try:
        stream.reconfigure(errors="replace")
    except AttributeError:
        pass
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[RotatingFileHandler(config.LOG_FILE, maxBytes=5_000_000, backupCount=5, encoding="utf-8"),
                              logging.StreamHandler(sys.stdout)])
for noisy in ("SmartApi", "logzero", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("bot")
EXIT_ALREADY_RUNNING, EXIT_NOT_READY = 3, 4
_lock = None


def acquire_lock():
    global _lock
    _lock = open(config.LOCK_FILE, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            _lock.seek(0)
            msvcrt.locking(_lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    config.PID_FILE.write_text(f"pid {os.getpid()} since {market.now().isoformat()}", encoding="utf-8")
    return True


def lock_holder():
    try:
        return config.PID_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


class State(dict):
    def __init__(self):
        super().__init__(positions={}, paper_balance=config.PAPER_START_BALANCE, realized_pnl=0.0,
                         day=market.now().date().isoformat(), entries_today=[], orders_today=0, history=[],
                         risk_log=[], watchlist=None, scan_day=None, news={}, last_bar={}, summary_day=None)
        if config.STATE_FILE.exists():
            self.update(json.loads(config.STATE_FILE.read_text(encoding="utf-8")))

    def save(self):
        tmp = config.STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, config.STATE_FILE)

    def day_summary(self, day):
        sells = [h for h in self["history"] if h["side"] == "SELL" and h["time"].startswith(day)]
        pnl = sum(h["pnl"] for h in sells)
        wins = sum(h["pnl"] > 0 for h in sells)
        return (f"{day}: {len(sells)} trades, {wins} wins, PnL Rs {pnl:.2f} after charges | total PnL "
                f"Rs {self['realized_pnl']:.2f} | paper cash Rs {self['paper_balance']:.2f}")

    def roll_day(self):
        today = market.now().date().isoformat()
        if self["day"] != today:
            self.update(day=today, entries_today=[], orders_today=0, last_bar={})


class Bot:
    def __init__(self, announce=True):
        self.state = State()
        self.angel = None
        have_keys = all([config.ANGEL_API_KEY, config.ANGEL_CLIENT_CODE, config.ANGEL_PIN, config.ANGEL_TOTP_SECRET])
        if config.LIVE_TRADING or have_keys:
            from angel_client import AngelClient
            try:
                self.angel = AngelClient()
            except Exception as e:
                if config.LIVE_TRADING:
                    raise
                log.warning("Angel One login failed (%s): paper trading on Yahoo data instead", e)
        self.data = Data(self.angel)
        self.bar_summary = {}
        self._closed_logged = None
        if announce:
            msg = (f"started | data {self.data.source} | budget Rs {config.BUDGET:.0f} | max loss/trade Rs "
                   f"{config.MAX_LOSS_PER_TRADE:.0f} | max daily loss Rs {config.MAX_DAILY_LOSS:.0f} | "
                   f"{'TradingView signals only' if config.TV_ONLY else 'built-in signals'}"
                   f"{' + TradingView webhook' if config.TV_WEBHOOK else ''}")
            log.info("Bot %s mode %s", "LIVE" if config.LIVE_TRADING else "PAPER", msg)
            notifier.notify(f"Bot {msg}")
            if market.holiday_list_missing(market.now().date()):
                log.warning("No NSE holiday list for %d: add dates to NSE_HOLIDAYS in .env", market.now().year)

    # ---- pre-market ----
    def premarket(self):
        s = self.state
        today = market.now().date().isoformat()
        if s["scan_day"] == today and s["watchlist"]:
            return
        s["watchlist"] = scanner.scan()["hits"]
        s["news"] = {k: v["event_risk"] for k, v in news.check(s["watchlist"]).items()} if config.NEWS_CHECK else {}
        s["scan_day"] = today
        s.save()

    # ---- the pipeline after a trigger (built-in or TradingView) ----
    @staticmethod
    def day_bars(sym, day):
        """5-minute bars covering an earlier day and that day's flat-by minute, to settle a trade still open from a
        day when the bot was not running at the close."""
        d = date.fromisoformat(day)
        rng = "5d" if (market.now().date() - d).days <= 4 else "60d"
        return data_mod.yahoo_bars(sym, "5m", rng), market.hhmm(config.FLAT_BY)

    def pipeline(self, sig, bars_today):
        sym, s = sig["symbol"], self.state
        write_json("entry.json", sig)
        bid, ask, last = self.data.quote(sym)
        rec = {"time": market.now().isoformat(), "bot": "angelone", "symbol": sym, "setup": sig["setup"],
               "trigger_close": sig["price"], "level": sig.get("level"), "bid": bid, "ask": ask}
        plan = exit_plan.plan(sig, ask, ask - bid, bars_today)
        if sig.get("tv_stop") or sig.get("tv_target"):
            plan = self._apply_tv_levels(plan, sig, ask)
        write_json("plan.json", plan)
        if not plan["ok"]:
            log.info("EXIT PLAN: %s refused: %s", sym, plan["reason"])
            return journal.signal({**rec, "decision": "SKIPPED", "stage": "exit plan", "reason": plan["reason"]})
        log.info("EXIT PLAN: %s entry %.2f stop %.2f (%s) target %.2f (%sR), risk %s%%", sym, plan["entry"],
                 plan["stop"], plan["stop_rule"], plan["target"], plan["target_r"], plan["risk_pct"])

        rec.update(entry=plan["entry"], stop=plan["stop"], target=plan["target"], risk_pct=plan["risk_pct"],
                   stop_rule=plan["stop_rule"])
        cash = broker.cash_available(self.angel, s)
        order = budget.size(plan, cash)

        def skip(stage, reason):
            journal.signal({**rec, "decision": "SKIPPED", "stage": stage, "reason": reason})
            qty = order["qty"] if order["qty"] > 0 else int(config.MAX_LOSS_PER_TRADE / (plan["entry"] - plan["stop"]))
            journal.open_shadow(s, sym, plan, qty, f"{stage}: {reason}", market.now().isoformat(),
                                market.now().date().isoformat())

        tm = timemachine.run(plan, market.now().date(), ask - bid)
        write_json("timemachine.json", {"plan": plan, **tm})
        if not tm["ok"]:
            log.info("TIME MACHINE: %s refused: %s", sym, tm["reason"])
            return skip("time machine", tm["reason"])
        h = tm["history"]
        log.info("TIME MACHINE: %s %s | target first %s%% stop first %s%% neither %s%% | avg R after costs %s | "
                 "history %s trades: %s target %s stop, avg R after costs %s | seed %s", sym, tm["verdict"],
                 tm["target_first_pct"], tm["stop_first_pct"], tm["neither_pct"], tm["avg_r_after_costs"],
                 h["trades"], h["target"], h["stop"], h["avg_r_net"], tm["seed"])
        for note in tm.get("notes", []):
            log.info("TIME MACHINE: %s %s", sym, note)
        rec["time_machine"] = {k: tm[k] for k in ("verdict", "target_first_pct", "stop_first_pct", "neither_pct", "avg_r_after_costs")}
        if not tm["take"]:
            log.info("TIME MACHINE: %s skipped, odds do not beat break-even after charges", sym)
            return skip("time machine", f"{tm['verdict']}: avg {tm['avg_r_after_costs']}R after costs")

        write_json("orders.json", order)
        if not order["ok"]:
            log.info("BUDGET: %s refused: %s", sym, order["reason"])
            return skip("budget", order["reason"])
        log.info("BUDGET: %s buy %d shares (Rs %.0f), max loss Rs %.0f incl. charges, decided by %s",
                 sym, order["qty"], order["value"], order["max_loss"], order["decided_by"])
        rec.update(qty=order["qty"], value=order["value"], max_loss=order["max_loss"])
        # trader-risk measures from the higher of the plan's entry and a fresh price: the Time Machine takes a while
        try:
            bid, ask, _ = self.data.quote(sym)
        except Exception as e:  # noqa: BLE001
            log.warning("RISK: no fresh price for %s (%s), checking from the plan's entry", sym, e)
        verdict = risk.check_entry(order, plan, s, cash, s["news"].get(sym), fresh_price=ask)
        write_json("risk.json", verdict)
        if verdict["verdict"] == "BLOCK":
            log.info("RISK: %s BLOCK: %s", sym, "; ".join(verdict["reasons"]))
            return skip("risk", "; ".join(verdict["reasons"]))
        log.info("RISK: %s PASS (checked from entry %.2f, max loss Rs %.0f, reward:risk %.2f)", sym,
                 verdict["checked_entry"], verdict["max_loss"], verdict["reward_risk"])
        order = {**order, "max_loss": max(order["max_loss"], verdict["max_loss"])}  # today's risk counts the worse one
        pos = broker.buy(self.angel, s, order, plan, ask)
        journal.signal({**rec, "decision": "BOUGHT" if pos else "SKIPPED", "stage": "broker",
                        "reason": "" if pos else "order not filled", "fill": pos["entry"] if pos else None})
        s.save()

    @staticmethod
    def _apply_tv_levels(plan, sig, ask):
        if not plan.get("ok") and not sig.get("tv_stop"):
            return plan
        e = max(sig["price"], ask)
        stop = sig.get("tv_stop") or plan["stop"]
        if stop >= e:
            return {"ok": False, "reason": f"TradingView stop {stop} is not below the entry {e}"}
        risk_ = e - stop
        target = sig.get("tv_target") or e + config.TARGET_R * risk_
        base = plan if plan.get("ok") else {"symbol": sig["symbol"], "setup": sig["setup"], "trigger_close": sig["price"],
                                            "flat_by": config.FLAT_BY, "opening_range": sig["opening_range"],
                                            "entry_bar_index": sig["bar_index"]}
        return {**base, "ok": True, "entry": e, "stop": round(stop, 2), "stop_rule": "tradingview" if sig.get("tv_stop") else plan["stop_rule"],
                "target": round(target, 2), "target_r": round((target - e) / risk_, 2), "risk_per_share": round(risk_, 2),
                "risk_pct": round(risk_ / e * 100, 3), "reward_risk": round((target - e) / risk_, 3)}

    def builtin_entries(self, t):
        s = self.state
        last_bar = market.last_closed_bar_start(t)
        if not last_bar:
            return
        key = last_bar.isoformat()
        self.bar_summary = {}
        for hit in s["watchlist"] or []:
            sym = hit["symbol"]
            if s["last_bar"].get(sym) == key or sym in s["positions"] or any(x["symbol"] == sym for x in s["entries_today"]):
                continue
            if config.TREND_FILTER and hit.get("above_ma20") is False:
                s["last_bar"][sym] = key
                self.bar_summary.setdefault("downtrend (skipped)", []).append(sym)
                continue
            try:
                bars = self.data.intraday(sym, 1)
                closed = entry.closed_today(bars, t)
                if not closed or closed[-1]["t"] != last_bar:
                    continue  # the data source has not published this bar yet
                s["last_bar"][sym] = key
                sig = entry.check(sym, bars, hit.get("level_20d"), t)
                self.bar_summary.setdefault(sig["status"], []).append(sym)
                if sig["status"] == "triggered":
                    log.info("ENTRY: %s %s triggered, close %.2f above %.2f", sym, sig["setup"], sig["price"], sig["level"])
                    self.pipeline(sig, closed)
            except Exception as e:
                log.warning("%s: %s", sym, e)
        if self.bar_summary:
            st = data_mod.STATS
            log.info("ENTRY bar %s | %s | open positions %d | paper cash Rs %.0f | data: Angel One %d, Yahoo %d, "
                     "fallbacks %d", last_bar.strftime("%H:%M"),
                     " | ".join(f"{k}: {len(v)}" for k, v in sorted(self.bar_summary.items())),
                     len(s["positions"]), s["paper_balance"], st["angel"], st["yahoo"], st["fallback"])

    def tradingview_signals(self, t):
        while not tv_webhook.signals.empty():
            tv = tv_webhook.signals.get()
            sym = tv["symbol"]
            try:
                if tv["action"] == "exit":
                    pos = self.state["positions"].get(sym)
                    if pos:
                        bid, _, _ = self.data.quote(sym)
                        broker.sell(self.angel, self.state, pos, bid, "TradingView exit alert")
                    continue
                if not market.is_open(t):
                    log.info("TradingView BUY %s ignored: market closed", sym)
                    continue
                bars = self.data.intraday(sym, 1)
                closed = entry.closed_today(bars, t)
                n_or = config.OR_MINUTES // market.BAR_MIN
                if len(closed) < n_or:
                    log.info("TradingView BUY %s ignored: opening range not complete", sym)
                    continue
                rng = {"high": max(b["h"] for b in closed[:n_or]), "low": min(b["l"] for b in closed[:n_or])}
                sig = {"symbol": sym, "setup": "tradingview", "price": tv.get("price") or closed[-1]["c"],
                       "opening_range": rng, "bar_index": market.bar_index(closed[-1]["t"]),
                       "tv_stop": tv.get("stop"), "tv_target": tv.get("target"), "status": "triggered"}
                log.info("ENTRY: %s TradingView alert at %.2f", sym, sig["price"])
                self.pipeline(sig, closed)
            except Exception as e:
                log.warning("TradingView %s %s: %s", tv["action"], sym, e)

    def step(self):
        s, t = self.state, market.now()
        s.roll_day()
        if market.is_trading_day(t.date()) and market.OPEN_MIN - 20 <= market.minutes(t) < market.CLOSE_MIN:
            self.premarket()
        if not market.is_open(t):
            if s["positions"] and market.is_trading_day(t.date()) and market.minutes(t) >= market.CLOSE_MIN:
                log.warning("Market closed with open positions %s: check Angel One (auto square-off)", list(s["positions"]))
            if market.minutes(t) >= market.CLOSE_MIN and s["summary_day"] != t.date().isoformat() and market.is_trading_day(t.date()):
                summary = s.day_summary(t.date().isoformat())
                log.info("DAILY SUMMARY: %s", summary)
                notifier.notify(summary)
                s["summary_day"] = t.date().isoformat()
            if self._closed_logged != t.date():
                log.info("Market closed. Next open %s", market.next_open(t).strftime("%a %d %b %H:%M"))
                self._closed_logged = t.date()
            self.tradingview_signals(t)  # exits still work; buys are ignored
            s.save()
            return
        alerts.check_positions(self.angel, self.data, s, self.day_bars)
        journal.check_shadows(s, lambda sym: self.data.quote(sym)[2],
                              lambda sh: sh["day"] != t.date().isoformat() or market.minutes(t) >= market.hhmm(config.FLAT_BY),
                              lambda sh, px: costs.round_trip(sh['qty'] * sh['entry'], sh['qty'] * px), t.isoformat(),
                              lambda sh: self.day_bars(sh["key"], sh["day"]))
        s.save()
        self.tradingview_signals(t)
        if not config.TV_ONLY:
            self.builtin_entries(t)
        s.save()

    def run(self):
        if config.TV_WEBHOOK:
            tv_webhook.start()
        errors = 0
        while True:
            try:
                self.step()
                errors = 0
            except Exception as e:
                errors += 1
                log.error("Error: %s", e)
                notifier.notify(f"Error: {e}", key="loop-error", every_seconds=1800)
                if errors >= 20:
                    raise
            time.sleep(config.POLL_SECONDS if market.is_open() else 60)

    def status(self):
        s = self.state
        print(f"Mode:          {'LIVE' if config.LIVE_TRADING else 'PAPER'} | data {self.data.source}")
        print(f"Market:        {'OPEN' if market.is_open() else 'closed, next open ' + market.next_open().strftime('%a %d %b %H:%M')}")
        print(f"Cash:          Rs {broker.cash_available(self.angel, s):,.2f}")
        print(f"Realized PnL:  Rs {s['realized_pnl']:,.2f} (after charges)")
        print(f"Today:         {s.day_summary(market.now().date().isoformat())}")
        print(f"Risked today:  Rs {sum(x['max_loss'] for x in s['entries_today']):.0f} of {config.MAX_DAILY_LOSS:.0f}, "
              f"{len(s['entries_today'])}/{config.MAX_TRADES_PER_DAY} trades")
        print(f"Watching:      {len(s['watchlist'] or [])} stocks (scanned {s['scan_day']})")
        flagged = [k for k, v in (s.get('news') or {}).items() if v]
        print(f"News risk:     {', '.join(flagged) or 'none'}")
        if config.PID_FILE.exists():
            print(f"Last started:  {lock_holder()}")
        for sym, p in s["positions"].items():
            print(f"Open: {sym} {p['qty']} @ {p['entry']:.2f} | stop {p['stop']} target {p['target']}")
        if not s["positions"]:
            print("Open positions: none")


def test_data(symbol):
    """Read-only check of Angel One market data (no orders): logs in, fetches bars and a price, compares with Yahoo."""
    from angel_client import AngelClient
    try:
        a = AngelClient()
    except Exception as e:  # noqa: BLE001
        print(f"FAILED: cannot log in to Angel One: {e}")
        return 1
    ok = True
    checks = [("5-minute bars, 1 day", lambda: a.candles(symbol, "FIVE_MINUTE", 1), lambda: data_mod.yahoo_bars(symbol, "5m", "1d")),
              ("daily bars, 3 months", lambda: a.candles(symbol, "ONE_DAY", 93), lambda: data_mod.yahoo_bars(symbol, "1d", "3mo")),
              ("Nifty 50 daily, 1 month", lambda: a.candles("^NSEI", "ONE_DAY", 31), lambda: data_mod.yahoo_bars("^NSEI", "1d", "1mo"))]
    for name, angel_fn, yahoo_fn in checks:
        try:
            ab = angel_fn()
        except Exception as e:  # noqa: BLE001
            print(f"  {name}: FAILED: {e}")
            ok = False
            continue
        if not ab:
            print(f"  {name}: FAILED: Angel One returned no bars")
            ok = False
            continue
        try:
            yb = yahoo_fn()
            ylast = yb[-1]["c"] if yb else None
        except Exception:  # noqa: BLE001
            ylast = None
        diff = f", Yahoo last close {ylast:.2f} ({(ab[-1]['c'] / ylast - 1) * 100:+.2f}%)" if ylast else ""
        print(f"  {name}: {len(ab)} bars, {ab[0]['t']:%Y-%m-%d %H:%M} to {ab[-1]['t']:%Y-%m-%d %H:%M}, "
              f"last close {ab[-1]['c']:.2f}{diff}")
    try:
        print(f"  live price (LTP): {a.ltp(symbol):.2f}")
    except Exception as e:  # noqa: BLE001
        print(f"  live price: FAILED: {e}")
        ok = False
    print("PASSED: the bot will use Angel One data" if ok else "FAILED: the bot would fall back to Yahoo for these")
    return 0 if ok else 1


def test_order(symbol):
    """One REAL 1-share intraday market buy, then sell it back, printing Angel One's replies."""
    from angel_client import AngelClient
    if not market.is_open():
        sys.exit("The market is closed. Run this between 9:15 and 15:00 IST on a trading day.")
    a = AngelClient()
    price = a.ltp(symbol)
    print(f"This places a REAL intraday market BUY of 1 {symbol} (about Rs {price:.2f}), then sells it straight back.\n"
          f"You pay brokerage and charges (a few rupees).")
    if input("Type YES to go ahead: ").strip() != "YES":
        sys.exit("Cancelled.")
    b = a.market_order(symbol, "buy", 1, tag="bot-test")
    print("BUY:", json.dumps({k: v for k, v in b.items() if k != "raw"}, indent=2))
    time.sleep(2)
    sres = a.market_order(symbol, "sell", 1, tag="bot-test") if b["filled"] else {"status": "skipped"}
    print("SELL:", json.dumps({k: v for k, v in sres.items() if k != "raw"}, indent=2))
    ok = b.get("status") == "complete" and sres.get("status") == "complete"
    detail = f"buy {b.get('status')} @ {b.get('avg_price')}, sell {sres.get('status')} @ {sres.get('avg_price')}"
    write_json("test_order.json", {"ok": ok, "symbol": symbol, "time": market.now().isoformat(), "detail": detail})
    print(("PASSED: " if ok else "FAILED: ") + detail)


def main():
    p = argparse.ArgumentParser(description="Angel One NSE day-trader bot")
    p.add_argument("--once", action="store_true")
    p.add_argument("--status", action="store_true")
    p.add_argument("--scan", action="store_true")
    p.add_argument("--test-order", metavar="SYMBOL")
    p.add_argument("--test-data", metavar="SYMBOL", help="read-only check of Angel One market data (no orders)")
    a = p.parse_args()
    if a.test_data:
        return test_data(a.test_data.upper())
    if a.test_order:
        return test_order(a.test_order.upper())
    if a.status:
        return Bot(announce=False).status()
    if a.scan:
        hits = scanner.scan()["hits"]
        return news.check(hits) if config.NEWS_CHECK else None

    if config.LIVE_TRADING:
        import readiness
        ready, text = readiness.report(readiness.check())
        if not ready:
            msg = "LIVE_TRADING=true, but the bot is not ready for real money:\n" + text
            print(msg)
            log.error(msg)
            notifier.notify(msg)
            sys.exit(EXIT_NOT_READY)
    if not acquire_lock():
        print(f"Another copy of the bot is already running ({lock_holder()}). Not starting a second one.")
        sys.exit(EXIT_ALREADY_RUNNING)
    bot = Bot()
    if a.once:
        return bot.step()
    try:
        bot.run()
    except KeyboardInterrupt:
        log.info("Stopped by user.")
        notifier.notify("Stopped by user.")
    except Exception as e:
        log.critical("CRASH: %s\n%s", e, traceback.format_exc())
        notifier.notify(f"CRASHED: {e}. The watchdog restarts it if it is running.")
        sys.exit(1)


if __name__ == "__main__":
    main()
