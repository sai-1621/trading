"""Train on market conditions and test walk-forward (see regime_engine.py), for US stocks.
Benchmark: SPY (S&P 500). Daily strategies hold for days, so the per-side cost is the commission on a typical
order plus half the spread.

    python regime_research.py               the 100 most traded S&P 500 stocks, 5 years of daily bars
    python regime_research.py --stocks 200

Tax is not simulated (it depends on your country; for an Indian resident, US share gains are taxed in India).
"""
import argparse
import json
import sys
import time
from datetime import datetime

import config
import costs
import data
import regime_engine as R
from market import now
from skills import scanner, write_json


def load(sym, refresh=False):
    f = config.CACHE_DIR / f"d5y_{sym.replace('^', '_')}.json"
    if f.exists() and not refresh and time.time() - f.stat().st_mtime < 86400:
        bars = [{**b, "t": datetime.fromisoformat(b["t"])} for b in json.loads(f.read_text())]
    else:
        bars = data.yahoo_bars(sym, "1d", "5y")
        f.write_text(json.dumps(bars, default=lambda o: o.isoformat()))
        time.sleep(0.1)
    return [b for b in bars if b["t"].date() < now().date()]


def cols(bars):
    return {"d": [b["t"].date() for b in bars], "o": [b["o"] for b in bars], "h": [b["h"] for b in bars],
            "l": [b["l"] for b in bars], "c": [b["c"] for b in bars]}


def run(n, refresh=False, quiet=False):
    hits = scanner.scan(write=False, size=n)["hits"]
    bench = cols(load("SPY", refresh))
    stocks = {}
    for i, h in enumerate(hits, 1):
        if not quiet:
            sys.stdout.write(f"\r  loading {i}/{len(hits)} {h['symbol']:<8}")
            sys.stdout.flush()
        try:
            b = cols(load(h["symbol"], refresh))
        except Exception:
            continue
        if len(b["c"]) >= 300:
            stocks[h["symbol"]] = b
    # per side: commission on a typical $4,000 order + half the spread
    typical = config.MAX_ORDER
    cost_of = lambda s: costs.order_charges(typical, "buy", typical / stocks[s]["c"][-1]) / typical \
        + config.ASSUMED_SPREAD_PCT / 100 / 2
    return R.walk_forward(stocks, bench, cost_of, tax=0.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=100)
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    rep = run(a.stocks, a.refresh)
    ok, why = R.verdict(rep)
    print(f"\r=== US: {rep['markets']} S&P 500 stocks | IBKR commission + {config.ASSUMED_SPREAD_PCT}% spread | "
          f"out-of-sample from {rep['oos_start']} ===")
    print(f"  Portfolio (equal weight): bot {rep['portfolio_ret_pct']:+.1f}% (worst fall {rep['portfolio_dd_pct']}%) | "
          f"buy-and-hold {rep['bh_portfolio_ret_pct']:+.1f}% (worst fall {rep['bh_portfolio_dd_pct']}%)")
    print(f"  Per stock: average {rep['avg_ret_pct']:+.1f}%, median {rep['median_ret_pct']:+.1f}%, "
          f"{rep['markets_up_pct']}% made money | buy-and-hold average {rep['bh_avg_ret_pct']:+.1f}%")
    print("  By year (bot vs hold): " + ", ".join(f"{y} {v['bot_pct']:+.1f}% vs {v['hold_pct']:+.1f}%" for y, v in rep["by_year"].items()))
    print("  Current choices: " + ", ".join(f"{k} -> {v}" for k, v in rep["choices"][-1]["pick"].items()))
    print(f"  VERDICT: {'PASS' if ok else 'FAIL'}: {why}\n")
    write_json("regime.json", {"run_at": now().isoformat(), "pass": ok, **{k: v for k, v in rep.items() if k != "per_market"}})
    print("Full results: trader/regime.json")


if __name__ == "__main__":
    main()
