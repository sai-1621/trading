"""Order-flow strategies on real aggressor data, with returns in % of the account.

Data: Binance's free 1-minute klines (data.binance.vision), which include the taker-buy volume: the volume that
came from buyers hitting the ask. Delta = taker-buy volume minus taker-sell volume. CoinDCX's USDT markets follow
Binance prices closely, so the signals carry over; the trades are charged CoinDCX's fees.

    ..\\.venv\\Scripts\\python orderflow.py              download (cached) and test
    ..\\.venv\\Scripts\\python orderflow.py --coins 5

Strategies (5-minute bars, long; short is the mirror image and needs futures):
  delta surge   a volume spike where aggressive buyers dominate and price closes near the high: follow it
  absorption    a new 24-bar low on heavy aggressive selling, but the bar closes in its upper half: sellers were
                absorbed by passive buyers, buy the reversal
  CVD divergence  price makes a new 24-bar low but the day's cumulative delta is higher than at the previous
                low: buy the divergence on a green bar
  opening flow  the first hour's net delta is strongly positive: buy at 01:00 UTC and hold for the day

Account like the crypto bot: 10,000 capital, max loss 100 a trade (1%), max position 4,000, at most 3 trades
a day and 2 open at once. Settings chosen on the first 2/3 of the days, judged on the last 1/3.
"""
import argparse
import csv
import io
import itertools
import json
import sys
import time
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache" / "binance"
COINS = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "TRX", "LINK", "LTC"]
START = date(2026, 3, 1)
BAR = 5                     # minutes
PER_DAY = 1440 // BAR
ENTRY_FROM, ENTRY_TO, FLAT = 60 // BAR, 18 * 60 // BAR, (23 * 60 + 30) // BAR   # 01:00-18:00 UTC, out by 23:30
SPREAD = 0.0004             # CoinDCX USDT majors, about 0.04%
FEES = {"USDT (0.20%)": 0.002, "INR (0.59%)": 0.0059, "no fees (edge check)": 0.0}
CAPITAL, MAX_LOSS, MAX_ORDER, MAX_TRADES, MAX_OPEN = 10_000, 100, 4_000, 3, 2


# ----------------------------------------------------------------------------------------------- data
def _get_zip(url, f):
    if f.exists():
        return f.read_bytes()
    r = requests.get(url, timeout=60)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(r.content)
    return r.content


def _rows(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        text = z.read(z.namelist()[0]).decode()
    for row in csv.reader(io.StringIO(text)):
        if not row or not row[0].isdigit():
            continue
        t = int(row[0])
        t = t // 1000 if t > 10**14 else t      # Binance spot files switched to microseconds in 2025
        yield t, float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5]), float(row[9])


def minutes(coin, end):
    sym = f"{coin}USDT"
    base = "https://data.binance.vision/data/spot"
    out = []
    m = START
    while m < end.replace(day=1):
        tag = f"{m:%Y-%m}"
        blob = _get_zip(f"{base}/monthly/klines/{sym}/1m/{sym}-1m-{tag}.zip", CACHE / sym / f"{tag}.zip")
        if blob:
            out.extend(_rows(blob))
        m = (m + timedelta(days=32)).replace(day=1)
    d = end.replace(day=1)
    while d < end:
        blob = _get_zip(f"{base}/daily/klines/{sym}/1m/{sym}-1m-{d}.zip", CACHE / sym / f"{d}.zip")
        if blob:
            out.extend(_rows(blob))
        d += timedelta(days=1)
    return out


def sessions(coin, end):
    """{day: [5m bars {o,h,l,c,v,d}]} for complete UTC days."""
    days = defaultdict(dict)
    for t, o, h, l, c, v, tb in minutes(coin, end):
        dt = datetime.fromtimestamp(t / 1000, timezone.utc)
        k = (dt.hour * 60 + dt.minute) // BAR
        b = days[dt.date()].get(k)
        if b is None:
            days[dt.date()][k] = {"o": o, "h": h, "l": l, "c": c, "v": v, "d": 2 * tb - v, "n": 1}
        else:
            b["h"], b["l"], b["c"], b["v"], b["d"], b["n"] = max(b["h"], h), min(b["l"], l), c, b["v"] + v, b["d"] + 2 * tb - v, b["n"] + 1
    out = {}
    for d, bars in days.items():
        if len(bars) == PER_DAY and all(bars[k]["n"] == BAR for k in range(PER_DAY)):
            out[d.isoformat()] = [bars[k] for k in range(PER_DAY)]
    return out


