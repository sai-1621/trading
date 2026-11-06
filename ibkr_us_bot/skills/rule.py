"""The opening-range breakout rule on one past US session, shared by the Time Machine's history and backtest.py.
It mirrors the live bot exactly, and charges IBKR's commission and fees on a position sized like the Budget Manager.
"""
import config
import costs
import market
from skills.exit_plan import stop_price


def live_params(**over):
    p = {"or_minutes": config.OR_MINUTES, "stop_rule": config.STOP_RULE, "target_r": config.TARGET_R,
         "min_volume_x": config.MIN_VOLUME_X, "entry_from": market.hhmm(config.NO_ENTRIES_BEFORE),
         "trend_filter": config.TREND_FILTER}
    p.update(over)
    return p


def shares_for(entry, risk):
    by_loss = config.MAX_LOSS_PER_TRADE / risk
    cap = min(config.MAX_ORDER, config.BUDGET * config.MAX_POSITION_PCT / 100) / entry
    return max(1, int(min(by_loss, cap)))


def trend_map(daily):
    out = {}
    for i in range(20, len(daily)):
        closes = [b["c"] for b in daily[i - 20:i]]
        out[daily[i]["t"].date()] = closes[-1] > sum(closes) / 20
    return out


def simulate_day(bars, p, spread_frac=None, above_ma20=None):
    if p["trend_filter"] and above_ma20 is False:
        return None
    day = bars[0]["t"].date()
    spread_frac = config.ASSUMED_SPREAD_PCT / 100 if spread_frac is None else spread_frac
    n_or = p["or_minutes"] // market.BAR_MIN
    hi, lo = max(b["h"] for b in bars[:n_or]), min(b["l"] for b in bars[:n_or])
    avg_vol = sum(b["v"] for b in bars[:n_or]) / n_or
    trig = next((i for i in range(n_or, len(bars)) if bars[i]["c"] > hi), None)
    if trig is None:
        return None
    close_min = market.OPEN_MIN + (trig + 1) * market.BAR_MIN
    if not (p["entry_from"] <= close_min <= market.entries_to_min(day)):
        return None
    if p["min_volume_x"] > 0 and bars[trig]["v"] < p["min_volume_x"] * avg_vol:
        return None
    e = bars[trig]["c"] * (1 + spread_frac / 2)
    stop = stop_price(p["stop_rule"], e, {"high": hi, "low": lo}, bars[: trig + 1])
    risk = e - stop
    if risk <= 0 or risk < max(e * 0.0005, 2 * spread_frac * e):
        return None
    target = e + p["target_r"] * risk
    end = (market.flat_by_min(day) - market.OPEN_MIN) // market.BAR_MIN
    outcome, r = "neither", None
    for b in bars[trig + 1: end]:
        if b["l"] <= stop:
            outcome, r = "stop", -1.0
            break
        if b["h"] >= target:
            outcome, r = "target", p["target_r"]
            break
    if r is None:
        r = (bars[min(end, len(bars)) - 1]["c"] - e) / risk
    n = shares_for(e, risk)
    exit_px = e + r * risk
    cost = costs.round_trip(n * e, n * exit_px, n) + n * e * spread_frac / 2
    return {"outcome": outcome, "r": r, "r_net": r - cost / (n * risk)}


def summarize(results):
    t = [x for x in results if x]
    n = len(t)
    if not n:
        return {"trades": 0, "target": 0, "stop": 0, "neither": 0, "win_pct": None, "avg_r": None, "avg_r_net": None}
    return {"trades": n, "target": sum(x["outcome"] == "target" for x in t), "stop": sum(x["outcome"] == "stop" for x in t),
            "neither": sum(x["outcome"] == "neither" for x in t),
            "win_pct": round(sum(x["r_net"] > 0 for x in t) / n * 100, 1),
            "avg_r": round(sum(x["r"] for x in t) / n, 3), "avg_r_net": round(sum(x["r_net"] for x in t) / n, 3)}
