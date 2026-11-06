"""Intraday mean reversion v1, exactly as pre-registered in research/PREREG_meanrev_v1.md.

    .venv\\Scripts\\python research\\meanrev_v1.py

Reads the cached Angel One bars, runs 48 variants on the pick period, chooses one by the pre-registered rule,
and only then runs it (and the current live ORB rules, for the win-rate comparison) on the holdout.
Writes research/meanrev_v1_results.json.
"""
import itertools
import json
import math
import sys
import time
from datetime import date, datetime
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import costs  # noqa: E402
import data  # noqa: E402
import market  # noqa: E402
from backtest_pipeline import bot_limits, equity  # noqa: E402
from skills import rule  # noqa: E402
from skills.timemachine import full_sessions  # noqa: E402

PICK = (date(2024, 12, 3), date(2025, 12, 31))
HOLD = (date(2026, 1, 1), date(2026, 10, 30))
GRID = {"d": [0.5, 0.75, 1.0], "s": [0.5, 1.0], "target": ["vwap", "open"], "stock": ["none", "ma50"],
        "market": ["none", "nifty20"]}
MIN_T, MIN_TRADES, HOLD_T = 3.0, 200, 2.0
ENTRY_FROM, ENTRY_TO, FLAT_BY = market.hhmm("09:45"), market.hhmm("14:00"), market.hhmm("15:10")
SPREAD = config.ASSUMED_SPREAD_PCT / 100
LIVE_ORB = {"or_minutes": 15, "stop_rule": "or-mid", "target_r": 2.0, "entry_to": "14:30"}

_STOCKS = None
_NIFTY = None


def daily_context(d1):
    """{date: {"atr": 14-day ATR, "ma50": previous close above its 50-day average}} using bars before that date."""
    out = {}
    for i in range(51, len(d1)):
        prev = d1[:i]
        trs = [max(b["h"] - b["l"], abs(b["h"] - prev[j - 1]["c"]), abs(b["l"] - prev[j - 1]["c"]))
               for j, b in enumerate(prev) if j > 0][-14:]
        closes = [b["c"] for b in prev[-50:]]
        out[d1[i]["t"].date()] = {"atr": sum(trs) / len(trs), "ma50": closes[-1] > sum(closes) / 50}
    return out


def load_stocks():
    out = {}
    for f in sorted(config.CACHE_DIR.glob("angel_bt_*_730.json")):
        sym = f.name[len("angel_bt_"):-len("_730.json")]
        raw = json.loads(f.read_text())
        fix = lambda bars: [{**b, "t": datetime.fromisoformat(b["t"])} for b in bars]
        m5, d1 = fix(raw["m5"]), fix(raw["d1"])
        out[sym] = (full_sessions(m5, date(2026, 10, 31)), daily_context(d1))
    return out


def _init(nifty):
    global _STOCKS, _NIFTY
    _STOCKS, _NIFTY = load_stocks(), nifty


def simulate(bars, ctx, v):
    atr = ctx["atr"]
    cum_pv = cum_v = 0.0
    vwap = []
    for b in bars:
        cum_pv += (b["h"] + b["l"] + b["c"]) / 3 * b["v"]
        cum_v += b["v"]
        vwap.append(cum_pv / cum_v if cum_v else b["c"])
    end = (FLAT_BY - market.OPEN_MIN) // market.BAR_MIN          # bars before 15:10
    trig = None
    for i in range(len(bars)):
        close_min = market.OPEN_MIN + (i + 1) * market.BAR_MIN
        if close_min < ENTRY_FROM:
            continue
        if close_min > ENTRY_TO:
            break
        if bars[i]["c"] <= vwap[i] - v["d"] * atr:
            trig = i
            break
    if trig is None:
        return None
    e = bars[trig]["c"] * (1 + SPREAD / 2)
    stop = e - v["s"] * atr
    risk = e - stop
    if risk <= 0 or risk < max(e * 0.0005, 2 * SPREAD * e):
        return None
    outcome, exit_px, exit_bar = "neither", None, min(end, len(bars)) - 1
    for k in range(trig + 1, min(end, len(bars))):
        b = bars[k]
        tgt = vwap[k - 1] if v["target"] == "vwap" else bars[0]["o"]
        if b["l"] <= stop:
            outcome, exit_px, exit_bar = "stop", min(b["o"], stop), k
            break
        if tgt > e and b["h"] >= tgt:
            outcome, exit_px, exit_bar = "target", max(b["o"], tgt), k
            break
    if exit_px is None:
        exit_px = bars[exit_bar]["c"]
    exit_px *= (1 - SPREAD / 2) if outcome == "neither" else 1
    r = (exit_px - e) / risk
    value = rule.position_value(e, risk)
    shares = value / e
    cost_r = (costs.round_trip(value, shares * exit_px) + shares * e * SPREAD / 2) / (shares * risk)
    return {"outcome": outcome, "r": r, "r_net": r - cost_r, "entry_bar": trig, "exit_bar": exit_bar,
            "pnl": (r - cost_r) * shares * risk}


