"""Do a market-trend filter and a relative-strength filter make the bots' opening-range breakout pay?

    ..\\angelone_bot\\.venv\\Scripts\\python filters.py --market nse
    ..\\ibkr_us_bot\\.venv\\Scripts\\python  filters.py --market us

Filters (all use closes from before the trade's day only):
  market:  index (NIFTY 50 / S&P 500) above its 50-day average, its 200-day average, or both
  strength: stock beat the index over the last 1 month (21 days), 3 months (63 days), both, or is in the top 30%
            of the watchlist by 3-month return over the index
Test A: the bots' exact rule on 5-minute bars, the ~60 days Yahoo keeps.
Test B: the same idea on hourly bars over ~3 years: the first hour is the opening range, entry on the first hourly
        close above it, stop at its middle (or low), target in R, out at the last bar. Covers rising and falling
        markets, which a 60-day window cannot.
Each test: settings chosen on the first 2/3 of the days (pick), judged on the last 1/3 (check). The combination
proposed before the test (bot's rule + index above both averages + beats the index over 3 months) is also
reported as is, with no picking.
"""
import argparse
import itertools
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from lab import HERE, MIN_PICK_TRADES, Stocks

INDEX = {"nse": "^NSEI", "us": "^GSPC"}
ENTRY_TO = {"nse": 14 * 60 + 30, "us": 15 * 60 + 30}   # last minute a trigger bar may close
FULL_BARS = 7                                            # hourly bars in a full session (both markets)
CACHE = HERE / "cache" / "hourly"

MKT = {"any market": lambda g: True,
       "index > 50d": lambda g: g["up50"],
       "index > 200d": lambda g: g["up200"],
       "index > both": lambda g: g["up50"] and g["up200"]}
RS = {"any stock": lambda g: True,
      "beats index 1m": lambda g: g["rs21"] > 0,
      "beats index 3m": lambda g: g["rs63"] > 0,
      "beats index 1m+3m": lambda g: g["rs21"] > 0 and g["rs63"] > 0,
      "top 30% vs index 3m": lambda g: g["top30"]}
PROPOSED = ("index > both", "beats index 3m")


# ----------------------------------------------------------------------------------------------- features
def index_regime(d1):
    """{day: {"up50","up200"}} from the closes before that day, and {day: close}."""
    c = [b["c"] for b in d1]
    days = [b["t"].date() for b in d1]
    out = {}
    for i in range(201, len(c)):
        prev = c[i - 1]
        out[days[i]] = {"up50": prev > sum(c[i - 50:i]) / 50, "up200": prev > sum(c[i - 200:i]) / 200}
    return out, dict(zip(days, c))


def strength(closes, idx_close):
    """closes: [(day, close)] of one stock. {day: {"rs21","rs63","trend"}} using closes before that day."""
    out = {}
    days = [d for d, _ in closes]
    c = [x for _, x in closes]
    for i in range(64, len(c)):
        d0, d21, d63 = days[i - 1], days[i - 22], days[i - 64]
        if d0 not in idx_close or d21 not in idx_close or d63 not in idx_close:
            continue
        ir21, ir63 = idx_close[d0] / idx_close[d21] - 1, idx_close[d0] / idx_close[d63] - 1
        out[days[i]] = {"rs21": c[i - 1] / c[i - 22] - 1 - ir21, "rs63": c[i - 1] / c[i - 64] - 1 - ir63,
                        "trend": c[i - 1] > sum(c[i - 21:i - 1]) / 20}
    return out


def add_rank(feat):
    """top30: the stock's 3-month strength vs the index is in the top 30% of the stocks that day."""
    by_day = defaultdict(list)
    for (sym, day), g in feat.items():
        by_day[day].append(g)
    for gs in by_day.values():
        gs.sort(key=lambda g: g["rs63"], reverse=True)
        k = max(1, round(len(gs) * 0.3))
        for j, g in enumerate(gs):
            g["top30"] = j < k


