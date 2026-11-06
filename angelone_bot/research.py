"""Strategy research on NSE stocks: long-only swing strategies on daily bars, after delivery charges.

    python research.py                 the 30 most traded stocks of the universe
    python research.py --stocks 80
    python research.py --refresh       re-download (otherwise a 24-hour cache)

Method (same as the crypto bot's research):
  * Orders fill at the NEXT day's open (no peeking), paying half the spread plus DELIVERY_COST_PCT each way.
  * Stops are checked on each day's low; a gap below the stop fills at the open.
  * The ~5 years of history are split by date: settings are picked on the older 2/3 and judged on the newest
    1/3 ("check"), compared with buy-and-hold over the same days.
  * PASS on the check period: average return above zero AND above buy-and-hold, at least half the stocks up,
    at least 30 trades.
These hold positions for days or weeks (delivery / CNC), unlike the intraday bot. Tax is not included:
short-term gains on shares are taxed at 20%, and losses can be set off against gains in the same year.
Caveat: the stocks are today's index members (survivorship), which flatters every result a little.
"""
import argparse
import json
import sys
import time
from datetime import datetime

import config
import data
from market import now
from skills import scanner, write_json

DELIVERY_COST_PCT = 0.15     # per side: STT 0.1% + stamp, exchange, SEBI, DP charge, GST (approx.)
SPREAD = config.ASSUMED_SPREAD_PCT / 100


def sma(x, n):
    out, s = [None] * len(x), 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def rsi(x, n):
    out, g, l = [None] * len(x), 0.0, 0.0
    for i in range(1, len(x)):
        d = x[i] - x[i - 1]
        gi, li = max(d, 0), max(-d, 0)
        if i <= n:
            g, l = g + gi / n, l + li / n
        else:
            g, l = (g * (n - 1) + gi) / n, (l * (n - 1) + li) / n
        if i >= n:
            out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def atr(h, l, c, n=14):
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    return sma(tr, n)


def dip_buy(b, p, ctx):
    c = b["c"]
    trend, r2, ex = sma(c, 200), rsi(c, 2), sma(c, p["exit_ma"])
    return {"enter": [trend[i] is not None and r2[i] is not None and c[i] > trend[i] and r2[i] < p["rsi"] for i in range(len(c))],
            "exit": [ex[i] is not None and c[i] > ex[i] for i in range(len(c))],
            "atr": atr(b["h"], b["l"], c), "stop_k": 3.0, "max_bars": 15}


def ma_trend(b, p, ctx):
    c, m = b["c"], sma(b["c"], p["ma"])
    reg = ctx["nifty_regime"] if p.get("nifty_filter") else None
    up = [m[i] is not None and c[i] > m[i] and (reg is None or reg.get(b["d"][i], False)) for i in range(len(c))]
    return {"enter": up, "exit": [not u for u in up], "atr": None, "stop_k": None, "max_bars": None}


def donchian(b, p, ctx):
    c = b["c"]
    hi = [None if i < p["entry"] else max(c[i - p["entry"]:i]) for i in range(len(c))]
    lo = [None if i < p["exit"] else min(c[i - p["exit"]:i]) for i in range(len(c))]
    return {"enter": [hi[i] is not None and c[i] > hi[i] for i in range(len(c))],
            "exit": [lo[i] is not None and c[i] < lo[i] for i in range(len(c))],
            "atr": atr(b["h"], b["l"], c), "stop_k": 3.0, "max_bars": None}


STRATEGIES = {
    "dip_buy_rsi2": (dip_buy, [{"rsi": r, "exit_ma": e} for r in (5, 10) for e in (5, 10)]),
    "donchian": (donchian, [{"entry": e, "exit": x} for e in (20, 55) for x in (10, 20)]),
    "ma_trend": (ma_trend, [{"ma": m} for m in (50, 100, 200)]),
    "ma_trend_nifty": (ma_trend, [{"ma": m, "nifty_filter": True} for m in (50, 100, 200)]),
}


