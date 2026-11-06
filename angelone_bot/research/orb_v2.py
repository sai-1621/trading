"""ORB v2 search, exactly as pre-registered in research/PREREG_orb_v2.md.

    .venv\\Scripts\\python research\\orb_v2.py

Reads the cached Angel One bars (cache/angel_bt_<SYM>_730.json, written by backtest_pipeline.py --source angel),
runs 128 rule variants on the pick period, chooses one by the pre-registered rule, and only then runs the
chosen variant and the current live rules on the holdout. Writes research/orb_v2_results.json.
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
import data  # noqa: E402
import market  # noqa: E402
from backtest_pipeline import bot_limits, equity  # noqa: E402
from skills import rule  # noqa: E402
from skills.timemachine import full_sessions  # noqa: E402

PICK = (date(2024, 12, 3), date(2025, 12, 31))
HOLD = (date(2026, 1, 1), date(2026, 10, 30))
GRID = {"or_minutes": [15, 30], "stop_rule": ["or-mid", "or-low", "atr:3", "pct:1.0"],
        "target_r": [0.5, 1.0, 1.5, 2.0], "entry_to": ["10:30", "14:30"], "market": ["none", "nifty20"]}
LIVE = {"or_minutes": 15, "stop_rule": "or-mid", "target_r": 2.0, "entry_to": "14:30", "market": "none"}
MIN_T, MIN_TRADES, HOLD_T = 3.0, 200, 2.0

_STOCKS = None
_NIFTY = None


def load_stocks():
    out = {}
    for f in sorted(config.CACHE_DIR.glob("angel_bt_*_730.json")):
        sym = f.name[len("angel_bt_"):-len("_730.json")]
        raw = json.loads(f.read_text())
        fix = lambda bars: [{**b, "t": datetime.fromisoformat(b["t"])} for b in bars]
        m5, d1 = fix(raw["m5"]), fix(raw["d1"])
        out[sym] = (full_sessions(m5, HOLD[1].replace(day=31)), rule.trend_map(d1))
    return out


def _init(stocks=None, nifty=None):
    """Each worker loads the bars itself (sending them over a pipe ran out of memory)."""
    global _STOCKS, _NIFTY
    _STOCKS, _NIFTY = (stocks if stocks is not None else load_stocks()), nifty


def variant_params(v):
    return rule.live_params(or_minutes=v["or_minutes"], stop_rule=v["stop_rule"], target_r=v["target_r"],
                            entry_to=market.hhmm(v["entry_to"]), trend_filter=True,
                            min_volume_x=1.0, entry_from=market.hhmm("09:30"), flat_by=market.hhmm("15:10"))


def trades(v, period):
    p = variant_params(v)
    rows = []
    for sym, (full, trends) in _STOCKS.items():
        for d, bars in full.items():
            if not (period[0] <= d <= period[1]):
                continue
            if v["market"] == "nifty20" and _NIFTY.get(d) is not True:
                continue
            res = rule.simulate_day(bars, p, None, trends.get(d))
            if not res:
                continue
            rows.append({"symbol": sym, "day": d, "entry_bar": res["entry_bar"], "exit_bar": res["exit_bar"],
                         "outcome": res["outcome"], "r": res["r"], "r_net": res["r_net"],
                         "pnl": res["r_net"] * res["shares"] * res["risk"]})
    return rows


def stats(rows):
    n = len(rows)
    if n < 2:
        return {"trades": n}
    r = [x["r_net"] for x in rows]
    m = sum(r) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in r) / (n - 1))
    wins = sum(x > 0 for x in r)
    return {"trades": n, "win_pct": round(wins / n * 100, 1), "avg_r_net": round(m, 3),
            "avg_r_gross": round(sum(x["r"] for x in rows) / n, 3),
            "t": round(m / sd * math.sqrt(n), 2) if sd else None, "rupees": round(sum(x["pnl"] for x in rows)),
            "targets": sum(x["outcome"] == "target" for x in rows), "stops": sum(x["outcome"] == "stop" for x in rows)}


def run_pick(v):
    return {**v, **stats(trades(v, PICK))}


def bot(rows):
    live = bot_limits(rows)
    eq = equity(live)
    return {**stats(live), **{k: v for k, v in eq.items() if k != "curve"}}


def main():
    t0 = time.time()
    stocks = load_stocks()
    nifty = rule.trend_map(data.yahoo_bars("^NSEI", "1d", "5y"))
    print(f"{len(stocks)} stocks loaded ({time.time() - t0:.0f}s)")
    variants = [dict(zip(GRID, combo)) for combo in itertools.product(*GRID.values())]
    with Pool(4, initializer=_init, initargs=(None, nifty)) as pool:
        pick = pool.map(run_pick, variants)
    _init(stocks, nifty)
    print(f"pick period done: {len(pick)} variants ({time.time() - t0:.0f}s)")

    eligible = [x for x in pick if x.get("trades", 0) >= MIN_TRADES and (x.get("avg_r_net") or 0) > 0
                and (x.get("t") or 0) >= MIN_T]
    eligible.sort(key=lambda x: (x["win_pct"], x["t"]), reverse=True)
    live_pick = next(x for x in pick if all(x[k] == LIVE[k] for k in LIVE))
    rep = {"run_at": datetime.now().isoformat(), "pick_period": [str(d) for d in PICK],
           "holdout": [str(d) for d in HOLD], "variants": len(pick), "eligible": len(eligible),
           "live_rules_pick": live_pick,
           "top_pick_by_t": sorted(pick, key=lambda x: x.get("t") or -99, reverse=True)[:15],
           "top_pick_by_win": sorted(pick, key=lambda x: x.get("win_pct") or 0, reverse=True)[:10],
           "eligible_list": eligible}
    if eligible:
        chosen = {k: eligible[0][k] for k in GRID}
        h_new, h_live = trades(chosen, HOLD), trades(LIVE, HOLD)
        s_new, s_live = stats(h_new), stats(h_live)
        b_new, b_live = bot(h_new), bot(h_live)
        passed = ((s_new.get("avg_r_net") or 0) > 0 and (s_new.get("t") or 0) >= HOLD_T
                  and (s_new.get("win_pct") or 0) > (s_live.get("win_pct") or 0) and b_new["total_rupees"] > 0)
        rep.update({"chosen": chosen, "chosen_pick": eligible[0], "holdout_chosen": s_new,
                    "holdout_live_rules": s_live, "holdout_bot_chosen": b_new, "holdout_bot_live_rules": b_live,
                    "passed": passed})
    else:
        rep["passed"] = False
        rep["note"] = "no variant met the pre-registered choice rule on the pick period; holdout not looked at"
    out = Path(__file__).with_name("orb_v2_results.json")
    out.write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps({k: v for k, v in rep.items() if k not in ("top_pick_by_t", "top_pick_by_win", "eligible_list")},
                     indent=1, default=str))
    print("top 15 on the pick period by t:")
    for x in rep["top_pick_by_t"]:
        print(f"  {x['or_minutes']:>2} {x['stop_rule']:<7} {x['target_r']:>3}R to {x['entry_to']} {x['market']:<7}"
              f" n={x.get('trades')} win={x.get('win_pct')}% avgR={x.get('avg_r_net')} gross={x.get('avg_r_gross')} t={x.get('t')}")
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