# ----------------------------------------------------------------------------------------------- hourly sessions
def hourly(mk, data, sym, refresh):
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{mk.kind}_{sym}.json"
    if f.exists() and not refresh and time.time() - f.stat().st_mtime < 12 * 3600:
        raw = json.loads(f.read_text())
    else:
        raw = data.yahoo_bars(sym, "60m", "730d")
        f.write_text(json.dumps(raw, default=lambda o: o.isoformat()))
        time.sleep(0.2)
    return [{**b, "t": datetime.fromisoformat(b["t"]) if isinstance(b["t"], str) else b["t"]} for b in raw]


def sim_hour(mk, s, stop_rule, target_r):
    bs, sf = s["bars"], s["sf"]
    hi, lo = bs[0]["h"], bs[0]["l"]
    for i in range(1, s["last_entry"] + 1):
        if bs[i]["c"] <= hi:
            continue
        e = bs[i]["c"] * (1 + sf / 2)
        stop = (hi + lo) / 2 if stop_rule == "or-mid" else lo
        risk = e - stop
        if risk <= 0 or risk < 2 * sf * e:
            return None
        tgt = e + target_r * risk
        outcome, px = "neither", bs[-1]["c"] * (1 - sf / 2)
        for b in bs[i + 1:]:
            if b["o"] <= stop:
                outcome, px = "stop", b["o"]
                break
            if b["l"] <= stop:
                outcome, px = "stop", stop
                break
            if b["h"] >= tgt:
                outcome, px = "target", tgt
                break
        r = (px - e) / risk
        return {"outcome": outcome, "r": r, "r_net": r - mk.cost_r(s, e, risk, px)}
    return None


# ----------------------------------------------------------------------------------------------- scoring
def summ(ts):
    n = len(ts)
    if not n:
        return {"trades": 0, "win_pct": None, "avg_r": None, "avg_r_net": None, "total_r_net": 0}
    return {"trades": n, "win_pct": round(sum(t["r_net"] > 0 for t in ts) / n * 100, 1),
            "avg_r": round(sum(t["r"] for t in ts) / n, 3), "avg_r_net": round(sum(t["r_net"] for t in ts) / n, 3),
            "total_r_net": round(sum(t["r_net"] for t in ts), 1)}


def evaluate(name, results, feat, rules, cut):
    """results[rule_key] = [(sym, day, trade)]. Returns the report for one test."""
    rows = []
    for rk, mname, rname in itertools.product(rules, MKT, RS):
        mf, rf = MKT[mname], RS[rname]
        pick, check = [], []
        for sym, day, t in results[rk]:
            g = feat.get((sym, day))
            if g is None or not mf(g) or not rf(g):
                continue
            (pick if day < cut else check).append(t)
        rows.append({"rule": rk, "market": mname, "strength": rname, "pick": summ(pick), "check": summ(check),
                     "all": summ(pick + check)})
    live = rules[0]
    grid = {f"{m} | {r}": next(x["all"] for x in rows if x["rule"] == live and x["market"] == m and x["strength"] == r)
            for m in MKT for r in RS}
    proposed = next(x for x in rows if x["rule"] == live and (x["market"], x["strength"]) == PROPOSED)
    baseline = next(x for x in rows if x["rule"] == live and x["market"] == "any market" and x["strength"] == "any stock")
    ranked = sorted([x for x in rows if x["pick"]["trades"] >= MIN_PICK_TRADES],
                    key=lambda x: x["pick"]["avg_r_net"], reverse=True)
    judged = [x for x in rows if x["check"]["trades"] >= 10]
    pos = [x for x in judged if x["check"]["avg_r_net"] > 0 and (x["pick"]["avg_r_net"] or -1) > 0]
    best = ranked[0] if ranked else None
    if best and best["check"]["trades"] >= 15 and best["check"]["avg_r_net"] > 0 and best["pick"]["avg_r_net"] > 0:
        verdict = "PROMISING"
    elif best and (best["check"]["avg_r_net"] or 0) > 0:
        verdict = "NOT CONSISTENT"
    else:
        verdict = "NO RELIABLE EDGE"

    f = lambda v: "-" if v is None else v
    print(f"\n  --- {name} ---")
    print(f"  Bot's rule ({live}), R per trade after charges over all days, by filter:")
    print(f"    {'':<16}" + "".join(f"{r[:19]:>21}" for r in RS))
    for m in MKT:
        cells = [grid[f"{m} | {r}"] for r in RS]
        print(f"    {m:<16}" + "".join(f"{f(c['avg_r_net']):>13} ({c['trades']:>4})" for c in cells))
    for lab_, x in (("no filters", baseline), ("PROPOSED", proposed)):
        print(f"  {lab_:<11} pick {f(x['pick']['avg_r_net'])}R ({x['pick']['trades']}) | check {f(x['check']['avg_r_net'])}R "
              f"({x['check']['trades']}, {f(x['check']['win_pct'])}% won)")
    print(f"  {len(rows)} combinations; {len(pos)} of {len(judged)} made money on both pick and check")
    for x in ranked[:5]:
        print(f"    {x['rule']:<22} {x['market']:<13} {x['strength']:<20} pick {x['pick']['avg_r_net']:>6} ({x['pick']['trades']:>4}) "
              f"| check {f(x['check']['avg_r_net']):>6} ({x['check']['trades']:>4}, {f(x['check']['win_pct'])}%)")
    print(f"  verdict: {verdict}")
    return {"rule_live": live, "filter_grid_all_days": grid, "baseline": baseline, "proposed": proposed,
            "best_on_pick": best, "top": ranked[:10], "combinations": len(rows), "judged": len(judged),
            "positive_both": len(pos), "positive_rows": pos[:10], "verdict": verdict}


