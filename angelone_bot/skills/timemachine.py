"""Time Machine (trader-timemachine), for NSE.

1. The pool: only FULL sessions (every five-minute bar from 9:15 to FLAT_BY) before today, from the stock's last
   60 days on Yahoo. Each bar becomes four ratios (open, high, low, close) to the bar before it.
2. One replay: start at the entry. For each bar left until FLAT_BY, pick a past bar from the same time of
   day (+/- 2 bars) at random and scale it to the current price. Stop first, then target; a bar touching
   both counts as the stop.
3. 10,000 replays (seed 19): target first, stop first, neither (still open at FLAT_BY).
4. The history: the same rule (skills/rule.py) on every past session, after Angel One's charges.
5. Verdict vs break-even 1/(1+R), adjusted for charges and spread, with trader-timemachine's notes under it
   (over 40% of replays hit neither; the average R goes against the lean). The bot only trades when the odds
   clear break-even after costs and the average R after costs is positive.
"""
import random
from collections import defaultdict
from datetime import timedelta

import config
import costs
import data
import market
from skills import rule

WINDOW = 2
MANY_NEITHER = 0.40


def needed_bars():
    """Bars from 9:15 up to the flat-by time; later bars never matter (Yahoo often drops 15:15-15:25 ones)."""
    return (market.hhmm(config.FLAT_BY) - market.OPEN_MIN) // market.BAR_MIN


def full_sessions(bars, before_day):
    """{date: bars} for sessions with every 5-minute bar from 9:15 to the flat-by time, cut there."""
    need = needed_bars()
    days = defaultdict(list)
    for b in bars:
        if b["t"].date() < before_day:
            days[b["t"].date()].append(b)
    out = {}
    for d, bs in sorted(days.items()):
        bs.sort(key=lambda b: b["t"])
        start = market.session_start(d)
        if len(bs) >= need and all(bs[i]["t"] == start + timedelta(minutes=5 * i) for i in range(need)):
            out[d] = bs[:need]
    return out


def run(plan, today, spread):
    bars = data.bars(plan["symbol"], "5m", "60d")
    full = full_sessions(bars, today)
    if len(full) < config.TM_MIN_DAYS:
        return {"ok": False, "reason": f"only {len(full)} full sessions of 5m bars (need {config.TM_MIN_DAYS})"}
    out = replay(full, plan, spread)
    if not out["ok"]:
        return out
    trends = rule.trend_map(data.bars(plan["symbol"], "1d", "6mo"))
    out["history"] = rule.summarize([rule.simulate_day(bs, rule.live_params(), spread / plan["entry"], trends.get(d))
                                     for d, bs in full.items()])
    return out


def replay(full, plan, spread, replays=None):
    """The replays and the verdict from past full sessions only (backtest_pipeline.py calls this directly)."""
    pool = defaultdict(list)
    for bs in full.values():
        for i in range(1, len(bs)):
            pc = bs[i - 1]["c"]
            b = bs[i]
            pool[i].append((b["o"] / pc, b["h"] / pc, b["l"] / pc, b["c"] / pc))

    e, stop, target, R = plan["entry"], plan["stop"], plan["target"], plan["target_r"]
    risk = e - stop
    start = plan["entry_bar_index"] + 1
    end = (market.hhmm(config.FLAT_BY) - market.OPEN_MIN) // market.BAR_MIN
    if start >= end:
        return {"ok": False, "reason": "entry is at or after the flat-by time"}
    slots = [[r for k in range(max(1, s - WINDOW), min(needed_bars(), s + WINDOW + 1)) for r in pool[k]]
             for s in range(start, end)]

    n = replays or config.TM_REPLAYS
    rng = random.Random(config.TM_SEED)
    tf = sf = nf = green = 0
    r_total = 0.0
    for _ in range(n):
        p, res = e, None
        for cands in slots:
            o, h, l, c = rng.choice(cands)
            if p * l <= stop:
                res, sf = -1.0, sf + 1
                break
            if p * h >= target:
                res, tf, green = R, tf + 1, green + 1
                break
            p *= c
        if res is None:
            nf += 1
            res = (p - e) / risk
            green += p > e
        r_total += res

    value = rule.position_value(e, risk)
    cost_r = (costs.round_trip(value, value) + value * (spread / e)) / (value / e * risk)
    avg_r_net = r_total / n - cost_r
    decided = tf + sf
    share = tf / decided if decided else 0.0
    breakeven = (1 + cost_r) / (1 + R)
    edge = (share - breakeven) * 100
    if not decided:
        verdict = "No replay hit a side"
    elif abs(edge) < 5:
        verdict = "Close to a coin flip" if R == 1 else "Close to break-even"
    elif abs(edge) < 15:
        verdict = "Leans toward the target" if edge > 0 else "Leans toward the stop"
    else:
        verdict = "Strongly toward the target" if edge > 0 else "Strongly toward the stop"

    # trader-timemachine's lines under the verdict
    notes = []
    if nf / n > MANY_NEITHER:
        notes.append(f"{nf / n * 100:.1f}% of the replays hit neither by {config.FLAT_BY}; "
                     f"the price at {config.FLAT_BY} decides those")
    if decided and abs(edge) >= 5 and (edge > 0) != (avg_r_net > 0):
        notes.append(f"average R after costs is {avg_r_net:+.3f}, against the lean")

    return {"ok": True, "take": decided > 0 and edge >= config.TM_MIN_EDGE_PTS and avg_r_net > 0,
            "verdict": verdict, "notes": notes, "seed": config.TM_SEED, "replays": n, "sessions": len(full),
            "target_first_pct": round(tf / n * 100, 1), "stop_first_pct": round(sf / n * 100, 1),
            "neither_pct": round(nf / n * 100, 1), "green_pct": round(green / n * 100, 1),
            "avg_r": round(r_total / n, 3), "cost_r": round(cost_r, 3), "avg_r_after_costs": round(avg_r_net, 3),
            "breakeven_after_costs_pct": round(breakeven * 100, 1), "edge_pts": round(edge, 1)}
