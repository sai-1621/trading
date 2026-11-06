"""Skip-rule test, as pre-registered in PREREG_skip_rule.md.

Replays the live opening-range rule on past sessions, asks the Time Machine (using only earlier sessions) whether the
bot would have taken each signal, and compares taken and skipped signals after costs.

    .venv\\Scripts\\python skip_rule_test.py      -> trader/skip_rule_results.json
"""
import json
import math
import random
import sys
import time
from collections import defaultdict
from datetime import date, datetime

import config
import costs
import data
import market
from skills import rule
from skills.exit_plan import stop_price
from skills.timemachine import WINDOW, full_sessions, needed_bars

REPLAYS = 2000
LIVE_FROM = date(2026, 10, 27)
LAST_DAY = date(2026, 11, 5)          # sessions before this day
OUT = config.BASE_DIR / "trader" / "skip_rule_results.json"
FRESH = config.CACHE_DIR / "skiptest"


def _fix(bars):
    return [{**b, "t": datetime.fromisoformat(b["t"]) if isinstance(b["t"], str) else b["t"]} for b in bars]


def load(sym):
    """Backtest cache (older) merged with a fresh download (newer); newer bars win on the same timestamp."""
    old = json.loads((config.CACHE_DIR / f"bt_{sym}.json").read_text())
    FRESH.mkdir(exist_ok=True)
    f = FRESH / f"{sym}.json"
    if f.exists():
        new = json.loads(f.read_text())
    else:
        new = {"m5": data.yahoo_bars(sym, "5m", "60d"), "d1": data.yahoo_bars(sym, "1d", "1y")}
        f.write_text(json.dumps(new, default=lambda o: o.isoformat()))
        time.sleep(0.4)
    out = {}
    for k in ("m5", "d1"):
        merged = {}
        for b in _fix(old[k]) + _fix(new[k]):
            merged[b["t"]] = b
        out[k] = [merged[t] for t in sorted(merged)]
    return out


def signal(bars, p, above):
    """rule.simulate_day, returning the plan details the Time Machine needs."""
    if p["trend_filter"] and above is False:
        return None
    day = bars[0]["t"].date()
    sf = config.ASSUMED_SPREAD_PCT / 100
    n_or = p["or_minutes"] // market.BAR_MIN
    hi, lo = max(b["h"] for b in bars[:n_or]), min(b["l"] for b in bars[:n_or])
    trig = next((i for i in range(n_or, len(bars)) if bars[i]["c"] > hi), None)
    if trig is None:
        return None
    close_min = market.OPEN_MIN + (trig + 1) * market.BAR_MIN
    if not (p["entry_from"] <= close_min <= market.entries_to_min(day)):
        return None
    e = bars[trig]["c"] * (1 + sf / 2)
    stop = stop_price(p["stop_rule"], e, {"high": hi, "low": lo}, bars[: trig + 1])
    risk = e - stop
    if risk <= 0 or risk < max(e * 0.0005, 2 * sf * e):
        return None
    res = rule.simulate_day(bars, p, None, above)
    return {"entry": e, "stop": stop, "target": e + p["target_r"] * risk, "target_r": p["target_r"],
            "entry_bar_index": trig, "spread": sf * e, **res}


def time_machine(plan, prior, day):
    """skills/timemachine.run on the given prior sessions (no download)."""
    pool = defaultdict(list)
    for bs in prior.values():
        for i in range(1, len(bs)):
            pc, b = bs[i - 1]["c"], bs[i]
            pool[i].append((b["o"] / pc, b["h"] / pc, b["l"] / pc, b["c"] / pc))
    e, stop, target, R = plan["entry"], plan["stop"], plan["target"], plan["target_r"]
    risk = e - stop
    start = plan["entry_bar_index"] + 1
    end = (market.flat_by_min(day) - market.OPEN_MIN) // market.BAR_MIN
    if start >= end:
        return None
    slots = [[r for k in range(max(1, s - WINDOW), min(needed_bars(), s + WINDOW + 1)) for r in pool[k]]
             for s in range(start, end)]
    rng = random.Random(config.TM_SEED)
    tf = sf = 0
    r_total = 0.0
    for _ in range(REPLAYS):
        p, res = e, None
        for cands in slots:
            o, h, l, c = rng.choice(cands)
            if p * l <= stop:
                res, sf = -1.0, sf + 1
                break
            if p * h >= target:
                res, tf = R, tf + 1
                break
            p *= c
        if res is None:
            res = (p - e) / risk
        r_total += res
    shares = rule.shares_for(e, risk)
    cost_r = (costs.round_trip(shares * e, shares * e, shares) + shares * plan["spread"]) / (shares * risk)
    avg_net = r_total / REPLAYS - cost_r
    decided = tf + sf
    share = tf / decided if decided else 0.0
    edge = (share - (1 + cost_r) / (1 + R)) * 100
    return {"take": decided > 0 and edge >= config.TM_MIN_EDGE_PTS and avg_net > 0, "tm_avg_net": avg_net,
            "edge": edge}


