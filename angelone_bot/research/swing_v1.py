"""Swing (delivery) v1, exactly as pre-registered in research/PREREG_swing_v1.md.

    .venv\\Scripts\\python research\\fetch_daily.py     (once: Angel One daily bars into cache/)
    .venv\\Scripts\\python research\\swing_v1.py

Writes research/swing_v1_results.json. Holdout numbers are computed only for the chosen variant.
"""
import itertools
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

PICK = (date(2022, 3, 1), date(2024, 12, 31))
HOLD = (date(2025, 1, 1), date(2026, 10, 30))
SPREAD = 0.0005
SIZE = 20000.0
CAPITAL, SLOTS = 100000.0, 5
MIN_T, MIN_TRADES, HOLD_T = 3.0, 200, 2.0
M_GRID = {"family": ["M"], "n": [20, 50], "exit": ["atr3", "low10", "low20"], "stock": ["none", "ma200"],
          "market": ["none", "nifty200"]}
R_GRID = {"family": ["R"], "x": [5, 10], "f": [200, 50], "exit": ["ma5", "rsi70"], "stop": ["none", "atr3"],
          "market": ["none", "nifty200"]}


def delivery_charges(value, side):
    brokerage = min(20.0, value * 0.001)
    fees = value * (0.00297 + 0.0001) / 100
    stt = value * 0.001
    stamp = value * 0.00015 if side == "buy" else 0.0
    dp = 15.93 if side == "sell" else 0.0
    return brokerage + fees + stt + stamp + dp + 0.18 * (brokerage + fees)


def sma(xs, n, i):
    return sum(xs[i - n + 1:i + 1]) / n if i >= n - 1 else None


