"""Backtest two intraday strategies, order blocks (Smart Money Concepts) and VWAP pullback, after each broker's charges.

Run it with the Python of the bot whose market you test (it reuses that bot's data, cache, charges and settings):
    ..\\angelone_bot\\.venv\\Scripts\\python lab.py --market nse
    ..\\ibkr_us_bot\\.venv\\Scripts\\python  lab.py --market us
    ..\\coindcx_bot\\.venv\\Scripts\\python  lab.py --market crypto-inr     (or crypto-usdt)

Every setting combination is chosen on the first 2/3 of the days (pick) and judged on the last 1/3 (check), which
played no part in choosing it. The current opening-range breakout runs on the same days as a baseline. The best
setting is also re-run with the Time Machine gate the bots use (Monte Carlo replays from earlier days only).
Results: results/<market>.json
"""
import argparse
import itertools
import json
import random
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BOTS = {"nse": "angelone_bot", "us": "ibkr_us_bot", "crypto-inr": "coindcx_bot", "crypto-usdt": "coindcx_bot"}
MIN_PICK_TRADES = 30
TM_REPLAYS = 1000          # the bots use 10,000; 1,000 gives the same take/skip almost always and runs 10x faster
TM_WINDOW = 2


# ----------------------------------------------------------------------------------------------- market adapters
class Market:
    """Loads sessions as {"sym","day","bars":[{o,h,l,c,v}],"raw","trend","sf","entry_from","entry_to","end"} and
    knows the market's charges. Bar index i covers minutes [start + i*bm, start + (i+1)*bm)."""
    shorts = True

    def cost_r(self, s, e, risk, exit_px):
        raise NotImplementedError


