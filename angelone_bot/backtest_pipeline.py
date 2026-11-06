"""Walk-forward backtest of the bot's whole day-trading skill on NSE, as the live bot runs it:
opening-range breakout (skills/rule.py) -> trend filter -> Time Machine verdict (skills/timemachine.replay) ->
the bot's limits (max trades a day, max open positions, daily loss limit) -> Angel One charges.

    .venv\\Scripts\\python backtest_pipeline.py                 the 60 most traded stocks (cached 5m bars)
    .venv\\Scripts\\python backtest_pipeline.py --replays 10000 the live replay count (slower)
    .venv\\Scripts\\python backtest_pipeline.py --source angel --days 730
                                        2 years of 5-minute bars from Angel One's historical API
                                        (getCandleData; needs the Angel One keys in .env; read-only, no orders)

For each trading day the Time Machine sees ONLY the sessions before that day (no look-ahead), and needs
TM_MIN_DAYS of them, so the first ~20 of the ~60 Yahoo sessions are warm-up. A random filter that skips the same
number of trades is the baseline the Time Machine has to beat. Writes trader/backtest_pipeline.json.
Known limits: 60 days of 5-minute data (Yahoo's maximum; Angel One keys would allow years); the stock list is
today's most traded (mild look-ahead); the level setup (20-day high) is not simulated, only the opening range.
"""
import argparse
import json
import math
import random
import sys
import time
from collections import defaultdict
from datetime import datetime

import config
from backtest import _load
import json as _json
from market import now
from skills import rule, scanner, write_json
from skills.timemachine import full_sessions, replay


TM_LOOKBACK = 42     # the live Time Machine reads 60 calendar days of 5-minute bars: about 42 sessions


def stats(rows):
    if not rows:
        return {"trades": 0}
    r = [x["r_net"] for x in rows]
    n = len(r)
    m = sum(r) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in r) / (n - 1)) if n > 1 else 0.0
    wins = [x for x in r if x > 0]
    losses = [x for x in r if x <= 0]
    pf = sum(wins) / -sum(losses) if losses and sum(losses) < 0 else float("inf")
    return {"trades": n, "win_pct": round(len(wins) / n * 100, 1), "avg_r_net": round(m, 3),
            "avg_r_gross": round(sum(x["r"] for x in rows) / n, 3), "profit_factor": round(pf, 2),
            "t": round(m / sd * math.sqrt(n), 2) if sd > 0 else None,
            "rupees": round(sum(x["pnl"] for x in rows)),
            "targets": sum(x["outcome"] == "target" for x in rows), "stops": sum(x["outcome"] == "stop" for x in rows),
            "neither": sum(x["outcome"] == "neither" for x in rows)}


def bot_limits(rows):
    """What the live bot would actually have traded: per day, in trigger order, at most MAX_TRADES_PER_DAY,
    at most MAX_OPEN_POSITIONS at once, and nothing new after the day's losses reach MAX_DAILY_LOSS."""
    out = []
    by_day = defaultdict(list)
    for x in rows:
        by_day[x["day"]].append(x)
    for d in sorted(by_day):
        taken, lost = [], 0.0
        for x in sorted(by_day[d], key=lambda y: (y["entry_bar"], y["symbol"])):
            if len(taken) >= config.MAX_TRADES_PER_DAY or lost >= config.MAX_DAILY_LOSS:
                break
            # losses of trades already closed by this bar count toward the daily limit
            lost = -sum(t["pnl"] for t in taken if t["exit_bar"] < x["entry_bar"] and t["pnl"] < 0)
            if lost >= config.MAX_DAILY_LOSS:
                break
            if sum(t["exit_bar"] >= x["entry_bar"] for t in taken) >= config.MAX_OPEN_POSITIONS:
                continue
            taken.append(x)
        out += taken
    return out


def equity(rows):
    by_day = defaultdict(float)
    for x in rows:
        by_day[x["day"]] += x["pnl"]
    eq, peak, dd, curve = 0.0, 0.0, 0.0, []
    for d in sorted(by_day):
        eq += by_day[d]
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
        curve.append((str(d), round(eq)))
    return {"days_traded": len(by_day), "total_rupees": round(eq), "max_drawdown_rupees": round(dd),
            "best_day": round(max(by_day.values())) if by_day else 0,
            "worst_day": round(min(by_day.values())) if by_day else 0, "curve": curve}