def daily_trend(sess):
    """{day: close of the day before above its 20-day average} - the bots' trend filter."""
    days = sorted(sess)
    closes = [sess[d][-1]["c"] for d in days]
    return {days[i]: closes[i - 1] > sum(closes[i - 20:i]) / 20 for i in range(20, len(days))}


# ----------------------------------------------------------------------------------------------- signals
def mirror(bars):
    return [{"o": -b["o"], "h": -b["l"], "l": -b["h"], "c": -b["c"], "v": b["v"], "d": -b["d"]} for b in bars]


def features(bars):
    """Rolling values per bar, computed once: average volume (48 bars before), lowest low of the 24 bars before,
    ATR(14), the day's cumulative delta, and the previous low 30 min to 4 h back."""
    n = len(bars)
    av, lo24, at, cvd, prev = [0.0] * n, [0.0] * n, [0.0] * n, [0.0] * n, [0] * n
    vsum, run, trs = 0.0, 0.0, []
    for i, b in enumerate(bars):
        av[i] = vsum / min(i, 48) if i else 0.0
        vsum += b["v"] - (bars[i - 48]["v"] if i >= 48 else 0.0)
        lo24[i] = min(x["l"] for x in bars[max(0, i - 24):i]) if i else b["l"]
        tr = b["h"] - b["l"] if i == 0 else max(b["h"] - b["l"], abs(b["h"] - bars[i - 1]["c"]), abs(b["l"] - bars[i - 1]["c"]))
        trs.append(tr)
        at[i] = sum(trs[-14:]) / len(trs[-14:])
        run += b["d"]
        cvd[i] = run
        if i >= 48:
            prev[i] = min(range(i - 48, i - 6), key=lambda t: bars[t]["l"])
    return {"av": av, "lo24": lo24, "atr": at, "cvd": cvd, "prev": prev}


def signals(name, bars, f, p):
    """Bar indexes where the rule fires, with the stop, in long space; entry is that bar's close."""
    out = []
    stop_of = lambda i: bars[i]["l"] if p["stop"] == "bar" else bars[i]["c"] - f["atr"][i]
    if name == "delta surge":
        for i in range(ENTRY_FROM, ENTRY_TO):
            b = bars[i]
            rng = b["h"] - b["l"]
            if rng > 0 and b["v"] > 0 and b["v"] >= p["vol_x"] * f["av"][i] and b["d"] / b["v"] >= p["imb"]                     and (b["c"] - b["l"]) / rng >= 0.7 and b["c"] > b["o"]:
                out.append((i, stop_of(i)))
    elif name == "absorption":
        for i in range(max(ENTRY_FROM, 24), ENTRY_TO):
            b = bars[i]
            rng = b["h"] - b["l"]
            if rng > 0 and b["v"] > 0 and b["l"] < f["lo24"][i] and b["v"] >= p["vol_x"] * f["av"][i]                     and b["d"] / b["v"] <= -p["imb"] and (b["c"] - b["l"]) / rng >= 0.5:
                out.append((i, stop_of(i)))
    elif name == "CVD divergence":
        cvd = f["cvd"]
        for i in range(max(ENTRY_FROM, 48), ENTRY_TO):
            b = bars[i]
            if not (b["c"] > b["o"] and b["l"] < f["lo24"][i]):
                continue
            j = f["prev"][i]
            if b["l"] < bars[j]["l"] and cvd[i] > cvd[j] + p["imb"] * sum(x["v"] for x in bars[j:i + 1]) / 10:
                out.append((i, stop_of(i)))
    elif name == "opening flow":
        i = ENTRY_FROM - 1
        v = sum(b["v"] for b in bars[:i + 1])
        if v > 0 and sum(b["d"] for b in bars[:i + 1]) / v >= p["imb"]:
            out.append((i, min(b["l"] for b in bars[:i + 1]) if p["stop"] == "bar" else bars[i]["c"] - 2 * f["atr"][i]))
    return out