class Stocks(Market):
    def __init__(self, kind, n, refresh):
        self.kind = kind
        bot = ROOT / BOTS[kind]
        sys.path.insert(0, str(bot))
        import config, costs, market, backtest
        from skills import scanner, rule
        from skills.timemachine import full_sessions
        self.config, self.costs, self.market, self.rule = config, costs, market, rule
        self.bm, self.cur = market.BAR_MIN, "Rs" if kind == "nse" else "$"
        if kind == "nse":
            config.WATCH_SIZE = n
            hits = scanner.scan(write=False)["hits"]
        else:
            hits = scanner.scan(write=False, size=n)["hits"]
        today = market.now().date()
        p = rule.live_params()
        self.sessions = []
        for i, h in enumerate(hits, 1):
            sys.stdout.write(f"\r  loading {i}/{len(hits)} {h['symbol']:<14}")
            sys.stdout.flush()
            try:
                m5, d1 = backtest._load(h["symbol"], refresh)
            except Exception as ex:
                print(f"\n  {h['symbol']} skipped: {ex}")
                continue
            trends = rule.trend_map(d1)
            for d, bs in full_sessions(m5, today).items():
                if kind == "nse":
                    to_min, flat = p["entry_to"], p["flat_by"]
                else:
                    to_min, flat = market.entries_to_min(d), market.flat_by_min(d)
                self.sessions.append({
                    "sym": h["symbol"], "day": d.isoformat(), "raw": bs, "trend": trends.get(d),
                    "bars": [{k: b[k] for k in "ohlcv"} for b in bs], "sf": config.ASSUMED_SPREAD_PCT / 100,
                    "entry_from": p["entry_from"] - market.OPEN_MIN, "entry_to": to_min - market.OPEN_MIN,
                    "end": (flat - market.OPEN_MIN) // market.BAR_MIN})
        print(f"\r  {len({s['sym'] for s in self.sessions})} stocks, {len(self.sessions)} full sessions" + " " * 30)

    def cost_r(self, s, e, risk, exit_px):
        sf = s["sf"]
        if self.kind == "nse":
            value = self.rule.position_value(e, risk)
            n = value / e
            cost = self.costs.round_trip(value, n * exit_px) + n * e * sf / 2
        else:
            n = self.rule.shares_for(e, risk)
            cost = self.costs.round_trip(n * e, n * exit_px, n) + n * e * sf / 2
        return cost / (n * risk)

    def baseline(self, s):
        return self.rule.simulate_day(s["raw"], self.rule.live_params(), None, s["trend"])


class Crypto(Market):
    shorts = False      # CoinDCX spot: no short selling

    def __init__(self, quote, n, days, refresh):
        sys.path.insert(0, str(ROOT / "coindcx_bot"))
        import config, backtest
        from coindcx_client import CoinDCXClient
        from skills import scanner, rule, utc_now, day_start_ms
        from skills.timemachine import _full_days
        self.config, self.rule, self.bm, self.cur = config, rule, config.BAR_MINUTES, quote
        self.fee = config.fee_for(quote)
        client = CoinDCXClient()
        hits = scanner.scan(client, quote, write=False)["hits"][:n]
        today_ms = day_start_ms(utc_now())
        p = rule.live_params()
        self.sessions = []
        for i, h in enumerate(hits, 1):
            sys.stdout.write(f"\r  loading {i}/{len(hits)} {h['market']:<14}")
            sys.stdout.flush()
            try:
                m15, d1 = backtest.load(client, h["pair"], days, refresh)
            except Exception as ex:
                print(f"\n  {h['market']} skipped: {ex}")
                continue
            trends = rule.trend_map(d1)
            for dnum, bs in _full_days(m15, today_ms).items():
                self.sessions.append({
                    "sym": h["market"], "day": datetime.fromtimestamp(dnum * 86400, timezone.utc).date().isoformat(),
                    "raw": bs, "trend": trends.get(dnum), "sf": h["spread_pct"] / 100,
                    "bars": [{"o": float(b["open"]), "h": float(b["high"]), "l": float(b["low"]),
                              "c": float(b["close"]), "v": float(b["volume"])} for b in bs],
                    "entry_from": p["entry_from"], "entry_to": p["entry_to"], "end": p["flat_by"] // self.bm})
        print(f"\r  {len({s['sym'] for s in self.sessions})} markets, {len(self.sessions)} full days" + " " * 30)

    def cost_r(self, s, e, risk, exit_px):
        return (s["sf"] / 2 * e + self.fee * (e + exit_px)) / risk

    def baseline(self, s):
        return self.rule.simulate_day(s["raw"], self.rule.live_params(), s["sf"], self.fee, s["trend"])


# ----------------------------------------------------------------------------------------------- shared pieces
def mirror(bars):
    """Prices negated: running the long rules on these is the short side (high and low swap)."""
    return [{"o": -b["o"], "h": -b["l"], "l": -b["h"], "c": -b["c"], "v": b["v"]} for b in bars]


def atr(bars, end, period=14):
    trs = []
    for i in range(max(0, end - period), end + 1):
        b = bars[i]
        trs.append(b["h"] - b["l"] if i == 0 else max(b["h"] - b["l"], abs(b["h"] - bars[i - 1]["c"]),
                                                       abs(b["l"] - bars[i - 1]["c"])))
    return sum(trs) / len(trs)


def in_window(s, bm, i, at_close=True):
    m = (i + 1) * bm if at_close else i * bm
    return s["entry_from"] <= m <= s["entry_to"] and i + 1 < s["end"]


def manage(bars, start, end, e, stop, target):
    """Walk bars start..end-1 (prices in long space). Stop first when a bar touches both; a gap past the stop fills
    at the open. Returns (outcome, exit price)."""
    for b in bars[start:end]:
        if b["l"] <= stop:
            return "stop", min(b["o"], stop)
        if b["h"] >= target:
            return "target", target
    return "neither", bars[min(end, len(bars)) - 1]["c"]


def finish(mk, s, side, e, stop, target, outcome, exit_px, entry_i):
    """Results in R, net of charges. e/stop/exit are in long space (negated prices for shorts)."""
    risk = e - stop
    r = (exit_px - e) / risk
    cost = mk.cost_r(s, abs(e), risk, abs(exit_px))
    return {"outcome": outcome, "r": r, "r_net": r - cost, "side": side, "sym": s["sym"], "day": s["day"],
            "entry_i": entry_i, "e": e, "stop": stop, "target": target}


def risk_ok(e, risk, sf):
    return risk > 0 and risk >= max(abs(e) * 0.0005, 2 * sf * abs(e))


def trend_ok(p, s, side):
    if not p["trend"] or s["trend"] is None:
        return True
    return s["trend"] if side == "long" else not s["trend"]


# ----------------------------------------------------------------------------------------------- VWAP pullback
def vwap_pullback(mk, s, p, side):
    """In a session trending above VWAP (last k closes above a rising VWAP), buy the first bar that dips to VWAP
    and closes back above it, green. Entry at that bar's close plus half the spread."""
    if not trend_ok(p, s, side):
        return None
    bars = s["bars"] if side == "long" else mirror(s["bars"])
    sf, k = s["sf"], p["k"]
    pv = vol = 0.0
    vw = []
    for b in bars:
        tp = (b["h"] + b["l"] + b["c"]) / 3
        pv, vol = pv + tp * b["v"], vol + b["v"]
        vw.append(pv / vol if vol > 0 else b["c"])
    for i in range(k + 1, s["end"] - 1):
        if not in_window(s, mk.bm, i):
            continue
        b = bars[i]
        trending = all(bars[j]["c"] > vw[j] for j in range(i - k, i)) and vw[i] > vw[i - k]
        if not (trending and b["l"] <= vw[i] and b["c"] > vw[i] and b["c"] > b["o"]):
            continue
        e = b["c"] + abs(b["c"]) * sf / 2
        if p["stop"] == "bar-low":
            stop = min(b["l"], bars[i - 1]["l"]) - abs(e) * sf
        else:
            stop = vw[i] - float(p["stop"][4:]) * atr(bars, i)
        risk = e - stop
        if not risk_ok(e, risk, sf):
            return None
        target = e + p["target"] * risk
        out, px = manage(bars, i + 1, s["end"], e, stop, target)
        return finish(mk, s, side, e, stop, target, out, px, i)
    return None


VWAP_GRID = [dict(k=k, stop=st, target=t, trend=tr) for k, st, t, tr in
             itertools.product([3, 6], ["bar-low", "atr:1.0"], [1.0, 1.5, 2.0], [False, True])]


# ----------------------------------------------------------------------------------------------- order blocks
def order_block(mk, s, p, side):
    """Bullish order block, intraday:
    1. swing high = a bar whose high beats the 2 bars either side (known only 2 bars later);
    2. break of structure (BOS) = a close above the latest unbroken swing high;
    3. the impulse leg runs from the lowest low since that swing high to the BOS bar; with fvg on, it must leave a
       fair value gap (a bar whose low is above the high two bars earlier);
    4. order block = the last down candle at or just before the leg's low; zone = its high (wick) or open (body)
       down to its low;
    5. entry = a limit buy at the zone top when price comes back within `wait` bars (fill only if price trades
       through it); stop just under the zone low; target = R multiple, or the leg high ("leg").
    A newer BOS replaces an older, unfilled zone. One trade per session."""
    if not trend_ok(p, s, side):
        return None
    bars = s["bars"] if side == "long" else mirror(s["bars"])
    sf, n, end = s["sf"], len(s["bars"]), s["end"]
    swing_i = None
    zone = None   # (top, bottom, leg_high, expires_at)
    for j in range(2, end - 1):
        c = j - 2      # the bar that can now be confirmed as a swing high
        if c >= 2 and bars[c]["h"] > max(bars[c - 2]["h"], bars[c - 1]["h"], bars[c + 1]["h"], bars[c + 2]["h"]):
            swing_i = c
        b = bars[j]
        # 1) a waiting zone: does this bar fill it?
        if zone and j <= zone[3] and in_window(s, mk.bm, j, at_close=False):
            top, bottom, leg_high, _ = zone
            if b["l"] < top:
                if b["o"] <= bottom:          # opened below the whole zone: setup failed, no fill
                    zone = None
                else:
                    e = min(b["o"], top)
                    stop = bottom - abs(e) * sf
                    risk = e - stop
                    if not risk_ok(e, risk, sf):
                        zone = None
                    else:
                        target = leg_high if p["target"] == "leg" else e + p["target"] * risk
                        if target <= e:
                            zone = None
                        elif b["l"] <= stop:
                            return finish(mk, s, side, e, stop, target, "stop", min(b["o"], stop), j)
                        else:
                            out, px = manage(bars, j + 1, end, e, stop, target)
                            return finish(mk, s, side, e, stop, target, out, px, j)
        if zone and j > zone[3]:
            zone = None
        # 2) a new break of structure?
        if swing_i is not None and b["c"] > bars[swing_i]["h"]:
            sh = swing_i
            swing_i = None    # this swing high is now broken
            m = min(range(sh, j + 1), key=lambda t: bars[t]["l"])
            ob = next((t for t in range(m, max(-1, m - 6), -1) if bars[t]["c"] < bars[t]["o"]), None)
            if ob is None:
                continue
            if p["fvg"] and not any(bars[t]["l"] > bars[t - 2]["h"] for t in range(max(m + 2, 2), j + 1)):
                continue
            top = bars[ob]["h"] if p["zone"] == "wick" else bars[ob]["o"]
            bottom = bars[ob]["l"]
            if top <= bottom:
                continue
            zone = (top, bottom, max(x["h"] for x in bars[m:j + 1]), j + p["wait"])
    return None


OB_GRID = [dict(zone=z, fvg=f, target=t, trend=tr, wait=w) for z, f, t, tr, w in
           itertools.product(["wick", "body"], [True, False], [1.0, 2.0, "leg"], [False, True], [12, 24])]


# ----------------------------------------------------------------------------------------------- Time Machine gate
def time_machine(mk, by_sym_day, t):
    """The bots' Monte Carlo gate, using only sessions of the same symbol before the trade's day: replay the rest
    of the day from past bars at the same time of day; take the trade only if the target-first share beats
    break-even after costs and the average R after costs is positive."""
    s = by_sym_day[(t["sym"], t["day"])]
    past = [x for (sym, day), x in by_sym_day.items() if sym == t["sym"] and day < t["day"]][-60:]
    if len(past) < 20:
        return False
    neg = t["side"] == "short"
    pool = defaultdict(list)
    for x in past:
        bs = mirror(x["bars"]) if neg else x["bars"]
        for i in range(1, len(bs)):
            pc = bs[i - 1]["c"]
            b = bs[i]
            pool[i].append((b["o"] / pc, b["h"] / pc, b["l"] / pc, b["c"] / pc))
    e, stop, target = t["e"], t["stop"], t["target"]
    risk, R = e - stop, (target - e) / (e - stop)
    nb = len(s["bars"])
    slots = [[r for k in range(max(1, q - TM_WINDOW), min(nb, q + TM_WINDOW + 1)) for r in pool[k]]
             for q in range(t["entry_i"] + 1, s["end"])]
    if not slots or not all(slots):
        return False
    rng = random.Random(19)
    tf = sfc = 0
    tot = 0.0
    for _ in range(TM_REPLAYS):
        px, res = e, None
        for cands in slots:
            o, h, l, c = rng.choice(cands)
            # ratios work for negated prices too: p * ratio keeps the sign
            lo, hi = (px * l, px * h)
            if lo <= stop:
                res, sfc = -1.0, sfc + 1
                break
            if hi >= target:
                res, tf = R, tf + 1
                break
            px *= c
        tot += (px - e) / risk if res is None else res
    cost = mk.cost_r(s, abs(e), risk, abs(e))
    decided = tf + sfc
    share = tf / decided if decided else 0
    return decided > 0 and share >= (1 + cost) / (1 + R) and tot / TM_REPLAYS - cost > 0


# ----------------------------------------------------------------------------------------------- reporting
def summarize(ts):
    ts = [t for t in ts if t]
    n = len(ts)
    if not n:
        return {"trades": 0, "win_pct": None, "avg_r": None, "avg_r_net": None, "total_r_net": 0}
    return {"trades": n, "target": sum(t["outcome"] == "target" for t in ts),
            "stop": sum(t["outcome"] == "stop" for t in ts), "neither": sum(t["outcome"] == "neither" for t in ts),
            "win_pct": round(sum(t["r_net"] > 0 for t in ts) / n * 100, 1),
            "avg_r": round(sum(t["r"] for t in ts) / n, 3), "avg_r_net": round(sum(t["r_net"] for t in ts) / n, 3),
            "total_r_net": round(sum(t["r_net"] for t in ts), 1)}


def label(name, p, side):
    return f"{name} {side} " + " ".join(f"{k}={v}" for k, v in p.items())


def run_strategy(mk, name, fn, grid, cut, sides):
    rows = []
    for side in sides:
        for p in grid:
            pick, check = [], []
            for s in mk.sessions:
                t = fn(mk, s, p, side)
                if t:
                    (pick if s["day"] < cut else check).append(t)
            rows.append({"strategy": name, "side": side, "params": p, "label": label(name, p, side),
                         "pick": summarize(pick), "check": summarize(check), "_check": check})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True, choices=list(BOTS))
    ap.add_argument("--stocks", type=int, default=60, help="most traded stocks / markets to test")
    ap.add_argument("--days", type=int, default=120, help="crypto only: days of 15m candles")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    print(f"\n=== {a.market} ===")
    if a.market.startswith("crypto"):
        mk = Crypto(a.market.split("-")[1].upper(), a.stocks, a.days, a.refresh)
    else:
        mk = Stocks(a.market, a.stocks, a.refresh)
    days = sorted({s["day"] for s in mk.sessions})
    cut = days[int(len(days) * 2 / 3)]
    n_check = sum(d >= cut for d in days)
    print(f"  {days[0]} to {days[-1]}: pick {len(days) - n_check} days, check {n_check} days (from {cut})")

    base = [(s, mk.baseline(s)) for s in mk.sessions]
    baseline = {"pick": mk.rule.summarize([r for s, r in base if s["day"] < cut]),
                "check": mk.rule.summarize([r for s, r in base if s["day"] >= cut])}

    sides = ["long", "short"] if mk.shorts else ["long"]
    rows = run_strategy(mk, "VWAP pullback", vwap_pullback, VWAP_GRID, cut, sides)
    rows += run_strategy(mk, "Order block", order_block, OB_GRID, cut, sides)

    by_sym_day = {(s["sym"], s["day"]): s for s in mk.sessions}
    out = {"market": a.market, "run_at": datetime.now().isoformat(timespec="seconds"), "days": [days[0], days[-1]],
           "pick_days": len(days) - n_check, "check_days": n_check, "symbols": len({s["sym"] for s in mk.sessions}),
           "baseline_orb": baseline, "strategies": {}}
    print(f"\n  Baseline, the bots' current opening-range breakout: pick {baseline['pick']['avg_r_net']}R "
          f"({baseline['pick']['trades']} trades) | check {baseline['check']['avg_r_net']}R "
          f"({baseline['check']['trades']} trades)  [R per trade after charges]")
    for name in ("VWAP pullback", "Order block"):
        mine = [r for r in rows if r["strategy"] == name]
        ok = [r for r in mine if r["pick"]["trades"] >= MIN_PICK_TRADES]
        ranked = sorted(ok, key=lambda r: r["pick"]["avg_r_net"], reverse=True)
        checks = [r["check"]["avg_r_net"] for r in mine if r["check"]["trades"] >= 10]
        positive = sum(c > 0 for c in checks)
        print(f"\n  {name}: {len(mine)} setting combinations, {positive} of {len(checks)} made money on the check days, "
              f"median {statistics.median(checks) if checks else '-'}R")
        print(f"    {'settings':<62} | {'PICK n':>6} {'win%':>5} {'gross':>6} {'R net':>6} | {'CHECK n':>7} {'win%':>5} {'gross':>6} {'R net':>6}")
        for r in ranked[:6]:
            pk, ck = r["pick"], r["check"]
            f = lambda v: "-" if v is None else v
            print(f"    {r['label'][len(name) + 1:][:62]:<62} | {pk['trades']:>6} {f(pk['win_pct']):>5} "
                  f"{f(pk['avg_r']):>6} {f(pk['avg_r_net']):>6} | {ck['trades']:>7} {f(ck['win_pct']):>5} {f(ck['avg_r']):>6} {f(ck['avg_r_net']):>6}")
        best = ranked[0] if ranked else None
        tm = None
        if best:
            kept = [t for t in best["_check"] if time_machine(mk, by_sym_day, t)]
            tm = summarize(kept)
            print(f"    best on pick + Time Machine gate, check days: {tm['trades']} of {best['check']['trades']} trades "
                  f"taken, {tm['avg_r_net']}R per trade after charges")
            ck = best["check"]
            if ck["trades"] >= 15 and (ck["avg_r_net"] or 0) > 0 and (tm["avg_r_net"] or 0) >= 0 and best["pick"]["avg_r_net"] > 0:
                verdict = f"PROMISING: {best['label']} made money on both periods: {best['pick']['avg_r_net']}R (pick) and {ck['avg_r_net']}R per trade after charges on {ck['trades']} unseen trades. Paper trade it before anything else."
            elif (ck["avg_r_net"] or 0) > 0:
                verdict = f"NOT CONSISTENT: {best['label']} lost {best['pick']['avg_r_net']}R per trade on the pick days but made {ck['avg_r_net']}R on the check days ({ck['trades']} trades). One good month is not an edge."
            else:
                verdict = f"NO RELIABLE EDGE: the best settings on the pick days made {ck['avg_r_net']}R per trade after charges on the check days ({ck['trades']} trades)."
        else:
            verdict = "Too few trades to judge."
        print(f"    {verdict}")
        strip = lambda r: {k: v for k, v in r.items() if k != "_check"}
        out["strategies"][name] = {"combinations": len(mine), "check_positive": positive, "check_judged": len(checks),
                                   "check_median_r": statistics.median(checks) if checks else None,
                                   "best": strip(best) if best else None, "best_with_time_machine": tm,
                                   "verdict": verdict, "ranked": [strip(r) for r in ranked[:12]]}
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / f"{a.market}.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"\n  saved results/{a.market}.json ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
