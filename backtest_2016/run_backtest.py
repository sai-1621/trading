"""Long-term backtest, 2016 to today, of the daily strategies the trading bots use, on NSE and US stocks.

    python run_backtest.py              both markets (NIFTY 100 and S&P 500)
    python run_backtest.py --refresh    re-download prices (otherwise a 24-hour cache)

What it tests (rules in regime_engine.py):
  * each strategy on its own, every stock, Jan 2016 to today (2015 data only warms up the averages)
  * the trained bot: picks the best strategy (or cash) for each market condition, trained on 2016-2018 only,
    retrained every quarter on everything before it, trading from Jan 2019 (walk-forward: never sees the future)
  * benchmarks: buying and holding every stock (equal weight) and the index itself (NIFTY 50 / SPY)

Prices are adjusted for splits and dividends (Yahoo adjclose), so dividends count everywhere. Orders fill at the next
day's open after a signal at the close, paying costs on every buy and sell. Tax is not included.
Not testable this far back: the intraday opening-range strategy (free 5-minute data covers only 60 days).
Caveat: the stocks are TODAY's index members, which flatters every result (survivorship).
"""
import argparse
import csv
import io
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests

import regime_engine as R

HERE = Path(__file__).resolve().parent
CACHE, RESULTS = HERE / "cache", HERE / "results"
START_DATA, START_TEST, START_TRAINED = date(2015, 1, 1), date(2016, 1, 1), date(2019, 1, 1)
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

MARKETS = {
    "nse": {"name": "NSE India (NIFTY 100)", "cur": "₹", "index": "^NSEI", "index_name": "NIFTY 50", "suffix": ".NS",
            "universe": "https://archives.nseindia.com/content/indices/ind_nifty100list.csv", "col": "Symbol",
            "cost": 0.0015 + 0.00025,   # delivery: STT 0.1% + stamp, exchange, DP, GST (~0.15%) + half a 0.05% spread
            "cost_note": "0.175% per buy or sell (delivery charges + spread)"},
    "us": {"name": "US (S&P 500)", "cur": "$", "index": "SPY", "index_name": "SPY", "suffix": "",
           "universe": "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv", "col": "Symbol",
           "cost": 0.0003 + 0.0001,   # IBKR fixed commission on a ~$4,000 order (~0.03%) + half a 0.02% spread
           "cost_note": "0.04% per buy or sell (IBKR commission + spread)"},
}
LABELS = {
    "hold": "Buy and hold", "cash": "Cash", "ma20": "Trend: above 20-day average", "ma50": "Trend: above 50-day average",
    "ma100": "Trend: above 100-day average", "ma200": "Trend: above 200-day average",
    "ma50_index": "Trend 50-day + index filter", "ma100_index": "Trend 100-day + index filter",
    "donchian20_10": "Breakout: 20-day high / 10-day low", "donchian55_20": "Breakout: 55-day high / 20-day low",
    "dip_rsi2": "Dip-buy in uptrend (RSI-2 < 10)", "dip_rsi2_any": "Dip-buy any time (RSI-2 < 5)",
}


def symbols(m):
    r = S.get(m["universe"], timeout=30)
    r.raise_for_status()
    return [row[m["col"]].strip() for row in csv.DictReader(io.StringIO(r.text)) if row.get("Series", "EQ").strip() == "EQ"]


def daily(ysym, refresh):
    f = CACHE / f"{ysym.replace('^', '_')}.json"
    if f.exists() and not refresh and time.time() - f.stat().st_mtime < 86400:
        return json.loads(f.read_text())
    p1 = int(datetime(START_DATA.year, 1, 1, tzinfo=timezone.utc).timestamp())
    for attempt in range(4):
        r = S.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(ysym)}",
                  params={"interval": "1d", "period1": p1, "period2": int(time.time())}, timeout=30)
        if r.status_code == 429:
            time.sleep(3 + 5 * attempt)
            continue
        r.raise_for_status()
        break
    res = r.json()["chart"]["result"][0]
    q, adj = res["indicators"]["quote"][0], res["indicators"]["adjclose"][0]["adjclose"]
    tz = res["meta"].get("exchangeTimezoneName", "UTC")
    rows = []
    for i, t in enumerate(res.get("timestamp") or []):
        o, h, l, c, a = q["open"][i], q["high"][i], q["low"][i], q["close"][i], adj[i]
        if None in (o, h, l, c, a) or c <= 0 or o <= 0:
            continue
        k = a / c   # total-return adjustment (splits and dividends) applied to the whole bar
        rows.append([t, o * k, h * k, l * k, a])
    f.write_text(json.dumps({"tz": tz, "rows": rows}))
    time.sleep(0.15)
    return {"tz": tz, "rows": rows}