def indicators(bars):
    c = [b["c"] for b in bars]
    n = len(bars)
    tr = [bars[0]["h"] - bars[0]["l"]] + [max(bars[i]["h"] - bars[i]["l"], abs(bars[i]["h"] - c[i - 1]),
                                              abs(bars[i]["l"] - c[i - 1])) for i in range(1, n)]
    # 2-day RSI (Wilder)
    rsi, ag, al = [None] * n, None, None
    for i in range(1, n):
        g, l = max(0.0, c[i] - c[i - 1]), max(0.0, c[i - 1] - c[i])
        if i == 2:
            ag = (max(0.0, c[1] - c[0]) + g) / 2
            al = (max(0.0, c[0] - c[1]) + l) / 2
        elif i > 2:
            ag, al = (ag + g) / 2, (al + l) / 2
        if ag is not None:
            rsi[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return {"t": [b["t"] for b in bars], "o": [b["o"] for b in bars], "h": [b["h"] for b in bars],
            "l": [b["l"] for b in bars], "c": c, "rsi": rsi,
            "atr": [sum(tr[i - 13:i + 1]) / 14 if i >= 13 else None for i in range(n)],
            "ma5": [sma(c, 5, i) for i in range(n)], "ma50": [sma(c, 50, i) for i in range(n)],
            "ma200": [sma(c, 200, i) for i in range(n)]}


def load():
    stocks = {}
    for f in sorted(config.CACHE_DIR.glob("angel_d1_*_2000.json")):
        sym = f.name[len("angel_d1_"):-len("_2000.json")]
        bars = json.loads(f.read_text())
        for b in bars:
            b["t"] = datetime.fromisoformat(b["t"]).date()
        bars = [b for b in bars if b["t"] <= HOLD[1]]
        if sym == "_NSEI":
            nifty = indicators(bars)
            continue
        if len(bars) > 210:
            stocks[sym] = indicators(bars)
    nmap = {nifty["t"][i]: nifty["c"][i] > nifty["ma200"][i] for i in range(len(nifty["t"])) if nifty["ma200"][i]}
    return stocks, nmap, nifty


def signal(s, i, v, nmap):
    c = s["c"]
    if s["ma200"][i] is None or s["atr"][i] is None:
        return False
    if v["market"] == "nifty200" and nmap.get(s["t"][i]) is not True:
        return False
    if v["family"] == "M":
        n = v["n"]
        if i < n or c[i] <= max(c[i - n:i]):
            return False
        return v["stock"] == "none" or c[i] > s["ma200"][i]
    ma = s["ma200"][i] if v["f"] == 200 else s["ma50"][i]
    return ma is not None and c[i] > ma and s["rsi"][i] is not None and s["rsi"][i] < v["x"]


def trade(s, i, v):
    """Signal on day i's close. Returns the trade or None if there is no next day."""
    o, h, l, c = s["o"], s["h"], s["l"], s["c"]
    n = len(c)
    if i + 1 >= n:
        return None
    ent = i + 1
    e = o[ent] * (1 + SPREAD / 2)
    stop = e - 3 * s["atr"][i] if (v["family"] == "R" and v["stop"] == "atr3") else None
    top = c[ent]
    exit_day, px, why = None, None, None
    for k in range(ent, n):
        if stop is not None and k > ent and l[k] <= stop:
            exit_day, px, why = k, min(o[k], stop), "stop"
            break
        top = max(top, c[k])
        sell = False
        if v["family"] == "M":
            if v["exit"] == "atr3":
                sell = c[k] < top - 3 * s["atr"][k]
            else:
                m = 10 if v["exit"] == "low10" else 20
                sell = c[k] < min(c[k - m:k])
        else:
            if v["exit"] == "ma5":
                sell = c[k] > s["ma5"][k]
            else:
                sell = s["rsi"][k] is not None and s["rsi"][k] > 70
            sell = sell or (k - ent + 1) >= 10
        if sell:
            if k + 1 < n:
                exit_day, px, why = k + 1, o[k + 1], "signal"
            else:
                exit_day, px, why = k, c[k], "end"
            break
    if exit_day is None:
        exit_day, px, why = n - 1, c[n - 1], "end"
    px *= (1 - SPREAD / 2)
    shares = SIZE / e
    cost = delivery_charges(SIZE, "buy") + delivery_charges(shares * px, "sell")
    ret = (shares * px - SIZE - cost) / SIZE
    return {"sig_day": s["t"][i], "entry": ent, "exit": exit_day, "e": e, "px": px, "ret": ret,
            "gross": px / e - 1, "why": why, "days": exit_day - ent}


def per_trade(stocks, nmap, v, period):
    rows = []
    for sym, s in stocks.items():
        i, n = 0, len(s["c"])
        while i < n:
            d = s["t"][i]
            if d > period[1]:
                break
            if d >= period[0] and signal(s, i, v, nmap):
                t = trade(s, i, v)
                if t is None:
                    break
                rows.append({**t, "symbol": sym})
                i = t["exit"]          # one position per stock: look again from the exit day
                continue
            i += 1
    return rows


def stats(rows):
    n = len(rows)
    if n < 2:
        return {"trades": n}
    r = [x["ret"] for x in rows]
    m = sum(r) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in r) / (n - 1))
    return {"trades": n, "win_pct": round(sum(x > 0 for x in r) / n * 100, 1), "avg_ret_pct": round(m * 100, 3),
            "avg_gross_pct": round(sum(x["gross"] for x in rows) / n * 100, 3),
            "t": round(m / sd * math.sqrt(n), 2) if sd else None,
            "avg_days": round(sum(x["days"] for x in rows) / n, 1),
            "stops": sum(x["why"] == "stop" for x in rows)}


def portfolio(stocks, nmap, nifty, v, period):
    """Rs 1,00,000, 5 slots of Rs 20,000, ranked signals, daily mark-to-market."""
    dates = [d for d in nifty["t"] if period[0] <= d <= period[1]]
    idx = {sym: {d: i for i, d in enumerate(s["t"])} for sym, s in stocks.items()}
    cash, held, curve, n_trades, wins = CAPITAL, {}, [], 0, 0
    for d in dates:
        # exits whose sell happens at today's open
        for sym in list(held):
            p = held[sym]
            if stocks[sym]["t"][p["exit"]] == d:
                proceeds = p["shares"] * p["px"]
                cash += proceeds - delivery_charges(proceeds, "sell")
                wins += p["ret"] > 0
                n_trades += 1
                del held[sym]
        # entries from yesterday's signals happen at today's open: collected at yesterday's close below
        for sym, p in list(held.items()):
            if p.get("pending"):
                p.pop("pending")
        # mark to market at today's close
        value = cash + sum(p["shares"] * stocks[sym]["c"][idx[sym][d]] for sym, p in held.items() if d in idx[sym])
        curve.append((d, value))
        # today's signals -> buy at the next open
        cands = []
        for sym, s in stocks.items():
            i = idx[sym].get(d)
            if i is None or sym in held or not signal(s, i, v, nmap):
                continue
            key = (s["c"][i] / s["c"][i - 20] - 1) if v["family"] == "M" else -s["rsi"][i]
            cands.append((key, sym, i))
        cands.sort(reverse=True)
        for _, sym, i in cands:
            if len(held) >= SLOTS:
                break
            t = trade(stocks[sym], i, v)
            if t is None or t["e"] > SIZE or cash < SIZE:
                continue
            shares = math.floor(SIZE / t["e"])
            spend = shares * t["e"]
            cash -= spend + delivery_charges(spend, "buy")
            held[sym] = {**t, "shares": shares, "pending": True}
    final = curve[-1][1] if curve else CAPITAL
    peak, dd = CAPITAL, 0.0
    for _, val in curve:
        peak = max(peak, val)
        dd = min(dd, val / peak - 1)
    ni = [i for i, d in enumerate(nifty["t"]) if period[0] <= d <= period[1]]
    years = (dates[-1] - dates[0]).days / 365.25
    return {"final_rupees": round(final), "total_return_pct": round((final / CAPITAL - 1) * 100, 1),
            "cagr_pct": round(((final / CAPITAL) ** (1 / years) - 1) * 100, 1), "max_drawdown_pct": round(dd * 100, 1),
            "closed_trades": n_trades, "closed_win_pct": round(wins / n_trades * 100, 1) if n_trades else None,
            "nifty_buy_hold_pct": round((nifty["c"][ni[-1]] / nifty["c"][ni[0]] - 1) * 100, 1),
            "curve_monthly": [(str(d), round(val)) for d, val in curve if d.day <= 7 and d.weekday() == 0][::4]}


