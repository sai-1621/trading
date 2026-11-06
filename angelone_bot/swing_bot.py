"""Paper momentum swing bot (delivery / CNC): a forward test of research/PREREG_swing_v2_momentum.md.

PAPER ONLY. This file has no order code; it never sends anything to Angel One except read-only data requests.

Once per trading day after 16:00 IST it fetches daily bars (Angel One, Yahoo as the fallback) for the 120
stocks frozen in swing_universe.json, then for each trading day since its last run:
  fills the orders queued the evening before at that day's open, values the account at the close,
  and queues tomorrow's buys and sells from the close (swing_core.step).

    .venv\\Scripts\\python swing_bot.py            run forever (started hidden by run_swing.ps1)
    .venv\\Scripts\\python swing_bot.py --once     one cycle now (only processes days whose close is final)
    .venv\\Scripts\\python swing_bot.py --status   print the paper account

Files: trader/swing_paper.json (the account), trader/swing_trades.jsonl (closed trades), swing.log.
"""
import argparse
import json
import logging
import sys
import time
from datetime import date, datetime
from logging.handlers import RotatingFileHandler

import config
import data
import market
import swing_core

STATE = config.TRADER_DIR / "swing_paper.json"
TRADES = config.TRADER_DIR / "swing_trades.jsonl"
LOCK = config.BASE_DIR / "swing.lock"
UNIVERSE = config.BASE_DIR / "swing_universe.json"
RUN_AFTER = market.hhmm("16:00")
HISTORY = "1y"          # >= 200 trading days, needed by swing_core.MIN_HISTORY

log = logging.getLogger("swing")
_lock = None


def setup_logging():
    h = RotatingFileHandler(config.BASE_DIR / "swing.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    for lg in (log, logging.getLogger("bot")):      # "bot": angel_client and data log there
        lg.setLevel(logging.INFO)
        lg.addHandler(h)
    s = logging.StreamHandler()
    s.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(s)


def acquire_lock():
    global _lock
    _lock = open(LOCK, "a+")
    try:
        import msvcrt
        _lock.seek(0)
        msvcrt.locking(_lock.fileno(), msvcrt.LK_NBLCK, 1)
    except ImportError:
        import fcntl
        fcntl.flock(_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return None


def save_state(st):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def daily_bars(symbol):
    bars = data.bars(symbol, "1d", HISTORY)
    out = {}
    for b in bars:
        d = b["t"].date() if isinstance(b["t"], datetime) else b["t"]
        out[d] = {"t": d, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]}
    return [out[d] for d in sorted(out)]


def connect():
    """Angel One for data when the keys work; otherwise Yahoo only."""
    try:
        from angel_client import AngelClient
        c = AngelClient()
        data.set_broker(c)
        return "Angel One"
    except Exception as e:  # noqa: BLE001
        data.set_broker(None)
        log.warning("Angel One login failed (%s): using Yahoo data", e)
        return "Yahoo"


def cycle():
    """Fetch bars and process every finished trading day since the last run. Returns the events."""
    t = market.now()
    today = t.date()
    last_final = today if (market.is_trading_day(today) and market.minutes(t) >= RUN_AFTER) else None
    src = connect()
    syms = json.loads(UNIVERSE.read_text())
    nifty = daily_bars("^NSEI")
    bars, failed = {}, []
    for s in syms:
        try:
            bars[s] = daily_bars(s)
        except Exception as e:  # noqa: BLE001
            failed.append(s)
            log.warning("no bars for %s: %s", s, e)
    st = load_state()
    days = [b["t"] for b in nifty if b["t"] < today or b["t"] == last_final]
    if not days:
        log.info("no finished trading day yet")
        return []
    if st is None:
        st = swing_core.new_state(days[-1])
        st["mode"] = "paper"
        st["rule"] = "momentum: buy next open after a close above the previous 20 closes; sell next open after a close below the previous 20 closes"
        st["nifty_start"] = nifty[[b["t"] for b in nifty].index(days[-1])]["c"]
        todo = [days[-1]]
        log.info("new paper account Rs %.0f starting %s", swing_core.CAPITAL, days[-1])
    else:
        todo = [d for d in days if d > date.fromisoformat(st["last_day"])]
    nclose = {b["t"]: b["c"] for b in nifty}
    events = []
    n_closed = len(st["closed"])
    for d in todo:
        cut = {s: [b for b in bs if b["t"] <= d] for s, bs in bars.items()}
        ev = swing_core.step(st, d, cut, nclose.get(d))
        for e in ev:
            log.info("%s %s", d, e)
        events += [f"{d} {e}" for e in ev]
    with TRADES.open("a", encoding="utf-8") as f:
        for tr in st["closed"][n_closed:]:
            f.write(json.dumps(tr) + "\n")
    st["data_source"] = src
    st["updated"] = market.now().isoformat(timespec="seconds")
    st["failed_symbols"] = failed
    save_state(st)
    log.info("processed %d day(s) with %s data; %s", len(todo), src, summary(st))
    return events


def summary(st):
    eq = st["equity"][-1][1] if st["equity"] else st["cash"]
    nifty_now = next((x[2] for x in reversed(st["equity"]) if x[2]), None)
    bench = (f", Nifty {(nifty_now / st['nifty_start'] - 1) * 100:+.1f}%" if nifty_now and st.get("nifty_start") else "")
    wins = sum(t["pnl"] > 0 for t in st["closed"])
    return (f"value Rs {eq:,.0f} ({(eq / swing_core.CAPITAL - 1) * 100:+.1f}%{bench}), cash Rs {st['cash']:,.0f}, "
            f"{len(st['positions'])} open, {len(st['closed'])} closed ({wins} won), "
            f"queued: {', '.join(o['side'] + ' ' + o['symbol'] for o in st['pending']) or 'nothing'}")


def status():
    st = load_state()
    if not st:
        print("No paper account yet: run swing_bot.py --once after 16:00 IST on a trading day.")
        return
    print(f"Paper momentum swing bot since {st['start']} (last day {st['last_day']}, data {st.get('data_source')})")
    print(summary(st))
    for s, p in st["positions"].items():
        print(f"  {s:<12} {p['shares']:>5} @ {p['entry']:.2f} since {p['entry_day']}, last {p.get('last', 0):.2f}"
              f" ({(p.get('last', p['entry']) / p['entry'] - 1) * 100:+.1f}%)")
    for o in st["pending"]:
        print(f"  queued {o['side']} {o['symbol']}: {o['why']}")


def main():
    ap = argparse.ArgumentParser(description="Paper momentum swing bot (no real orders)")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.status:
        status()
        return 0
    setup_logging()
    try:
        acquire_lock()
    except OSError:
        log.error("another copy of the swing bot is running")
        return 3
    if a.once:
        cycle()
        return 0
    import os
    (config.BASE_DIR / "swing.pid").write_text(f"pid {os.getpid()} since {market.now():%Y-%m-%d %H:%M}", encoding="utf-8")
    log.info("swing bot started (paper only); runs each trading day after 16:00 IST")
    while True:
        try:
            t = market.now()
            st = load_state()
            done = st and st.get("last_day") == str(t.date())
            if market.is_trading_day(t.date()) and market.minutes(t) >= RUN_AFTER and not done:
                cycle()
        except Exception:  # noqa: BLE001 - keep the loop alive; the next check retries
            log.exception("swing cycle failed; retrying in 15 minutes")
            time.sleep(900)
        time.sleep(300)


if __name__ == "__main__":
    sys.exit(main())