def cols(raw):
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(raw["tz"])
    today = datetime.now(tz).date()
    out = {"d": [], "o": [], "h": [], "l": [], "c": []}
    for t, o, h, l, c in raw["rows"]:
        d = datetime.fromtimestamp(t, tz).date()
        if d >= today or (out["d"] and d <= out["d"][-1]):   # closed sessions only, no duplicates
            continue
        for k, v in zip("dohlc", (d, o, h, l, c)):
            out[k].append(v)
    return out


def stats(port, dates):
    """port: daily portfolio returns aligned with dates."""
    if not port:
        return None
    ret, dd = R.equity_stats(port)
    years = (dates[-1] - dates[0]).days / 365.25
    cagr = (1 + ret) ** (1 / years) - 1 if years > 0 and ret > -1 else None
    mean = sum(port) / len(port)
    sd = (sum((r - mean) ** 2 for r in port) / max(1, len(port) - 1)) ** 0.5
    by_year = {}
    for d, r in zip(dates, port):
        by_year.setdefault(d.year, []).append(r)
    curve, eq = [], 1.0
    for d, r in zip(dates, port):
        eq *= 1 + r
        curve.append(eq)
    step = max(1, len(curve) // 260)   # about weekly points for the chart
    return {"total_pct": round(ret * 100, 1), "cagr_pct": round(cagr * 100, 1) if cagr is not None else None,
            "max_dd_pct": round(dd * 100, 1), "sharpe": round(mean / sd * 252 ** 0.5, 2) if sd else None,
            "by_year": {y: round(R.equity_stats(v)[0] * 100, 1) for y, v in sorted(by_year.items())},
            "curve": [[str(dates[i]), round(curve[i], 4)] for i in range(0, len(curve), step)] + [[str(dates[-1]), round(curve[-1], 4)]]}


def portfolio(per_stock, start):
    """Equal weight across the stocks that have data each day."""
    by_day = {}
    for name, (dates, rets) in per_stock.items():
        for d, r in zip(dates, rets):
            if d >= start:
                by_day.setdefault(d, []).append(r)
    days = sorted(by_day)
    return days, [sum(by_day[d]) / len(by_day[d]) for d in days]


def run_market(key, refresh):
    m = MARKETS[key]
    syms = symbols(m)
    bench = cols(daily(m["index"], refresh))
    stocks = {}
    for i, s in enumerate(syms, 1):
        sys.stdout.write(f"\r  {m['name']}: downloading {i}/{len(syms)} {s:<12}")
        sys.stdout.flush()
        try:
            b = cols(daily(s.replace(".", "-") + m["suffix"], refresh))
        except Exception:
            continue
        if len(b["c"]) >= 300:
            stocks[s] = b
    print(f"\r  {m['name']}: {len(stocks)} of {len(syms)} stocks with enough history" + " " * 20)

    bench_up = R.bench_up_at_close(bench)
    cost = m["cost"]
    per = {}   # strategy -> {stock: (dates, rets)}
    activity = {}
    for s, b in stocks.items():
        for name, pos in R.strategies(b, bench_up).items():
            rets = R.daily_returns(b, pos, cost)
            per.setdefault(name, {})[s] = (b["d"], rets)
            a = activity.setdefault(name, {"days": 0, "held": 0, "trades": 0, "stocks_up": 0, "stocks": 0})
            idx = [i for i, d in enumerate(b["d"]) if d >= START_TEST]
            if not idx:
                continue
            a["days"] += len(idx)
            a["held"] += sum(pos[i - 1] for i in idx if i > 0)
            a["trades"] += sum(1 for i in idx if i > 0 and pos[i] and not pos[i - 1])
            a["stocks"] += 1
            a["stocks_up"] += R.equity_stats([rets[i] for i in idx])[0] > 0

    out = {"name": m["name"], "cur": m["cur"], "index_name": m["index_name"], "cost_note": m["cost_note"],
           "stocks": len(stocks), "strategies": {}, "trained": None}
    idays = [i for i, d in enumerate(bench["d"]) if d >= START_TEST]
    ib = [bench["c"][i] / bench["c"][i - 1] - 1 for i in idays]
    out["index"] = stats(ib, [bench["d"][i] for i in idays])
    idays19 = [i for i in idays if bench["d"][i] >= START_TRAINED]
    out["index_2019"] = stats([bench["c"][i] / bench["c"][i - 1] - 1 for i in idays19], [bench["d"][i] for i in idays19])
    for name, ps in per.items():
        if name == "cash":
            continue
        days, port = portfolio(ps, START_TEST)
        a = activity[name]
        days19, port19 = portfolio(ps, START_TRAINED)
        years = (days[-1] - days[0]).days / 365.25
        out["strategies"][name] = {
            "label": LABELS.get(name, name), "full": stats(port, days), "since_2019": stats(port19, days19),
            "time_in_market_pct": round(a["held"] / a["days"] * 100, 1) if a["days"] else 0,
            "trades_per_stock_per_year": round(a["trades"] / max(1, a["stocks"]) / years, 1),
            "stocks_profitable_pct": round(a["stocks_up"] / max(1, a["stocks"]) * 100)}

    print(f"  {m['name']}: training the market-condition bot (2016-2018, retrained quarterly) ...")
    tdays, tport, rep = _trained_portfolio(stocks, bench, cost)
    labels = R.regime_labels(bench)
    out["trained"] = {"stats": stats(tport, tdays), "choices": rep["choices"],
                      "stocks_profitable_pct": rep["markets_up_pct"],
                      "regime_days": {r: sum(1 for d, v in labels.items() if v == r and d >= START_TRAINED) for r in R.REGIMES}}
    return out


def _trained_portfolio(stocks, bench, cost):
    """The trained bot's equal-weight daily returns from 2019 (same logic as walk_forward, kept per day)."""
    captured = {}
    orig = R._report

    def grab(oos, bh, choices, oos_start):
        captured["oos"] = oos
        return orig(oos, bh, choices, oos_start)

    R._report = grab
    try:
        rep = R.walk_forward(stocks, bench, lambda s: cost, start_date=START_TRAINED)
    finally:
        R._report = orig
    by_day = {}
    for name, rows in captured["oos"].items():
        for d, r in rows:
            by_day.setdefault(d, []).append(r)
    days = sorted(by_day)
    return days, [sum(by_day[d]) / len(by_day[d]) for d in days], rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="nse,us")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    CACHE.mkdir(exist_ok=True)
    RESULTS.mkdir(exist_ok=True)
    out = {"run_at": datetime.now(timezone.utc).isoformat(), "test_from": str(START_TEST), "trained_from": str(START_TRAINED),
           "markets": {}}
    for k in [x.strip() for x in a.markets.split(",")]:
        out["markets"][k] = run_market(k, a.refresh)
        res = out["markets"][k]
        best = sorted(res["strategies"].items(), key=lambda kv: kv[1]["full"]["cagr_pct"] or -99, reverse=True)
        print(f"\n  {res['name']}, Jan 2016 to today, per year (CAGR) | worst fall | Sharpe:")
        print(f"    {'Index ' + res['index_name']:<40} {res['index']['cagr_pct']:>6}% | {res['index']['max_dd_pct']:>5}% | {res['index']['sharpe']}")
        for name, s in best:
            f = s["full"]
            print(f"    {s['label']:<40} {f['cagr_pct']:>6}% | {f['max_dd_pct']:>5}% | {f['sharpe']}")
        t = res["trained"]["stats"]
        h19 = res["strategies"]["hold"]["since_2019"]
        print(f"  Trained bot (from 2019): {t['cagr_pct']}%/yr, worst fall {t['max_dd_pct']}%, Sharpe {t['sharpe']} | "
              f"buy-and-hold same period {h19['cagr_pct']}%/yr, worst fall {h19['max_dd_pct']}%\n")
    (RESULTS / "results.json").write_text(json.dumps(out, default=str, indent=1), encoding="utf-8")
    print("Saved results/results.json")


if __name__ == "__main__":
    main()