def main():
    stocks, nmap, nifty = load()
    print(f"{len(stocks)} stocks")
    variants = [dict(zip(M_GRID, c)) for c in itertools.product(*M_GRID.values())]
    variants += [dict(zip(R_GRID, c)) for c in itertools.product(*R_GRID.values())]
    pick = [{**v, **stats(per_trade(stocks, nmap, v, PICK))} for v in variants]
    eligible = [x for x in pick if x.get("trades", 0) >= MIN_TRADES and (x.get("avg_ret_pct") or 0) > 0
                and (x.get("t") or 0) >= MIN_T]
    eligible.sort(key=lambda x: (x["win_pct"], x["t"]), reverse=True)
    rep = {"run_at": datetime.now().isoformat(), "stocks": len(stocks), "pick": [str(d) for d in PICK],
           "holdout": [str(d) for d in HOLD], "variants": len(pick), "eligible": len(eligible),
           "all_pick": sorted(pick, key=lambda x: x.get("t") or -99, reverse=True)}
    if eligible:
        ch = eligible[0]
        v = {k: ch[k] for k in (M_GRID if ch["family"] == "M" else R_GRID)}
        hold_rows = per_trade(stocks, nmap, v, HOLD)
        s_hold = stats(hold_rows)
        p_pick, p_hold = portfolio(stocks, nmap, nifty, v, PICK), portfolio(stocks, nmap, nifty, v, HOLD)
        passed = ((s_hold.get("avg_ret_pct") or 0) > 0 and (s_hold.get("t") or 0) >= HOLD_T
                  and (s_hold.get("win_pct") or 0) > 50 and p_hold["final_rupees"] > CAPITAL)
        rep.update({"chosen": v, "chosen_pick": ch, "portfolio_pick": p_pick, "holdout_trades": s_hold,
                    "portfolio_holdout": p_hold, "passed": passed})
    else:
        rep["passed"] = False
        rep["note"] = "no variant met the pre-registered choice rule; holdout not looked at"
    Path(__file__).with_name("swing_v1_results.json").write_text(json.dumps(rep, indent=1, default=str))
    show = {k: v for k, v in rep.items() if k != "all_pick"}
    for k in ("portfolio_pick", "portfolio_holdout"):
        if k in show:
            show[k] = {a: b for a, b in show[k].items() if a != "curve_monthly"}
    print(json.dumps(show, indent=1, default=str))
    print("all variants on the pick period, best t first:")
    for x in rep["all_pick"]:
        name = (f"M n{x['n']} {x['exit']} {x['stock']} {x['market']}" if x["family"] == "M"
                else f"R x{x['x']} ma{x['f']} {x['exit']} stop:{x['stop']} {x['market']}")
        print(f"  {name:<38} n={x.get('trades')} win={x.get('win_pct')}% avg={x.get('avg_ret_pct')}%"
              f" gross={x.get('avg_gross_pct')}% t={x.get('t')} days={x.get('avg_days')}")


if __name__ == "__main__":
    main()
