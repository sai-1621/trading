"""Entry Timer (trader-entry), for NSE.

Opening range (orb): high and low of the first OR_MINUTES (9:15-9:30 by default) on 5-minute bars.
Level: the 20-day closing high from the scanner, when it sits above the opening range.
The trigger is the FIRST completed bar after the range that CLOSES above the level; only the newest closed
bar can trigger (an older one is a late entry, a different trade). Long side only. It never places an order.
"""
from datetime import timedelta

import config
import market
from skills import today_bars


def closed_today(bars, t):
    return [b for b in today_bars(bars, t.date()) if b["t"] + timedelta(minutes=market.BAR_MIN) <= t]


def check(symbol, bars, level_20d, t):
    out = {"symbol": symbol, "status": "waiting", "checked_at": t.isoformat()}
    closed = closed_today(bars, t)
    n_or = config.OR_MINUTES // market.BAR_MIN
    if len(closed) < n_or or market.minutes(closed[0]["t"]) != market.OPEN_MIN:
        out["status"] = "range forming" if market.minutes(t) < market.OPEN_MIN + config.OR_MINUTES + 5 else "no data"
        return out

    rng = closed[:n_or]
    or_high, or_low = max(b["h"] for b in rng), min(b["l"] for b in rng)
    or_vol = sum(b["v"] for b in rng) / n_or
    out["opening_range"] = {"high": or_high, "low": or_low, "avg_volume": or_vol}
    after = closed[n_or:]
    if not after:
        return out
    last = after[-1]
    out["last_close"] = last["c"]
    m = market.minutes(t)
    if not (market.hhmm(config.NO_ENTRIES_BEFORE) <= m <= market.hhmm(config.NO_ENTRIES_AFTER)):
        out["status"] = "outside entry window"
        return out

    setups = [("orb", or_high)]
    if config.USE_LEVEL_SETUP and level_20d and level_20d > or_high:
        setups.append(("level", level_20d))
    for setup, level in setups:
        above = [b for b in after if b["c"] > level]
        if not above:
            continue
        if above[0]["t"] != last["t"]:
            out.update(status="expired", setup=setup, level=level)
            continue
        if config.MIN_VOLUME_X > 0 and last["v"] < config.MIN_VOLUME_X * or_vol:
            out.update(status="waiting", setup=setup, level=level, note="closed above, volume too low")
            continue
        out.update(status="triggered", setup=setup, level=level, price=last["c"],
                   bar_time=last["t"].isoformat(), bar_index=market.bar_index(last["t"]))
        return out
    if out["status"] == "waiting":
        out["distance_pct"] = round((last["c"] / or_high - 1) * 100, 3)
    return out
