"""Buy-the-dip swing test: buy a strong stock after a short pullback, sell on the bounce. Daily bars, after charges.

    ..\\angelone_bot\\.venv\\Scripts\\python dips.py --market nse
    ..\\ibkr_us_bot\\.venv\\Scripts\\python  dips.py --market us

Rule (decided at the close, traded at the NEXT open, so no peeking):
  buy  : close above its 200-day average (uptrend) and 2-day RSI below a threshold (a sharp short dip)
  sell : at the next open after the close rises above the 5-day average (or the 2-day RSI goes above 70),
         or after 10 days at most; optional stop-loss.
Same 99 stocks as the stock long/short test (the bots' scanner list), 10 years of Yahoo daily bars.
Settings are chosen on the first 2/3 of the years (pick) and judged on the last 1/3 (check), which played no part
in choosing them. Baselines on the check years: buying any uptrend day and holding the same number of days, and
simply holding the stocks / the index.
Charges: NSE delivery (STT 0.1% both sides, stamp 0.015%, DP charge), IBKR fixed for the US, plus half the spread
each way. Before tax: in India short-term gains on shares are taxed at 20%.
Caveat: today's most traded stocks are tested backwards, so stocks that later fell out of favour are missing
(survivorship bias). This flatters every long rule, which is why the baselines on the same stocks matter.
"""
import argparse
import itertools
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BOTS = {"nse": "angelone_bot", "us": "ibkr_us_bot"}
INDEX = {"nse": "^NSEI", "us": "^GSPC"}
SLOT = {"nse": 40000.0, "us": 2250.0}       # money per position (bots' position limits)
SLOTS = 2                                   # positions at the same time in the portfolio run
MAX_HOLD = 10
GRID = [dict(rsi=r, exit=x, stop=s, idx=i) for r, x, s, i in
        itertools.product((5, 10, 20), ("sma5", "rsi70"), (None, 0.08), (False, True))]
CACHE = HERE / "cache" / "daily"


def load_bot(mk):
    sys.path.insert(0, str(ROOT / BOTS[mk]))
    import config, costs, data
    return config, costs, data


def charges_fn(mk, config, costs):
    sf = config.ASSUMED_SPREAD_PCT / 100
    if mk == "nse":
        def order(value, side):
            brk = max(5.0, min(20.0, value * 0.001))
            txn, sebi = value * 0.0000297, value * 0.000001
            stt = value * 0.001
            stamp = value * 0.00015 if side == "buy" else 0.0
            dp = 13.5 * 1.18 if side == "sell" else 0.0
            return brk + txn + sebi + stt + stamp + (brk + txn + sebi) * 0.18 + dp
        return sf, lambda ev, xv, n: order(ev, "buy") + order(xv, "sell")
    return sf, lambda ev, xv, n: costs.round_trip(ev, xv, n)