def simulate(b, sig, start, end, cost):
    o, l, c = b["o"], b["l"], b["c"]
    half = SPREAD / 2
    eq, units, stop, entry_i, pending, basis = 1.0, 0.0, None, None, None, 1.0
    peak, dd, trades = 1.0, 0.0, []
    for i in range(start, end):
        if pending == "buy" and units == 0:
            px = o[i] * (1 + half)
            units, basis, entry_i = eq * (1 - cost) / px, eq, i
            a = sig["atr"][i - 1] if sig["atr"] else None
            stop = px - sig["stop_k"] * a if sig["stop_k"] and a else None
        elif pending == "sell" and units > 0:
            eq = units * o[i] * (1 - half) * (1 - cost)
            trades.append(eq / basis - 1)
            units, stop = 0.0, None
        pending = None
        if units > 0 and stop is not None and l[i] <= stop:
            eq = units * min(o[i], stop) * (1 - half) * (1 - cost)
            trades.append(eq / basis - 1)
            units, stop = 0.0, None
        value = units * c[i] * (1 - half) * (1 - cost) if units > 0 else eq
        peak, dd = max(peak, value), max(dd, 1 - value / max(peak, value))
        if i == end - 1:
            break
        if units > 0:
            if sig["exit"][i] or (sig["max_bars"] and i - entry_i + 1 >= sig["max_bars"]):
                pending = "sell"
        elif sig["enter"][i]:
            pending = "buy"
    if units > 0:
        eq = units * c[end - 1] * (1 - half) * (1 - cost)
        trades.append(eq / basis - 1)
    return {"ret": eq - 1, "trades": trades, "max_dd": dd}


def buy_hold(b, s, e, cost):
    c = b["c"]
    peak, dd = c[s], 0.0
    for i in range(s, e):
        peak = max(peak, c[i])
        dd = max(dd, 1 - c[i] / peak)
    return c[e - 1] * (1 - SPREAD / 2) * (1 - cost) / (b["o"][s] * (1 + SPREAD / 2) / (1 - cost)) - 1, dd