def by_year(results, feat, rk, combo):
    mf, rf = MKT[combo[0]], RS[combo[1]]
    out = defaultdict(list)
    for sym, day, t in results[rk]:
        g = feat.get((sym, day))
        if g is not None and mf(g) and rf(g):
            out[day[:4]].append(t)
    return {y: summ(v) for y, v in sorted(out.items())}


# ----------------------------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True, choices=list(INDEX))
    ap.add_argument("--stocks", type=int, default=100)
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    print(f"\n=== {a.market}: market-trend and relative-strength filters ===")
    mk = Stocks(a.market, a.stocks, a.refresh)
    import backtest, data
    idx_d1 = data.yahoo_bars(INDEX[a.market], "1d", "5y")
    regime, idx_close = index_regime(idx_d1)
    syms = sorted({s["sym"] for s in mk.sessions})
    today = mk.market.now().date()

    # ---- Test A: exact bot rule on 5m bars
    featA = {}
    for sym in syms:
        _, d1 = backtest._load(sym, False)
        st = strength([(b["t"].date(), b["c"]) for b in d1 if b["t"].date() < today], idx_close)
        for d, g in st.items():
            if d in regime:
                featA[(sym, d.isoformat())] = {**g, **regime[d]}
    add_rank(featA)
    rulesA = {}
    for stop_rule, tr, trend in itertools.product(("or-mid", "or-low"), (2.0, 1.5, 1.0), (True, False)):
        rulesA[f"{stop_rule} {tr}R trend {'on' if trend else 'off'}"] = mk.rule.live_params(
            stop_rule=stop_rule, target_r=tr, trend_filter=trend)
    resA = {k: [] for k in rulesA}
    for s in mk.sessions:
        for k, p in rulesA.items():
            t = mk.rule.simulate_day(s["raw"], p, None, s["trend"])
            if t:
                resA[k].append((s["sym"], s["day"], t))
    daysA = sorted({s["day"] for s in mk.sessions})
    cutA = daysA[int(len(daysA) * 2 / 3)]
    print(f"\n  Test A: {len(syms)} stocks, 5m bars {daysA[0]} to {daysA[-1]} ({len(daysA)} days, check from {cutA})")
    up = {k: sum(1 for d in daysA if regime.get(datetime.fromisoformat(d).date(), {}).get(k)) for k in ("up50", "up200")}
    print(f"  index above its 50-day average on {up['up50']} of these days, above its 200-day on {up['up200']}")
    repA = evaluate("Test A: bot's exact rule, 5-minute bars", resA, featA, list(rulesA), cutA)
    repA.update(days=[daysA[0], daysA[-1]], n_days=len(daysA), cut=cutA, index_up_days=up)

    # ---- Test B: hourly opening range over ~3 years
    sf = mk.config.ASSUMED_SPREAD_PCT / 100
    sessB, featB = [], {}
    for i, sym in enumerate(syms, 1):
        sys.stdout.write(f"\r  hourly {i}/{len(syms)} {sym:<14}")
        sys.stdout.flush()
        try:
            h1 = hourly(mk, data, sym, a.refresh)
        except Exception as ex:
            print(f"\n  {sym} skipped: {ex}")
            continue
        days = defaultdict(list)
        for b in h1:
            if None in (b.get("o"), b.get("h"), b.get("l"), b.get("c")):
                continue
            days[b["t"].date()].append(b)
        closes = []
        for d in sorted(days):
            bs = sorted(days[d], key=lambda b: b["t"])
            if d >= today or len(bs) != FULL_BARS:
                continue
            closes.append((d, bs[-1]["c"]))
            last_entry = max(j for j, b in enumerate(bs) if b["t"].hour * 60 + b["t"].minute + 60 <= ENTRY_TO[a.market])
            sessB.append({"sym": sym, "day": d.isoformat(), "bars": [{k: b[k] for k in "ohlc"} for b in bs],
                          "sf": sf, "last_entry": last_entry})
        for d, g in strength(closes, idx_close).items():
            if d in regime:
                featB[(sym, d.isoformat())] = {**g, **regime[d]}
    add_rank(featB)
    print()
    rulesB = list(itertools.product(("or-mid", "or-low"), (2.0, 1.5, 1.0), (True, False)))
    keyB = lambda sr, tr, trend: f"{sr} {tr}R trend {'on' if trend else 'off'}"
    resB = {keyB(*k): [] for k in rulesB}
    for s in sessB:
        g = featB.get((s["sym"], s["day"]))
        if g is None:
            continue   # warm-up days without 3 months of history: left out for every combination
        for sr, tr, trend in rulesB:
            if trend and not g["trend"]:
                continue
            t = sim_hour(mk, s, sr, tr)
            if t:
                resB[keyB(sr, tr, trend)].append((s["sym"], s["day"], t))
    daysB = sorted({d for (_, d) in featB})
    cutB = daysB[int(len(daysB) * 2 / 3)]
    upB = {k: sum(1 for d in daysB if regime.get(datetime.fromisoformat(d).date(), {}).get(k)) for k in ("up50", "up200")}
    print(f"\n  Test B: hourly bars {daysB[0]} to {daysB[-1]} ({len(daysB)} days, check from {cutB}); "
          f"index above 50d on {upB['up50']} days, above 200d on {upB['up200']}")
    repB = evaluate("Test B: first-hour range, hourly bars, ~3 years", resB, featB, [keyB(*k) for k in rulesB], cutB)
    repB.update(days=[daysB[0], daysB[-1]], n_days=len(daysB), cut=cutB, index_up_days=upB,
                proposed_by_year=by_year(resB, featB, keyB("or-mid", 2.0, True), PROPOSED),
                baseline_by_year=by_year(resB, featB, keyB("or-mid", 2.0, True), ("any market", "any stock")))
    print("  proposed, by year:", {y: (v["avg_r_net"], v["trades"]) for y, v in repB["proposed_by_year"].items()})
    print("  no filters, by year:", {y: (v["avg_r_net"], v["trades"]) for y, v in repB["baseline_by_year"].items()})

    out = {"market": a.market, "run_at": datetime.now().isoformat(timespec="seconds"), "stocks": len(syms),
           "index": INDEX[a.market], "proposed": PROPOSED, "test_a": repA, "test_b": repB}
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / f"filters_{a.market}.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"\n  saved results/filters_{a.market}.json ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