GRIDS = {
    "delta surge": [dict(vol_x=v, imb=m, stop=s, target=t, hold=h, trend=tr) for v, m, s, t, h, tr in
                    itertools.product([2, 3], [0.2, 0.4], ["bar", "atr"], [1.0, 2.0], [12, 999], [False, True])],
    "absorption": [dict(vol_x=v, imb=m, stop=s, target=t, hold=h, trend=tr) for v, m, s, t, h, tr in
                   itertools.product([2, 3], [0.2, 0.4], ["bar", "atr"], [1.0, 2.0], [12, 999], [False, True])],
    "CVD divergence": [dict(imb=m, stop=s, target=t, hold=h, trend=tr) for m, s, t, h, tr in
                       itertools.product([0.0, 1.0], ["bar", "atr"], [1.0, 2.0], [12, 999], [False, True])],
    "opening flow": [dict(imb=m, stop=s, target=t, hold=999, trend=tr) for m, s, t, tr in
                     itertools.product([0.05, 0.1, 0.2], ["bar", "atr"], [1.0, 2.0, 99.0], [False, True])],
}


def trade(bars, i, stop, p):
    """Manage one long from bar i's close. Returns (exit index, r) before costs, or None if the stop is unusable."""
    e = bars[i]["c"] + abs(bars[i]["c"]) * SPREAD / 2
    stop -= abs(e) * SPREAD
    risk = e - stop
    if risk <= 0 or risk < abs(e) * 0.0015:          # stops tighter than 0.15% die to noise and fees
        return None
    target = e + p["target"] * risk
    end = min(FLAT, i + 1 + p["hold"])
    for j in range(i + 1, end):
        b = bars[j]
        if b["l"] <= stop:
            return j, (min(b["o"], stop) - e) / risk, e, risk, "stop"
        if b["h"] >= target:
            return j, p["target"], e, risk, "target"
    return end - 1, (bars[end - 1]["c"] - e) / risk, e, risk, "time"


_PREP, _SIG = {}, {}


def prepared(coin, day, bars, side):
    k = (coin, day, side)
    if k not in _PREP:
        bs = bars if side == "long" else mirror(bars)
        _PREP[k] = (bs, features(bs))
    return _PREP[k]


def candidates(data, trends, name, p, side):
    """All trades the rule would take: [(day, entry i, exit i, coin, r, entry, risk, how)], one per coin per day."""
    out = []
    sig_key = (name, side) + tuple(sorted((k, v) for k, v in p.items() if k not in ("target", "hold", "trend")))
    for coin, sess in data.items():
        for day, bars in sess.items():
            tr = trends[coin].get(day)
            if p["trend"] and tr is not None and tr != (side == "long"):
                continue
            bs, f = prepared(coin, day, bars, side)
            k = sig_key + (coin, day)
            if k not in _SIG:
                _SIG[k] = signals(name, bs, f, p)
            for i, stop in _SIG[k]:
                t = trade(bs, i, stop, p)
                if t:
                    out.append((day, i, t[0], coin, t[1], abs(t[2]), t[3], t[4]))
                    break
    return out


def account(trades, fee):
    """Apply the bot's limits and sizing. Returns per-trade results with P&L in % of capital."""
    by_day = defaultdict(list)
    for t in trades:
        by_day[t[0]].append(t)
    res = []
    for day in sorted(by_day):
        taken, open_until = 0, []
        for d, i, x, coin, r, e, risk, how in sorted(by_day[day], key=lambda t: t[1]):
            open_until = [u for u in open_until if u > i]
            if taken >= MAX_TRADES or len(open_until) >= MAX_OPEN:
                continue
            qty = min(MAX_LOSS / risk, MAX_ORDER / e)
            exit_px = e + r * risk
            cost = qty * (e * SPREAD / 2 + fee * (e + exit_px))
            pnl = qty * risk * r - cost
            res.append({"day": d, "coin": coin, "r": r, "r_net": pnl / (qty * risk), "pct": pnl / CAPITAL * 100, "how": how})
            taken += 1
            open_until.append(x)
    return res


