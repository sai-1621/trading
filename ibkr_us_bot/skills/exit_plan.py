"""Exit Planner (trader-exit), as in the original pack.

Stop: or-mid (default), or-low, pct:X or atr:K (14-bar ATR on today's bars up to the entry bar). The stop rounds
AWAY from the entry to the cent (down, for a long); target = entry + R x risk, rounded half up to the cent.
Refuses a stop at or above the entry, or a risk under 0.05% of the price / 2 spreads (too tight to survive).
"""
import math

import config


def to_cent(x, up=False):
    return round((math.floor(x * 100 + 0.5) if up else math.floor(x * 100 + 1e-9)) / 100, 2)


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


def plan(sig, ask, spread, bars_today):
    rule = config.STOP_RULE if sig["setup"] == "orb" else config.LEVEL_STOP_RULE
    e = round(max(sig["price"], ask), 2)
    stop = to_cent(stop_price(rule, e, sig["opening_range"], bars_today))
    if stop >= e:
        return {"ok": False, "reason": f"stop {stop} is not below the entry {e}"}
    risk = e - stop
    min_risk = max(e * 0.0005, 2 * spread)
    if risk < min_risk:
        return {"ok": False, "reason": f"stop too tight: risk ${risk:.2f} under ${min_risk:.2f}"}
    target = to_cent(e + config.TARGET_R * risk, up=True)
    return {"ok": True, "symbol": sig["symbol"], "setup": sig["setup"], "entry": e, "trigger_close": sig["price"],
            "stop": stop, "stop_rule": rule, "target": target, "target_r": config.TARGET_R,
            "risk_per_share": round(risk, 2), "risk_pct": round(risk / e * 100, 3),
            "reward_risk": round((target - e) / risk, 3), "flat_by": config.FLAT_BY,
            "opening_range": sig["opening_range"], "entry_bar_index": sig["bar_index"]}