def summarize(rows):
    if not rows:
        return None
    n, trades = len(rows), [t for r in rows for t in r["trades"]]
    rets = sorted(r["ret"] for r in rows)
    return {"stocks": n, "avg_ret_pct": round(sum(rets) / n * 100, 1), "median_ret_pct": round(rets[n // 2] * 100, 1),
            "stocks_up_pct": round(sum(r > 0 for r in rets) / n * 100), "bh_avg_ret_pct": round(sum(r["bh"] for r in rows) / n * 100, 1),
            "trades": len(trades), "win_pct": round(sum(t > 0 for t in trades) / len(trades) * 100, 1) if trades else None,
            "avg_max_dd_pct": round(sum(r["max_dd"] for r in rows) / n * 100, 1),
            "bh_avg_max_dd_pct": round(sum(r["bh_dd"] for r in rows) / n * 100, 1)}


def cols(bars):
    return {"d": [b["t"].date() for b in bars], "o": [b["o"] for b in bars], "h": [b["h"] for b in bars],
            "l": [b["l"] for b in bars], "c": [b["c"] for b in bars]}


def load(sym, refresh):
    f = config.CACHE_DIR / f"rs_{sym.replace('^', '_')}.json"
    if f.exists() and not refresh and time.time() - f.stat().st_mtime < 86400:
        bars = [{**b, "t": datetime.fromisoformat(b["t"])} for b in json.loads(f.read_text())]
    else:
        bars = data.yahoo_bars(sym, "1d", "5y")
        f.write_text(json.dumps(bars, default=lambda o: o.isoformat()))
        time.sleep(0.2)
    return [b for b in bars if b["t"].date() < now().date()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=30)
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    old = config.WATCH_SIZE
    config.WATCH_SIZE = a.stocks
    hits = scanner.scan(write=False)["hits"]
    config.WATCH_SIZE = old
    cost = DELIVERY_COST_PCT / 100
    stocks = {}
    for i, h in enumerate(hits, 1):
        sys.stdout.write(f"\r  downloading {i}/{len(hits)} {h['symbol']:<14}")
        sys.stdout.flush()
        try:
            stocks[h["symbol"]] = cols(load(h["symbol"], a.refresh))
        except Exception as e:
            print(f"\n  {h['symbol']} skipped: {e}")
    nifty = cols(load("^NSEI", a.refresh))
    nm = sma(nifty["c"], 100)
    regime = {}
    for i in range(len(nifty["d"]) - 1):
        if nm[i] is not None:
            regime[nifty["d"][i + 1]] = nifty["c"][i] > nm[i]   # known at the next morning's open
    ctx = {"nifty_regime": regime}
    all_days = sorted({d for s in stocks.values() for d in s["d"]})
    cut_day = all_days[int(len(all_days) * 2 / 3)]
    print(f"\r  {len(stocks)} stocks | pick {all_days[0]} to {cut_day} | check {cut_day} to {all_days[-1]} | "
          f"costs {DELIVERY_COST_PCT}%/side + {config.ASSUMED_SPREAD_PCT}% spread\n")
    print(f"  {'strategy':<15} {'best settings (picked on older data)':<28} | {'CHECK avg':>9} {'median':>7} {'B&H':>7} "
          f"{'up':>4} {'trades':>6} {'win%':>5} {'DD':>5} {'B&H DD':>6} | verdict")
    report = {"run_at": now().isoformat(), "pick_to": str(cut_day), "strategies": {}}
    for name, (fn, grid) in STRATEGIES.items():
        results = []
        for p in grid:
            pick, check = [], []
            for sym, b in stocks.items():
                n = len(b["c"])
                warm = max(p.get("ma", 0), p.get("entry", 0), 200 if fn is dip_buy else 0, 30) + 5
                cut = next((i for i, d in enumerate(b["d"]) if d >= cut_day), n)
                if cut < warm + 60 or n - cut < 20:
                    continue
                sig = fn(b, p, ctx)
                for (s, e), rows in (((warm, cut), pick), ((cut, n), check)):
                    r = simulate(b, sig, s, e, cost)
                    r["bh"], r["bh_dd"] = buy_hold(b, s, e, cost)
                    r["stock"] = sym
                    rows.append(r)
            results.append({"params": p, "pick": summarize(pick), "check": summarize(check),
                            "check_by_stock": {r["stock"]: round(r["ret"] * 100, 1) for r in check}})
        results = [r for r in results if r["pick"] and r["check"]]
        if not results:
            continue
        best = max(results, key=lambda r: r["pick"]["avg_ret_pct"])
        ck = best["check"]
        passed = ck["avg_ret_pct"] > 0 and ck["avg_ret_pct"] > ck["bh_avg_ret_pct"] and ck["stocks_up_pct"] >= 50 and ck["trades"] >= 30
        ps = ",".join(f"{k}={v}" for k, v in best["params"].items())
        print(f"  {name:<15} {ps:<28} | {ck['avg_ret_pct']:>8}% {ck['median_ret_pct']:>6}% {ck['bh_avg_ret_pct']:>6}% "
              f"{ck['stocks_up_pct']:>3}% {ck['trades']:>6} {ck['win_pct'] if ck['win_pct'] is not None else '-':>5} "
              f"{ck['avg_max_dd_pct']:>4}% {ck['bh_avg_max_dd_pct']:>5}% | {'PASS' if passed else 'fail'}")
        report["strategies"][name] = {"best": best, "passed": passed, "all": results}
    write_json("research.json", report)
    print("\nPASS = on the unseen check period: average return above zero and above buy-and-hold, at least half the "
          "stocks up, 30+ trades.\nFull results: trader/research.json")


if __name__ == "__main__":
    main()
