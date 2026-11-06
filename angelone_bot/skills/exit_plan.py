"""Exit Planner (trader-exit).

Stop: or-mid (default), or-low, pct:X or atr:K (14-bar ATR on today's bars up to the entry bar), rounded
DOWN to the tick (5 paise). Target = entry + R x risk, rounded half up to the tick.
Refuses a stop at or above the entry, or one too tight to survive the spread.
"""
import math

import config


def to_tick(x, tick=0.05, up=False):
    n = x / tick
    n = math.floor(n + 0.5) if up else math.floor(n + 1e-9)
    return round(n * tick, 2)


def atr(bars, period=14):
    trs = []
    for i, b in enumerate(bars):
        if i == 0:
            trs.append(b["h"] - b["l"])
        else:
            pc = bars[i - 1]["c"]
            trs.append(max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc)))
    use = trs[-period:]
    return sum(use) / len(use) if use else 0.0


def stop_price(rule, e, rng, bars):
    if rule == "or-mid":
        return (rng["high"] + rng["low"]) / 2
    if rule == "or-low":
        return rng["low"]
    if rule.startswith("pct:"):
        return e * (1 - float(rule[4:]) / 100)
    if rule.startswith("atr:"):
        return e - float(rule[4:]) * atr(bars)
    raise ValueError(f"unknown stop rule {rule}")


def plan(sig, ask, spread, bars_today, tick=0.05):
    rule = config.STOP_RULE if sig["setup"] == "orb" else config.LEVEL_STOP_RULE
    e = round(max(sig["price"], ask), 2)
    stop = to_tick(stop_price(rule, e, sig["opening_range"], bars_today), tick)
    if stop >= e:
        return {"ok": False, "reason": f"stop {stop} is not below the entry {e}"}
    risk = e - stop
    min_risk = max(e * 0.0005, 2 * spread)
    if risk < min_risk:
        return {"ok": False, "reason": f"stop too tight: risk {risk:.2f} under {min_risk:.2f}"}
    target = to_tick(e + config.TARGET_R * risk, tick, up=True)
    return {"ok": True, "symbol": sig["symbol"], "setup": sig["setup"], "entry": e, "trigger_close": sig["price"],
            "stop": stop, "stop_rule": rule, "target": target, "target_r": config.TARGET_R,
            "risk_per_share": round(risk, 2), "risk_pct": round(risk / e * 100, 3),
            "reward_risk": round((target - e) / risk, 3), "flat_by": config.FLAT_BY,
            "opening_range": sig["opening_range"], "entry_bar_index": sig["bar_index"]}