def trades(v, period):
    rows = []
    for sym, (full, ctx) in _STOCKS.items():
        for d, bars in full.items():
            if not (period[0] <= d <= period[1]) or d not in ctx:
                continue
            if v["stock"] == "ma50" and not ctx[d]["ma50"]:
                continue
            if v["market"] == "nifty20" and _NIFTY.get(d) is not True:
                continue
            res = simulate(bars, ctx[d], v)
            if res:
                rows.append({"symbol": sym, "day": d, **res})
    return rows


def orb_trades(period):
    p = rule.live_params(or_minutes=15, stop_rule="or-mid", target_r=2.0, entry_to=market.hhmm("14:30"),
                         trend_filter=True, min_volume_x=1.0, entry_from=market.hhmm("09:30"), flat_by=FLAT_BY)
    rows = []
    for sym, (full, ctx) in _STOCKS.items():
        for d, bars in full.items():
            if period[0] <= d <= period[1]:
                res = rule.simulate_day(bars, p, None, None)   # trend filter not applied here: win rate reference only
                if res:
                    rows.append({"r_net": res["r_net"], "r": res["r"], "outcome": res["outcome"], "pnl": 0,
                                 "day": d, "entry_bar": res["entry_bar"], "exit_bar": res["exit_bar"]})
    return rows


def stats(rows):
    n = len(rows)
    if n < 2:
        return {"trades": n}
    r = [x["r_net"] for x in rows]
    m = sum(r) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in r) / (n - 1))
    return {"trades": n, "win_pct": round(sum(x > 0 for x in r) / n * 100, 1), "avg_r_net": round(m, 3),
            "avg_r_gross": round(sum(x["r"] for x in rows) / n, 3),
            "t": round(m / sd * math.sqrt(n), 2) if sd else None, "rupees": round(sum(x["pnl"] for x in rows)),
            "targets": sum(x["outcome"] == "target" for x in rows), "stops": sum(x["outcome"] == "stop" for x in rows),
            "neither": sum(x["outcome"] == "neither" for x in rows)}


def run_pick(v):
    return {**v, **stats(trades(v, PICK))}


def main():
    t0 = time.time()
    nifty = rule.trend_map(data.yahoo_bars("^NSEI", "1d", "5y"))
    variants = [dict(zip(GRID, c)) for c in itertools.product(*GRID.values())]
    with Pool(4, initializer=_init, initargs=(nifty,)) as pool:
        pick = pool.map(run_pick, variants)
    print(f"pick period: {len(pick)} variants ({time.time() - t0:.0f}s)")
    eligible = [x for x in pick if x.get("trades", 0) >= MIN_TRADES and (x.get("avg_r_net") or 0) > 0
                and (x.get("t") or 0) >= MIN_T]
    eligible.sort(key=lambda x: (x["win_pct"], x["t"]), reverse=True)
    rep = {"run_at": datetime.now().isoformat(), "pick_period": [str(d) for d in PICK],
           "holdout": [str(d) for d in HOLD], "variants": len(pick), "eligible": len(eligible),
           "all_pick": sorted(pick, key=lambda x: x.get("t") or -99, reverse=True), "eligible_list": eligible}
    if eligible:
        _init(nifty)
        chosen = {k: eligible[0][k] for k in GRID}
        h = trades(chosen, HOLD)
        s_new, s_orb = stats(h), stats(orb_trades(HOLD))
        live = bot_limits(h)
        b_new = {**stats(live), **{k: v for k, v in equity(live).items() if k != "curve"}}
        passed = ((s_new.get("avg_r_net") or 0) > 0 and (s_new.get("t") or 0) >= HOLD_T
                  and (s_new.get("win_pct") or 0) > (s_orb.get("win_pct") or 0) and b_new["total_rupees"] > 0)
        rep.update({"chosen": chosen, "chosen_pick": eligible[0], "holdout_chosen": s_new,
                    "holdout_live_orb_win_ref": s_orb, "holdout_bot_chosen": b_new,
                    "holdout_equity_curve": equity(live)["curve"], "passed": passed})
    else:
        rep["passed"] = False
        rep["note"] = "no variant met the pre-registered choice rule on the pick period; holdout not looked at"
    Path(__file__).with_name("meanrev_v1_results.json").write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps({k: v for k, v in rep.items() if k not in ("all_pick", "eligible_list", "holdout_equity_curve")},
                     indent=1, default=str))
    print("all 48 on the pick period, best t first:")
    for x in rep["all_pick"]:
        print(f"  D{x['d']:<4} S{x['s']:<3} {x['target']:<4} {x['stock']:<4} {x['market']:<7} n={x.get('trades')}"
              f" win={x.get('win_pct')}% avgR={x.get('avg_r_net')} gross={x.get('avg_r_gross')} t={x.get('t')}")
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