def daily(mk, data, sym):
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{mk}_{sym.replace('^', '_')}.json"
    if f.exists() and time.time() - f.stat().st_mtime < 24 * 3600:
        return json.loads(f.read_text())
    bars = [{"d": b["t"].date().isoformat(), "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]}
            for b in data.yahoo_bars(sym, "1d", "10y")]
    today = time.strftime("%Y-%m-%d")
    bars = [b for b in bars if b["d"] < today]                  # closed days only
    f.write_text(json.dumps(bars))
    time.sleep(0.2)
    return bars


def sma(c, n):
    out, s = [None] * len(c), 0.0
    for i, x in enumerate(c):
        s += x
        if i >= n:
            s -= c[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def rsi2(c):
    out, ag, al = [None] * len(c), None, None
    for i in range(1, len(c)):
        ch = c[i] - c[i - 1]
        g, l = max(ch, 0), max(-ch, 0)
        if i <= 2:
            ag = (ag or 0) + g / 2
            al = (al or 0) + l / 2
            if i < 2:
                continue
        else:
            ag = (ag * 1 + g) / 2
            al = (al * 1 + l) / 2
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def prep(bars):
    c = [b["c"] for b in bars]
    return {"bars": bars, "sma200": sma(c, 200), "sma5": sma(c, 5), "rsi": rsi2(c)}


def trades_for(sym, s, p, idx_up, sf, cost, slot):
    bars, out, i, n = s["bars"], [], 200, len(s["bars"])
    while i < n - 1:
        b, r = bars[i], s["rsi"][i]
        if r is None or s["sma200"][i] is None or b["c"] <= s["sma200"][i] or r >= p["rsi"] \
                or (p["idx"] and not idx_up.get(b["d"], False)):
            i += 1
            continue
        e = bars[i + 1]["o"] * (1 + sf / 2)
        stop = e * (1 - p["stop"]) if p["stop"] else None
        x_px, x_i, why = None, None, None
        for j in range(i + 1, n):
            bj = bars[j]
            if stop and bj["l"] <= stop:
                x_px, x_i, why = min(bj["o"], stop) if j > i + 1 else stop, j, "stop"
                break
            done = (bj["c"] > s["sma5"][j]) if p["exit"] == "sma5" else (s["rsi"][j] > 70)
            if done or j - i >= MAX_HOLD:
                if j + 1 >= n:
                    break
                x_px, x_i, why = bars[j + 1]["o"], j + 1, "bounce" if done else "time"
                break
        if x_px is None:
            break                                               # still open at the end of the data
        x_px *= (1 - sf / 2)
        qty = slot / e
        net = (x_px - e) * qty - cost(slot, qty * x_px, qty)
        out.append({"sym": sym, "in": bars[i + 1]["d"], "out": bars[x_i]["d"], "rsi": r, "days": x_i - i - 1,
                    "ret": net / slot, "why": why})
        i = x_i
    return out


def anyday(sym, s, hold, sf, cost, slot, start):
    """Buy at the open after any uptrend close, sell at the open `hold` days later."""
    bars, out = s["bars"], []
    for i in range(200, len(bars) - hold - 1):
        if bars[i + 1]["d"] < start or s["sma200"][i] is None or bars[i]["c"] <= s["sma200"][i]:
            continue
        e, x = bars[i + 1]["o"] * (1 + sf / 2), bars[i + 1 + hold]["o"] * (1 - sf / 2)
        q = slot / e
        out.append(((x - e) * q - cost(slot, q * x, q)) / slot)
    return out


def stats(ts):
    if not ts:
        return {"trades": 0}
    r = [t["ret"] for t in ts]
    by_y = defaultdict(list)
    for t in ts:
        by_y[t["in"][:4]].append(t["ret"])
    return {"trades": len(ts), "win_pct": round(100 * sum(x > 0 for x in r) / len(r), 1),
            "avg_pct": round(100 * statistics.mean(r), 3), "avg_win_pct": round(100 * statistics.mean([x for x in r if x > 0] or [0]), 2),
            "avg_loss_pct": round(100 * statistics.mean([x for x in r if x <= 0] or [0]), 2),
            "avg_days": round(statistics.mean(t["days"] for t in ts), 1),
            "by_year": {y: round(100 * statistics.mean(v), 2) for y, v in sorted(by_y.items())}}


def portfolio(ts, slot, start, end, slots=SLOTS):
    """At most SLOTS positions; on a crowded day the deepest dips (lowest RSI) go first. Fixed money per trade."""
    ts = sorted((t for t in ts if start <= t["in"] < end), key=lambda t: (t["in"], t["rsi"]))
    held, taken = [], []
    for t in ts:
        held = [h for h in held if h["out"] > t["in"]]
        if len(held) < slots and all(h["sym"] != t["sym"] for h in held):
            held.append(t)
            taken.append(t)
    cap = slot * slots
    eq, peak, dd, by_y = 0.0, 0.0, 0.0, defaultdict(float)
    for t in sorted(taken, key=lambda t: t["out"]):
        eq += t["ret"] * slot
        by_y[t["out"][:4]] += t["ret"] * slot
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return {"trades": len(taken), "win_pct": round(100 * sum(t["ret"] > 0 for t in taken) / max(1, len(taken)), 1),
            "profit": round(eq), "profit_pct": round(100 * eq / cap, 1), "worst_drop_pct": round(100 * dd / cap, 1),
            "by_year_pct": {y: round(100 * v / cap, 1) for y, v in sorted(by_y.items())}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True, choices=list(BOTS))
    a = ap.parse_args()
    mk = a.market
    config, costs, data = load_bot(mk)
    sf, cost = charges_fn(mk, config, costs)
    slot = SLOT[mk]
    syms = sorted({c["sym"] for c in json.loads((HERE / "results" / f"candidates_{mk}.json").read_text())["candidates"]})
    idx = prep(daily(mk, data, INDEX[mk]))
    idx_up = {b["d"]: (idx["sma200"][i] is not None and b["c"] > idx["sma200"][i]) for i, b in enumerate(idx["bars"])}
    stocks = {}
    for k, sym in enumerate(syms, 1):
        sys.stdout.write(f"\r  loading {k}/{len(syms)} {sym:<12}")
        sys.stdout.flush()
        try:
            bars = daily(mk, data, sym)
        except Exception as ex:
            print(f"\n  {sym} skipped: {ex}")
            continue
        if len(bars) > 260:
            stocks[sym] = prep(bars)
    days = [b["d"] for b in idx["bars"]][200:]
    split, end = days[len(days) * 2 // 3], "9999"
    print(f"\r  {len(stocks)} stocks, trading days {days[0]} .. {days[-1]}, pick before {split}, check from {split}")

    rows = []
    for p in GRID:
        ts = [t for sym, s in stocks.items() for t in trades_for(sym, s, p, idx_up, sf, cost, slot)]
        pick = [t for t in ts if t["in"] < split]
        check = [t for t in ts if t["in"] >= split]
        rows.append({"p": p, "pick": stats(pick), "check": stats(check), "all": ts})
    ok = [r for r in rows if r["pick"]["trades"] >= 100]
    best = max(ok, key=lambda r: r["pick"]["avg_pct"])

    def lbl(p):
        return f"RSI<{p['rsi']:<2} exit {p['exit']:<5} stop {('-' + str(int(p['stop'] * 100)) + '%') if p['stop'] else 'none':<5} {'index up' if p['idx'] else 'any mkt'}"

    print(f"\n{'setting':44} | {'PICK trades':>11} {'won%':>5} {'avg%':>6} | {'CHECK trades':>12} {'won%':>5} {'avg%':>6} {'days':>4}")
    for r in sorted(rows, key=lambda r: -r["pick"].get("avg_pct", -99)):
        P, C = r["pick"], r["check"]
        mark = "  <- chosen" if r is best else ""
        print(f"{lbl(r['p']):44} | {P['trades']:>11} {P.get('win_pct', 0):>5} {P.get('avg_pct', 0):>+6.2f} | "
              f"{C['trades']:>12} {C.get('win_pct', 0):>5} {C.get('avg_pct', 0):>+6.2f} {C.get('avg_days', 0):>4}{mark}")

    C = best["check"]
    hold = max(1, round(C["avg_days"]))
    base = [x for s in stocks.values() for x in anyday(None, s, hold, sf, cost, slot, split)]
    base_avg = 100 * statistics.mean(base)
    base_win = 100 * sum(x > 0 for x in base) / len(base)
    pf = portfolio(best["all"], slot, split, end)
    ib = [b for b in idx["bars"] if b["d"] >= split]
    idx_ret = 100 * (ib[-1]["c"] / ib[0]["c"] - 1)
    bh = [s["bars"] for s in stocks.values()]
    bh_ret = 100 * statistics.mean(b[-1]["c"] / next(x for x in b if x["d"] >= split)["c"] - 1 for b in bh if b[-1]["d"] > split)
    years = (len(ib) / 250)
    print(f"\nChosen on pick years: {lbl(best['p'])}")
    print(f"CHECK years ({split} .. {days[-1]}, unseen):")
    print(f"  dip rule      : {C['trades']} trades, {C['win_pct']}% won, avg {C['avg_pct']:+.2f}% per trade after charges "
          f"(wins avg {C['avg_win_pct']:+.2f}%, losses avg {C['avg_loss_pct']:+.2f}%), held {C['avg_days']} days")
    print(f"  by year       : " + "  ".join(f"{y}: {v:+.2f}%" for y, v in C["by_year"].items()))
    print(f"  any uptrend day, hold {hold}d (no dip needed): {len(base)} trades, {base_win:.1f}% won, avg {base_avg:+.2f}%")
    print(f"  portfolio ({SLOTS} positions x {slot:,.0f}): {pf['trades']} trades, {pf['win_pct']}% won, "
          f"profit {pf['profit']:,} = {pf['profit_pct']:+.1f}% of {slot * SLOTS:,.0f} over {years:.1f} years, "
          f"worst drop {pf['worst_drop_pct']}%  {pf['by_year_pct']}")
    print(f"  holding the index: {idx_ret:+.1f}%   holding all {len(bh)} stocks: {bh_ret:+.1f}%")
    spread_runs = {}
    for k in (2, 4, 6, 10):
        sl = slot * SLOTS / k
        ts = [t for sym, st in stocks.items() for t in trades_for(sym, st, best["p"], idx_up, sf, cost, sl)]
        spread_runs[k] = portfolio(ts, sl, split, end, k)
        q = spread_runs[k]
        print(f"  same money in {k:>2} positions of {sl:>8,.0f}: {q['trades']} trades, {q['win_pct']}% won, "
              f"{q['profit_pct']:+.1f}%, worst drop {q['worst_drop_pct']}%  {q['by_year_pct']}")
    rep = {"market": mk, "stocks": len(stocks), "days": [days[0], days[-1]], "split": split,
           "grid": [{"setting": lbl(r["p"]), "pick": r["pick"], "check": r["check"]} for r in rows],
           "chosen": lbl(best["p"]), "check": C, "anyday": {"hold": hold, "trades": len(base), "win_pct": round(base_win, 1),
                                                           "avg_pct": round(base_avg, 3)},
           "portfolio": pf, "spread_runs": spread_runs, "index_pct": round(idx_ret, 1), "hold_stocks_pct": round(bh_ret, 1)}
    (HERE / "results" / f"dips_{mk}.json").write_text(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
