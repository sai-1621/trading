"""Chart Scanner (trader-scanner), for NSE.

The universe is an NSE index (NIFTY100 by default). It ranks the stocks by their average daily traded value
over 20 days, keeps the WATCH_SIZE most traded, and tags daily setups on closes:

  breakout_20d     last close above every close of the 20 sessions before it
  bull_flag        up >10% over the 10 sessions before the last 5, then the last 5 closes inside a 5% range
  reclaim_ma20     close before was under its 20-day average, last close is above it
  oversold         RSI 14 (Wilder) under 30
  oversold_bounce  RSI 14 under 30 the session before and the last session closed up

It also records above_ma20 (the trend filter) and level_20d (the 20-day closing high for the level setup).
"""
import logging
import time

import config
import data
from market import now
from skills import write_json

log = logging.getLogger("bot")


def rsi_wilder(closes, period=14):
    if len(closes) <= period:
        return None
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag, al = sum(gains[:period]) / period, sum(losses[:period]) / period
    out = []
    for g, l in zip(gains[period:], losses[period:]):
        ag, al = (ag * (period - 1) + g) / period, (al * (period - 1) + l) / period
        out.append(100.0 if al == 0 else 100 - 100 / (1 + ag / al))
    return out


def tag_setups(closes):
    tags = []
    if len(closes) >= 21 and closes[-1] > max(closes[-21:-1]):
        tags.append("breakout_20d")
    if len(closes) >= 16:
        run = closes[-6] / closes[-16] - 1
        last5 = closes[-5:]
        if run > 0.10 and (max(last5) - min(last5)) / min(last5) <= 0.05:
            tags.append("bull_flag")
    if len(closes) >= 21:
        if closes[-2] < sum(closes[-21:-1]) / 20 and closes[-1] > sum(closes[-20:]) / 20:
            tags.append("reclaim_ma20")
    r = rsi_wilder(closes)
    if r and len(r) >= 2:
        if r[-1] < 30:
            tags.append("oversold")
        if r[-2] < 30 and closes[-1] > closes[-2]:
            tags.append("oversold_bounce")
    return tags


def scan(write: bool = True) -> dict:
    names = data.universe()
    symbols = config.WATCH or list(names)
    today = now().date()
    rows = []
    for sym in symbols:
        if sym in config.BLOCKED:
            continue
        try:
            daily = [b for b in data.bars(sym, "1d", "3mo") if b["t"].date() < today]  # closed sessions only
        except Exception as e:
            log.warning("Scanner: %s skipped (%s)", sym, e)
            continue
        if len(daily) < 21:
            continue
        closes = [b["c"] for b in daily]
        if closes[-1] < config.MIN_PRICE:
            continue
        turnover = sum(b["c"] * b["v"] for b in daily[-20:]) / 20
        rows.append({"symbol": sym, "name": names.get(sym, sym), "last_close": closes[-1],
                     "turnover_cr": round(turnover / 1e7, 1), "tags": tag_setups(closes),
                     "level_20d": max(closes[-20:]), "above_ma20": closes[-1] > sum(closes[-20:]) / 20})
        time.sleep(0.1)

    rows.sort(key=lambda r: r["turnover_cr"], reverse=True)
    rows = rows[: config.WATCH_SIZE]
    result = {"scanned_at": now().isoformat(), "universe": config.UNIVERSE, "hits": rows}
    if write:
        write_json("watchlist.json", result)
        tagged = [f"{r['symbol']}({','.join(r['tags'])})" for r in rows if r["tags"]]
        log.info("SCANNER: watching %d of %d %s stocks, %d above their 20-day average. Setups: %s",
                 len(rows), len(symbols), config.UNIVERSE, sum(r["above_ma20"] for r in rows), ", ".join(tagged) or "none")
    return result
