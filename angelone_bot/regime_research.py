"""Train the NSE bot on market conditions and test it walk-forward (see regime_engine.py).
Benchmark: NIFTY 50. Positions are held for days (delivery), so delivery costs apply.

    python regime_research.py               the 100 NIFTY 100 stocks
    python regime_research.py --stocks 50

Tax is not simulated: short-term gains on shares are taxed at 20% a year on NET gains (losses offset).
"""
import argparse
import sys
from datetime import datetime

import config
import regime_engine as R
from market import now
from research import load, DELIVERY_COST_PCT
from skills import scanner, write_json


def cols(bars):
    return {"d": [b["t"].date() for b in bars], "o": [b["o"] for b in bars], "h": [b["h"] for b in bars],
            "l": [b["l"] for b in bars], "c": [b["c"] for b in bars]}


def run(n, refresh=False, quiet=False):
    old = config.WATCH_SIZE
    config.WATCH_SIZE = n
    hits = scanner.scan(write=False)["hits"]
    config.WATCH_SIZE = old
    bench = cols(load("^NSEI", refresh))
    stocks = {}
    for i, h in enumerate(hits, 1):
        if not quiet:
            sys.stdout.write(f"\r  loading {i}/{len(hits)} {h['symbol']:<14}")
            sys.stdout.flush()
        try:
            b = cols(load(h["symbol"], refresh))
        except Exception:
            continue
        if len(b["c"]) >= 300:
            stocks[h["symbol"]] = b
    cost = DELIVERY_COST_PCT / 100 + config.ASSUMED_SPREAD_PCT / 100 / 2
    return R.walk_forward(stocks, bench, lambda s: cost, tax=0.0), cost


def show(rep, cost):
    ok, why = R.verdict(rep)
    print(f"\r=== NSE: {rep['markets']} stocks | costs {cost * 100:.3f}%/side | out-of-sample from {rep['oos_start']} ===")
    print(f"  Portfolio (equal weight): bot {rep['portfolio_ret_pct']:+.1f}% (worst fall {rep['portfolio_dd_pct']}%) | "
          f"buy-and-hold {rep['bh_portfolio_ret_pct']:+.1f}% (worst fall {rep['bh_portfolio_dd_pct']}%)")
    print(f"  Per stock: average {rep['avg_ret_pct']:+.1f}%, median {rep['median_ret_pct']:+.1f}%, "
          f"{rep['markets_up_pct']}% made money | buy-and-hold average {rep['bh_avg_ret_pct']:+.1f}%")
    print("  By year (bot vs hold): " + ", ".join(f"{y} {v['bot_pct']:+.1f}% vs {v['hold_pct']:+.1f}%" for y, v in rep["by_year"].items()))
    print("  Current choices: " + ", ".join(f"{k} -> {v}" for k, v in rep["choices"][-1]["pick"].items()))
    print(f"  VERDICT: {'PASS' if ok else 'FAIL'}: {why}\n")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=100)
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    rep, cost = run(a.stocks, a.refresh)
    ok = show(rep, cost)
    write_json("regime.json", {"run_at": now().isoformat(), "pass": ok, **{k: v for k, v in rep.items() if k != "per_market"}})
    print("Full results: trader/regime.json")


if __name__ == "__main__":
    main()