def stats(res, n_days):
    if not res:
        return {"trades": 0, "return_pct": 0.0, "per_month_pct": 0.0, "win_pct": None, "avg_r": None, "avg_r_net": None, "max_dd_pct": 0.0}
    eq = peak = dd = 0.0
    for t in sorted(res, key=lambda t: t["day"]):
        eq += t["pct"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    n = len(res)
    return {"trades": n, "return_pct": round(eq, 2), "per_month_pct": round(eq / n_days * 30.4, 2),
            "win_pct": round(sum(t["pct"] > 0 for t in res) / n * 100, 1), "avg_r": round(sum(t["r"] for t in res) / n, 3),
            "avg_r_net": round(sum(t["r_net"] for t in res) / n, 3), "max_dd_pct": round(dd, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", type=int, default=len(COINS))
    a = ap.parse_args()
    t0 = time.time()
    end = datetime.now(timezone.utc).date()
    data, trends = {}, {}
    for k, coin in enumerate(COINS[:a.coins], 1):
        sys.stdout.write(f"\r  loading {k}/{a.coins} {coin:<5}")
        sys.stdout.flush()
        data[coin] = sessions(coin, end)
        trends[coin] = daily_trend(data[coin])
        data[coin] = {d: b for d, b in data[coin].items() if d in trends[coin]}   # need 20 days for the trend filter
    days = sorted({d for s in data.values() for d in s})
    cut = days[int(len(days) * 2 / 3)]
    n_pick, n_check = sum(d < cut for d in days), sum(d >= cut for d in days)
    print(f"\r  {len(data)} coins, {days[0]} to {days[-1]}: pick {n_pick} days, check {n_check} days (from {cut})")
    hold = {}
    for coin, s in data.items():
        first, last = s[min(s)][0]["o"], s[max(s)][-1]["c"]
        hold[coin] = round((last / first - 1) * 100, 1)
    print("  buy and hold over the whole period: " + ", ".join(f"{c} {v:+.1f}%" for c, v in hold.items()))

    report = {"run_at": datetime.now().isoformat(timespec="seconds"), "coins": list(data), "days": [days[0], days[-1]],
              "pick_days": n_pick, "check_days": n_check, "buy_hold_pct": hold, "account": {
                  "capital": CAPITAL, "max_loss": MAX_LOSS, "max_order": MAX_ORDER, "max_trades_day": MAX_TRADES,
                  "max_open": MAX_OPEN, "spread": SPREAD}, "strategies": {}}
    for name, grid in GRIDS.items():
        print(f"\n  {name}: {len(grid) * 2} setting combinations (long and short)")
        rows = []
        for side in ("long", "short"):
            for p in grid:
                cands = candidates(data, trends, name, p, side)
                pick = [t for t in cands if t[0] < cut]
                check = [t for t in cands if t[0] >= cut]
                row = {"side": side, "params": p}
                for fname, fee in FEES.items():
                    row[fname] = {"pick": stats(account(pick, fee), n_pick), "check": stats(account(check, fee), n_check)}
                rows.append(row)
        report["strategies"][name] = {}
        for fname in FEES:
            ok = [r for r in rows if r[fname]["pick"]["trades"] >= 30]
            if not ok:
                continue
            best = max(ok, key=lambda r: r[fname]["pick"]["return_pct"])
            pos = sum(r[fname]["check"]["return_pct"] > 0 for r in rows if r[fname]["check"]["trades"] >= 10)
            judged = sum(r[fname]["check"]["trades"] >= 10 for r in rows)
            pk, ck = best[fname]["pick"], best[fname]["check"]
            print(f"    {fname:<21} best on pick: {best['side']:<5} {json.dumps(best['params'])}")
            print(f"    {'':<21} pick  {pk['trades']:>4} trades  {pk['return_pct']:+7.2f}%  ({pk['per_month_pct']:+.2f}%/month)  "
                  f"win {pk['win_pct']}%  gross {pk['avg_r']}R  net {pk['avg_r_net']}R")
            print(f"    {'':<21} check {ck['trades']:>4} trades  {ck['return_pct']:+7.2f}%  ({ck['per_month_pct']:+.2f}%/month)  "
                  f"win {ck['win_pct']}%  gross {ck['avg_r']}R  net {ck['avg_r_net']}R  max drawdown {ck['max_dd_pct']}%"
                  f"  | {pos} of {judged} settings made money on check")
            report["strategies"][name][fname] = {"best": best, "check_positive": pos, "check_judged": judged}
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "orderflow.json").write_text(json.dumps(report, indent=1))
    print(f"\n  saved results/orderflow.json ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