def stats(xs):
    n = len(xs)
    if n == 0:
        return {"trades": 0}
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0
    return {"trades": n, "avg_r_net": round(m, 3), "t": round(m / (sd / math.sqrt(n)), 2) if sd else 0.0,
            "win_pct": round(sum(x > 0 for x in xs) / n * 100, 1), "sum_r": round(sum(xs), 1),
            "dollars_at_50_risk": round(sum(xs) * config.MAX_LOSS_PER_TRADE, 0)}


def corr(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va, vb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
    return round(cov / math.sqrt(va * vb), 3) if va and vb else 0.0


def main():
    t0 = time.time()
    syms = sorted(f.stem[3:] for f in config.CACHE_DIR.glob("bt_*.json"))
    p = rule.live_params()
    rows = []
    for k, sym in enumerate(syms, 1):
        try:
            d = load(sym)
        except Exception as e:  # noqa: BLE001
            print(f"  {sym} skipped: {e}")
            continue
        full = full_sessions(d["m5"], LAST_DAY)
        trends = rule.trend_map(d["d1"])
        days = sorted(full)
        for i, day in enumerate(days):
            prior = {x: full[x] for x in days[:i] if (day - x).days <= 60}
            if len(prior) < config.TM_MIN_DAYS:
                continue
            s = signal(full[day], p, trends.get(day))
            if not s:
                continue
            tm = time_machine({**s, "symbol": sym}, prior, day)
            if tm is None:
                continue
            rows.append({"symbol": sym, "day": day.isoformat(), "r_net": s["r_net"], "outcome": s["outcome"], **tm})
        sys.stdout.write(f"\r  {k}/{len(syms)} stocks, {len(rows)} signals ({time.time() - t0:.0f}s)   ")
        sys.stdout.flush()
    print()

    rep = {"prereg": "PREREG_skip_rule.md", "replays": REPLAYS, "stocks": len(syms), "periods": {}}
    for name, sel in (("decision", lambda r: r["day"] < LIVE_FROM.isoformat()),
                      ("live_week", lambda r: r["day"] >= LIVE_FROM.isoformat())):
        rs = [r for r in rows if sel(r)]
        if not rs:
            continue
        days = sorted({r["day"] for r in rs})
        taken = [r["r_net"] for r in rs if r["take"]]
        skipped = [r["r_net"] for r in rs if not r["take"]]
        ranked = sorted(rs, key=lambda r: r["tm_avg_net"])
        half = len(ranked) // 2
        rep["periods"][name] = {
            "sessions": len(days), "from": days[0], "to": days[-1],
            "all": stats([r["r_net"] for r in rs]), "taken": stats(taken), "skipped": stats(skipped),
            "tm_score_vs_result_corr": corr([r["tm_avg_net"] for r in rs], [r["r_net"] for r in rs]),
            "tm_bottom_half": stats([r["r_net"] for r in ranked[:half]]),
            "tm_top_half": stats([r["r_net"] for r in ranked[half:]])}
    d = rep["periods"].get("decision", {})
    sk, al = d.get("skipped", {}), d.get("all", {})
    passed = bool(sk.get("trades") and sk["avg_r_net"] > 0 and sk["t"] >= 2.0 and al["avg_r_net"] > 0)
    rep["decision"] = {"PASS": passed,
                       "meaning": "skipped signals made money: loosen the skip rule" if passed
                       else "keep the skip rule: skipped signals did not make money reliably"}
    OUT.write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, indent=1))
    print(f"saved {OUT}  ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
