"""Paper crypto trend bot (Binance USDT perpetuals): forward test of futures_lab/PREREG_v2.md.

PAPER ONLY: public market data, no API keys, no order code.
Once a day after 01:05 UTC (06:35 IST) it reads Binance futures daily candles for 19 coins, works out the target
weights from yesterday's close (trend_core), fills them at the close of the 00:00-01:00 UTC hour, charges 0.10%
per side and the real funding rates, and catches up on any days it missed.

    ..\\freqtrade_bot\\.venv\\Scripts\\python trend_bot.py           run forever (run_trend.ps1)
    ..\\freqtrade_bot\\.venv\\Scripts\\python trend_bot.py --once    one cycle now
    ..\\freqtrade_bot\\.venv\\Scripts\\python trend_bot.py --status  print the paper account

Files: trend_paper.json (account), trend_bot.log, trend_bot.pid.
"""
import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pandas as pd
import requests

import trend_core as T

HERE = Path(__file__).resolve().parent
STATE = HERE / "trend_paper.json"
LOCK = HERE / "trend_bot.lock"
FAPI = "https://fapi.binance.com/fapi/v1"
RUN_AFTER = timedelta(hours=1, minutes=5)       # after 01:05 UTC
HISTORY_DAYS = 1000
log = logging.getLogger("trend")
_S = requests.Session()
_lock = None


def _get(path, params):
    for i in range(6):
        r = _S.get(f"{FAPI}/{path}", params=params, timeout=30)
        if r.status_code in (418, 429):
            time.sleep(30 * (i + 1))
            continue
        r.raise_for_status()
        time.sleep(0.2)
        return r.json()
    raise RuntimeError(f"Binance rate limit on {path}")


def daily_bars(today):
    """Closed daily candles (UTC) for every coin, as DataFrames C, H, L indexed by UTC day."""
    C, H, L = {}, {}, {}
    for c in T.COINS:
        k = _get("klines", {"symbol": f"{c}USDT", "interval": "1d", "limit": HISTORY_DAYS})
        df = pd.DataFrame([r[:5] for r in k], columns=["t", "o", "h", "l", "c"])
        df["t"] = pd.to_datetime(df["t"], unit="ms", utc=True)
        df = df[df["t"] < pd.Timestamp(today, tz="UTC")].set_index("t").astype(float)
        C[c], H[c], L[c] = df["c"], df["h"], df["l"]
    return pd.DataFrame(C), pd.DataFrame(H), pd.DataFrame(L)


def fill_prices(day):
    """Close of the 00:00-01:00 UTC candle of `day` for each coin."""
    t0 = int(pd.Timestamp(day, tz="UTC").timestamp() * 1000)
    out = {}
    for c in T.COINS:
        k = _get("klines", {"symbol": f"{c}USDT", "interval": "1h", "startTime": t0, "limit": 1})
        if k and k[0][0] == t0:
            out[c] = float(k[0][4])
    return out


def funding_between(coins, start, end):
    """Sum of funding rates with time in (start, end] per coin."""
    out = {}
    for c in coins:
        rows = _get("fundingRate", {"symbol": f"{c}USDT", "startTime": int(start.timestamp() * 1000) + 1,
                                    "endTime": int(end.timestamp() * 1000), "limit": 1000})
        out[c] = sum(float(r["fundingRate"]) for r in rows)
    return out


def load_state():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else None


def save_state(st):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def cycle(now=None):
    now = now or datetime.now(timezone.utc)
    today = now.date() if now - now.replace(hour=0, minute=0, second=0, microsecond=0) >= RUN_AFTER \
        else now.date() - timedelta(days=1)
    st = load_state()
    if st and st["last_exec_day"] and st["last_exec_day"] >= str(today):
        return []
    C, H, L = daily_bars(today)
    W = T.target_weights(C, H, L)
    if st is None:
        st = T.new_state(today)
        st["mode"] = "paper"
        st["rule"] = ("long only: buy a close above the 55-day high while BTC is above its 200-day average and the "
                      "coin's 55-day efficiency ratio >= 0.30; sell below the 20-day low or when BTC drops below its "
                      "200-day average. Vol target 40%/yr, max 3x gross, 0.75x per coin.")
        days = [today]
    else:
        last = datetime.fromisoformat(st["last_exec_day"]).date()
        days = [last + timedelta(days=i) for i in range(1, (today - last).days + 1)]
    events = []
    for e in days:
        d = pd.Timestamp(e - timedelta(days=1), tz="UTC")
        new_w = T.weights_on(W, d)
        prices = fill_prices(e)
        fund = {}
        if st["last_exec_day"]:
            p0 = pd.Timestamp(st["last_exec_day"], tz="UTC") + pd.Timedelta(hours=1)
            fund = funding_between([c for c, w in st["weights"].items() if w], p0,
                                   pd.Timestamp(e, tz="UTC") + pd.Timedelta(hours=1))
        if "btc_start" not in st:
            st["btc_start"] = prices.get("BTC")
        ev = T.step(st, e, prices, new_w, fund)
        for x in ev:
            log.info("%s %s", e, x)
        events += [f"{e} {x}" for x in ev]
    st["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    st["btc_above_200d"] = bool(C["BTC"].iloc[-1] > C["BTC"].iloc[-200:].mean())
    save_state(st)
    log.info("processed %d day(s); %s", len(days), summary(st))
    return events


def summary(st):
    eq = st["equity"]
    btc = (st["history"][-1][2] / st["btc_start"] - 1) * 100 if st.get("btc_start") and st["history"] else 0
    wins = sum(t["pnl_pct"] > 0 for t in st["closed"])
    return (f"equity {eq:,.2f} USDT ({(eq / T.CAPITAL - 1) * 100:+.1f}%, BTC {btc:+.1f}%), "
            f"{len(st['weights'])} open ({', '.join(f'{c} {w:.2f}x' for c, w in st['weights'].items()) or 'none'}), "
            f"{len(st['closed'])} closed ({wins} won)")


def status():
    st = load_state()
    if not st:
        print("No paper account yet.")
        return
    print(f"Paper crypto trend bot since {st['start']} (last fill {st['last_exec_day']} 01:00 UTC); "
          f"BTC above its 200-day average: {st.get('btc_above_200d')}")
    print(summary(st))


def setup_logging():
    h = RotatingFileHandler(HERE / "trend_bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.setLevel(logging.INFO)
    log.addHandler(h)
    s = logging.StreamHandler()
    s.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(s)


def main():
    ap = argparse.ArgumentParser(description="Paper crypto trend bot (no orders)")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.status:
        status()
        return 0
    setup_logging()
    global _lock
    _lock = open(LOCK, "a+")
    try:
        import msvcrt
        _lock.seek(0)
        msvcrt.locking(_lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        log.error("another copy of the trend bot is running")
        return 3
    if a.once:
        cycle()
        return 0
    (HERE / "trend_bot.pid").write_text(f"pid {os.getpid()} since {datetime.now():%Y-%m-%d %H:%M}", encoding="utf-8")
    log.info("trend bot started (paper only); fills daily at 01:00 UTC, checks after 01:05 UTC")
    while True:
        try:
            cycle()
        except Exception:  # noqa: BLE001 - keep running; retry later
            log.exception("trend cycle failed; retrying in 15 minutes")
            time.sleep(900)
        time.sleep(300)


if __name__ == "__main__":
    sys.exit(main())