def load_angel(client, sym, days, refresh=False):
    """5-minute and daily bars from Angel One's getCandleData, cached for 12 hours in cache/angel_bt_<SYM>_<days>.json."""
    f = config.CACHE_DIR / f"angel_bt_{sym}_{days}.json"
    if f.exists() and not refresh and time.time() - f.stat().st_mtime < 12 * 3600:
        raw = _json.loads(f.read_text())
        fix = lambda bars: [{**b, "t": datetime.fromisoformat(b["t"])} for b in bars]
        return fix(raw["m5"]), fix(raw["d1"])
    m5 = client.candles(sym, "FIVE_MINUTE", days)
    d1 = client.candles(sym, "ONE_DAY", days + 60)
    f.write_text(_json.dumps({"m5": m5, "d1": d1}, default=lambda o: o.isoformat()))
    return m5, d1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=config.WATCH_SIZE)
    ap.add_argument("--replays", type=int, default=2000)
    ap.add_argument("--placebo", type=int, default=2000)
    ap.add_argument("--source", choices=["yahoo", "angel"], default="yahoo")
    ap.add_argument("--days", type=int, default=730, help="history for --source angel (calendar days)")
    ap.add_argument("--gap", type=float, default=2.0,
                    help="seconds between Angel One requests (2 s leaves room for the live bot on the same account)")
    a = ap.parse_args()
    client = None
    if a.source == "angel":
        import angel_client
        from angel_client import AngelClient
        angel_client.HIST_GAP = a.gap
        try:
            client = AngelClient()
        except Exception as e:  # noqa: BLE001
            print(f"Cannot log in to Angel One ({e}). Fill the Angel One keys in .env, or run without --source angel.")
            return 1
    t0 = time.time()
    old = config.WATCH_SIZE
    config.WATCH_SIZE = a.stocks
    hits = scanner.scan(write=False)["hits"]
    config.WATCH_SIZE = old
    today = now().date()
    p = rule.live_params()
    spread_frac = config.ASSUMED_SPREAD_PCT / 100
    rows, n_stocks, test_days = [], 0, set()
    for i, h in enumerate(hits, 1):
        sym = h["symbol"]
        try:
            m5, d1 = load_angel(client, sym, a.days) if client else _load(sym, False)
        except Exception as e:  # noqa: BLE001
            print(f"  {sym} skipped: {e}")
            continue
        full = full_sessions(m5, today)
        trends = rule.trend_map(d1)
        days = sorted(full)
        if len(days) <= config.TM_MIN_DAYS:
            continue
        n_stocks += 1
        for j in range(config.TM_MIN_DAYS, len(days)):
            d = days[j]
            test_days.add(d)
            res = rule.simulate_day(full[d], p, None, trends.get(d))
            if not res:
                continue
            prior = {k: full[k] for k in days[max(0, j - TM_LOOKBACK):j]}   # what the live bot sees: ~60 days
            plan = {"symbol": sym, "entry": res["entry"], "stop": res["stop"], "target": res["target"],
                    "target_r": p["target_r"], "entry_bar_index": res["entry_bar"]}
            tm = replay(prior, plan, res["entry"] * spread_frac, replays=a.replays)
            rows.append({"symbol": sym, "day": d, "entry_bar": res["entry_bar"], "exit_bar": res["exit_bar"],
                         "outcome": res["outcome"], "r": res["r"], "r_net": res["r_net"],
                         "pnl": res["r_net"] * res["shares"] * res["risk"],
                         "take": bool(tm.get("ok") and tm.get("take")), "verdict": tm.get("verdict", tm.get("reason")),
                         "edge_pts": tm.get("edge_pts"), "tm_avg_r_net": tm.get("avg_r_after_costs")})
        sys.stdout.write(f"\r  {i}/{len(hits)} {sym:<12} {len(rows)} trades so far ({time.time() - t0:.0f}s)   ")
        sys.stdout.flush()
    print()
    take = [x for x in rows if x["take"]]
    skip = [x for x in rows if not x["take"]]

    # baseline: skip the same number of trades at random
    rng = random.Random(7)
    plac = []
    for _ in range(a.placebo):
        pick = rng.sample(rows, len(take)) if len(take) <= len(rows) else rows
        plac.append(sum(x["r_net"] for x in pick) / max(1, len(pick)))
    plac.sort()
    placebo = {"avg_r_net_mean": round(sum(plac) / len(plac), 3), "p95": round(plac[int(0.95 * len(plac))], 3),
               "share_beating_tm": round(sum(v >= (stats(take).get("avg_r_net") or -9) for v in plac) / len(plac), 3)}

    live_tm = bot_limits(take)
    live_all = bot_limits(rows)
    test_days = sorted(test_days)
    rep = {"run_at": now().isoformat(), "source": a.source, "stocks": n_stocks, "test_days": len(test_days),
           "period": [str(test_days[0]), str(test_days[-1])] if test_days else None,
           "settings": {k: getattr(config, k) for k in ("OR_MINUTES", "STOP_RULE", "TARGET_R", "TREND_FILTER",
                                                         "NO_ENTRIES_BEFORE", "NO_ENTRIES_AFTER", "FLAT_BY",
                                                         "MAX_LOSS_PER_TRADE", "MAX_TRADES_PER_DAY",
                                                         "MAX_OPEN_POSITIONS", "MAX_DAILY_LOSS", "TM_MIN_EDGE_PTS")},
           "replays": a.replays,
           "all_orb_trades": stats(rows), "time_machine_take": stats(take), "time_machine_skip": stats(skip),
           "random_skip_baseline": placebo,
           "bot_with_time_machine": {**stats(live_tm), **{k: v for k, v in equity(live_tm).items() if k != "curve"}},
           "bot_without_time_machine": {**stats(live_all), **{k: v for k, v in equity(live_all).items() if k != "curve"}},
           "equity_curve_with_tm": equity(live_tm)["curve"],
           "verdicts": {v: stats([x for x in rows if x["verdict"] == v]) for v in sorted({x["verdict"] for x in rows})}}
    write_json("backtest_pipeline.json", rep)
    print(json.dumps({k: v for k, v in rep.items() if k != "equity_curve_with_tm"}, indent=1, default=str))
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
