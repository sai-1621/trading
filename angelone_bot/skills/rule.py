"""The opening-range breakout rule on one past NSE session, shared by the Time Machine's history and backtest.py.

It mirrors the live bot: the first bar after the range that closes above the range high is the only possible
trigger; it must close inside the entry window and pass the volume check, or the session has no trade.
Costs are Angel One's real charges for a position sized the way the Budget Manager would size it.
"""
import config
import costs
import market
from skills.exit_plan import stop_price


def live_params(**over):
    p = {"or_minutes": config.OR_MINUTES, "stop_rule": config.STOP_RULE, "target_r": config.TARGET_R,
         "min_volume_x": config.MIN_VOLUME_X, "entry_from": market.hhmm(config.NO_ENTRIES_BEFORE),
         "entry_to": market.hhmm(config.NO_ENTRIES_AFTER), "flat_by": market.hhmm(config.FLAT_BY),
         "trend_filter": config.TREND_FILTER}
    p.update(over)
    return p


def position_value(entry, risk):
    """What the Budget Manager would buy: limited by the max loss, the max order and the position cap."""
    shares_by_loss = config.MAX_LOSS_PER_TRADE / risk
    cap = min(config.MAX_ORDER, config.BUDGET * config.MAX_POSITION_PCT / 100) * config.LEVERAGE
    return max(entry, min(shares_by_loss * entry, cap))


def trend_map(daily):
    """{date: True/False}: was the close of the session before above its 20-day average?"""
    out = {}
    for i in range(20, len(daily)):
        closes = [b["c"] for b in daily[i - 20:i]]
        out[daily[i]["t"].date()] = closes[-1] > sum(closes) / 20
    return out


def simulate_day(bars, p, spread_frac=None, above_ma20=None):
    """bars: one full session of 5m bars. Returns None (no trade) or {"outcome", "r", "r_net"}."""
    if p["trend_filter"] and above_ma20 is False:
        return None
    spread_frac = config.ASSUMED_SPREAD_PCT / 100 if spread_frac is None else spread_frac
    n_or = p["or_minutes"] // market.BAR_MIN
    hi, lo = max(b["h"] for b in bars[:n_or]), min(b["l"] for b in bars[:n_or])
    avg_vol = sum(b["v"] for b in bars[:n_or]) / n_or
    trig = next((i for i in range(n_or, len(bars)) if bars[i]["c"] > hi), None)
    if trig is None:
        return None
    close_min = market.OPEN_MIN + (trig + 1) * market.BAR_MIN
    if not (p["entry_from"] <= close_min <= p["entry_to"]):
        return None
    if p["min_volume_x"] > 0 and bars[trig]["v"] < p["min_volume_x"] * avg_vol:
        return None

    e = bars[trig]["c"] * (1 + spread_frac / 2)
    stop = stop_price(p["stop_rule"], e, {"high": hi, "low": lo}, bars[: trig + 1])
    risk = e - stop
    if risk <= 0 or risk < max(e * 0.0005, 2 * spread_frac * e):
        return None
    target = e + p["target_r"] * risk
    end = (p["flat_by"] - market.OPEN_MIN) // market.BAR_MIN
    outcome, r, exit_bar = "neither", None, min(end, len(bars)) - 1
    for k in range(trig + 1, end):
        b = bars[k]
        if b["l"] <= stop:
            outcome, r, exit_bar = "stop", -1.0, k
            break
        if b["h"] >= target:
            outcome, r, exit_bar = "target", p["target_r"], k
            break
    if r is None:
        r = (bars[min(end, len(bars)) - 1]["c"] - e) / risk
    value = position_value(e, risk)
    shares = value / e
    cost_rupees = costs.round_trip(value, value * (1 + r * risk / e)) + shares * e * spread_frac / 2
    cost_r = cost_rupees / (shares * risk)
    return {"outcome": outcome, "r": r, "r_net": r - cost_r, "entry": e, "stop": stop, "target": target,
            "entry_bar": trig, "exit_bar": exit_bar, "shares": shares, "risk": risk}


def summarize(results):
    t = [x for x in results if x]
    n = len(t)
    if not n:
        return {"trades": 0, "target": 0, "stop": 0, "neither": 0, "win_pct": None, "avg_r": None, "avg_r_net": None}
    return {"trades": n, "target": sum(x["outcome"] == "target" for x in t), "stop": sum(x["outcome"] == "stop" for x in t),
            "neither": sum(x["outcome"] == "neither" for x in t),
            "win_pct": round(sum(x["r_net"] > 0 for x in t) / n * 100, 1),
            "avg_r": round(sum(x["r"] for x in t) / n, 3), "avg_r_net": round(sum(x["r_net"] for x in t) / n, 3)}
