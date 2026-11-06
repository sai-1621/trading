"""Backtest the bot's opening-range rule on the last 60 days of 5-minute bars, after IBKR's charges.

    python backtest.py               the WATCH_SIZE most traded S&P 500 stocks
    python backtest.py --stocks 60
    python backtest.py --refresh     re-download (otherwise a 12-hour cache)

Settings are picked on the first 2/3 of the sessions and judged on the last 1/3, which played no part in choosing.
"""
import argparse
import itertools
import json
import sys
import time
from datetime import datetime

import config
import data
from market import now
from skills import write_json, rule, scanner
from skills.timemachine import full_sessions

STOPS, TARGETS, TRENDS = ["or-mid", "or-low", "atr:1.5"], [1.0, 1.5, 2.0], [False, True]


def _load(sym, refresh):
    f = config.CACHE_DIR / f"bt_{sym}.json"
    if f.exists() and not refresh and time.time() - f.stat().st_mtime < 12 * 3600:
        raw = json.loads(f.read_text())
    else:
        raw = {"m5": data.yahoo_bars(sym, "5m", "60d"), "d1": data.yahoo_bars(sym, "1d", "1y")}
        f.write_text(json.dumps(raw, default=lambda o: o.isoformat()))
        time.sleep(0.2)
    fix = lambda bars: [{**b, "t": datetime.fromisoformat(b["t"]) if isinstance(b["t"], str) else b["t"]} for b in bars]
    return fix(raw["m5"]), fix(raw["d1"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=config.WATCH_SIZE)
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    hits = scanner.scan(write=False, size=a.stocks)["hits"]
    today = now().date()
    sets = []
    for i, h in enumerate(hits, 1):
        sys.stdout.write(f"\r  downloading {i}/{len(hits)} {h['symbol']:<8}")
        sys.stdout.flush()
        try:
            m5, d1 = _load(h["symbol"], a.refresh)
        except Exception as e:
            print(f"\n  {h['symbol']} skipped: {e}")
            continue
        full = full_sessions(m5, today)
        if len(full) >= 10:
            sets.append((h["symbol"], full, rule.trend_map(d1)))
    print(f"\r  {len(sets)} stocks with enough full 5-minute sessions" + " " * 30)
    if not sets:
        return
    days = sorted({d for _, f, _ in sets for d in f})
    cut = days[int(len(days) * 2 / 3)]
    rows = []
    for stop, target, trend in itertools.product(STOPS, TARGETS, TRENDS):
        p = rule.live_params(stop_rule=stop, target_r=target, trend_filter=trend)
        pick, check = [], []
        for sym, full, trends in sets:
            for d, bars in full.items():
                (pick if d < cut else check).append(rule.simulate_day(bars, p, None, trends.get(d)))
        rows.append({"stop": stop, "target_r": target, "trend_filter": trend,
                     "pick": rule.summarize(pick), "check": rule.summarize(check)})
    ranked = sorted([r for r in rows if r["pick"]["trades"] >= 20], key=lambda r: r["pick"]["avg_r_net"], reverse=True)
    n_check = len([d for d in days if d >= cut])
    print(f"  pick period: {len(days) - n_check} sessions | check period: {n_check} sessions | charges: IBKR Pro Fixed\n")
    print(f"  {'stop':<8} {'target':>6} {'trend':>6} | {'PICK trades':>11} {'win%':>5} {'avg R':>6} {'R net':>6} | "
          f"{'CHECK trades':>12} {'win%':>5} {'avg R':>6} {'R net':>6}")
    f = lambda v: "-" if v is None else v
    for r in ranked[:8]:
        pk, ck = r["pick"], r["check"]
        print(f"  {r['stop']:<8} {r['target_r']:>5}R {'on' if r['trend_filter'] else 'off':>6} | {pk['trades']:>11} "
              f"{f(pk['win_pct']):>5} {f(pk['avg_r']):>6} {f(pk['avg_r_net']):>6} | {ck['trades']:>12} {f(ck['win_pct']):>5} "
              f"{f(ck['avg_r']):>6} {f(ck['avg_r_net']):>6}")
    verdict = "no settings had enough trades"
    if ranked:
        b, ck = ranked[0], ranked[0]["check"]
        if ck["trades"] >= 10 and (ck["avg_r_net"] or 0) > 0:
            verdict = (f"EDGE HELD UP: STOP_RULE={b['stop']} TARGET_R={b['target_r']} TREND_FILTER="
                       f"{str(b['trend_filter']).lower()} made {ck['avg_r_net']}R per trade after charges on {ck['trades']} "
                       f"unseen trades. Promising, not proof: paper trade it.")
        else:
            verdict = (f"NO RELIABLE EDGE: the best settings on the pick period ({b['stop']}, {b['target_r']}R, trend "
                       f"{'on' if b['trend_filter'] else 'off'}) made {ck['avg_r_net']}R per trade after charges on the "
                       f"check period ({ck['trades']} trades). Do not trade this rule with real money.")
    print(f"\n  {verdict}")
    write_json("backtest.json", {"run_at": now().isoformat(), "stocks": len(sets), "verdict": verdict, "ranked": ranked})
    print("\nFull results: trader/backtest.json")


if __name__ == "__main__":
    main()
