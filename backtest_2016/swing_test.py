"""Holding for weeks: is momentum / trend-following real, or just survivorship bias?

    ..\\.venv\\Scripts\\python swing_test.py

Survivorship-free (the list of things to pick from is fixed and known in advance):
  1. India sector rotation: 13 NSE sector indices, 2012 to today. Each month hold the 3 strongest (6-month return),
     only while NIFTY 50 is above its 200-day average (else cash). Benchmark: NIFTY 50 (both price indices).
  2. US sector rotation: the 9 original SPDR sector ETFs, 2000 to today (includes the 2000-02 and 2008 crashes).
     Benchmark: SPY. Dividends included.
  3. MTUM (iShares USA Momentum ETF) against SPY since 2014: a real momentum fund, no backtest choices at all.
Today's index members (biased: they are the stocks that went on to succeed):
  4. Monthly momentum, top 20 of NIFTY 100 / S&P 500 by 12-1 month return, with and without the index filter.
  5. Breakout trend-following: buy a stock closing at a 50-day high (above its 200-day average, index above its
     200-day), best 6-month return first, up to 10 positions; sell when it closes below its 20-day low.
  Compared with an equal-weight portfolio of ALL the same stocks, which carries the same bias: what a strategy
  adds on top of that is closer to its real edge.
Every decision uses closes up to day t and trades at the close of day t+1. Costs on every buy and sell.
Periods: "pick" 2016-2022 and "check" 2023-today are shown separately; nothing here was tuned, the settings are
the standard textbook ones fixed before running.
"""
import json
import time
from datetime import date, datetime, timezone

import requests

import regime_engine as R
from run_backtest import CACHE, MARKETS, RESULTS, cols, daily, stats, symbols

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
LONG_CACHE = CACHE / "long"
ETF_COST = 0.001          # per side, ETF / index fund: brokerage + spread
IN_SECTORS = ["^CNXIT", "^NSEBANK", "^CNXPHARMA", "^CNXAUTO", "^CNXFMCG", "^CNXMETAL", "^CNXENERGY", "^CNXREALTY",
              "^CNXINFRA", "^CNXPSUBANK", "^CNXPSE", "^CNXMNC", "^CNXCONSUM"]
US_SECTORS = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]
PICK_END, CHECK_START = date(2022, 12, 31), date(2023, 1, 1)


def long_daily(ysym, start_year=1999):
    LONG_CACHE.mkdir(parents=True, exist_ok=True)
    f = LONG_CACHE / f"{ysym.replace('^', '_')}.json"
    if f.exists() and time.time() - f.stat().st_mtime < 86400:
        return cols(json.loads(f.read_text()))
    p1 = int(datetime(start_year, 1, 1, tzinfo=timezone.utc).timestamp())
    for attempt in range(4):
        r = S.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(ysym)}",
                  params={"interval": "1d", "period1": p1, "period2": int(time.time())}, timeout=30)
        if r.status_code == 429:
            time.sleep(3 + 5 * attempt)
            continue
        r.raise_for_status()
        break
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose") or q["close"]
    rows = []
    for i, t in enumerate(res.get("timestamp") or []):
        o, h, l, c, a = q["open"][i], q["high"][i], q["low"][i], q["close"][i], adj[i]
        if None in (o, h, l, c, a) or c <= 0 or o <= 0:
            continue
        k = a / c
        rows.append([t, o * k, h * k, l * k, a])
    raw = {"tz": res["meta"].get("exchangeTimezoneName", "UTC"), "rows": rows}
    f.write_text(json.dumps(raw))
    time.sleep(0.2)
    return cols(raw)


# ----------------------------------------------------------------------------------------------- helpers
def periods(port, days):
    """Full-period stats plus the pick (to 2022) and check (2023 on) halves."""
    out = stats(port, days)
    for name, keep in (("pick", lambda d: d <= PICK_END), ("check", lambda d: d >= CHECK_START)):
        idx = [i for i, d in enumerate(days) if keep(d)]
        st = stats([port[i] for i in idx], [days[i] for i in idx]) if len(idx) > 20 else None
        out[name] = {k: st[k] for k in ("cagr_pct", "max_dd_pct", "sharpe")} if st else None
    return out


def hold(b, start):
    idx = [i for i in range(1, len(b["d"])) if b["d"][i] >= start]
    return [b["c"][i] / b["c"][i - 1] - 1 for i in idx], [b["d"][i] for i in idx]


def rotation(assets, bench, start, top_n, lookback, skip, cost, use_filter):
    """Monthly: rank by return over `lookback` days ending `skip` days ago; hold the top_n equally from the close
    of the next day. With use_filter, cash while the benchmark is below its 200-day average at the month end."""
    dates = bench["d"]
    pos_of = {s: {d: i for i, d in enumerate(b["d"])} for s, b in assets.items()}
    m200 = R.sma(bench["c"], 200)
    month_ends = [i for i in range(len(dates) - 2) if dates[i].month != dates[i + 1].month]
    port, pdays, held = [], [], set()
    turn_total = n_reb = 0
    for k, mi in enumerate(month_ends):
        if use_filter and not (m200[mi] is not None and bench["c"][mi] > m200[mi]):
            new = set()
        else:
            sc = []
            for s, b in assets.items():
                i = pos_of[s].get(dates[mi])
                if i is None or i < lookback + skip:
                    continue
                sc.append((b["c"][i - skip] / b["c"][i - skip - lookback] - 1, s))
            new = {s for _, s in sorted(sc, reverse=True)[:top_n]}
        turnover = len(held ^ new) / top_n
        end = month_ends[k + 1] + 1 if k + 1 < len(month_ends) else len(dates) - 1
        # day mi+1 still earns the old holdings; the switch happens at that close
        for j in range(mi + 1, end + 1):
            d, dp = dates[j], dates[j - 1]
            h = held if j == mi + 1 else new
            rs = [assets[s]["c"][pos_of[s][d]] / assets[s]["c"][pos_of[s][dp]] - 1
                  for s in h if d in pos_of[s] and dp in pos_of[s]]
            r = sum(rs) / len(h) if h else 0.0   # a missing day counts as flat
            if j == mi + 1 and turnover:
                r -= turnover * cost
            if d >= start:
                port.append(r)
                pdays.append(d)
        if held or new:
            turn_total += turnover
            n_reb += 1
        held = new
    st = periods(port, pdays)
    st["avg_turnover_pct"] = round(turn_total / max(1, n_reb) * 100)
    return st


def breakout(stocks, bench, start, cost, slots=10, hi_n=50, lo_n=20):
    dates = bench["d"]
    m200b = R.sma(bench["c"], 200)
    pre = {}
    for s, b in stocks.items():
        c, h, l = b["c"], b["h"], b["l"]
        m200 = R.sma(c, 200)
        pre[s] = {"i": {d: i for i, d in enumerate(b["d"])}, "c": c, "m200": m200,
                  "hi": [max(h[i - hi_n:i]) if i >= hi_n else None for i in range(len(c))],
                  "lo": [min(l[i - lo_n:i]) if i >= lo_n else None for i in range(len(c))]}
    cash, pos = 1.0, {}
    pend_buy, pend_sell = [], set()
    port, pdays, trades, wins = [], [], 0, 0
    entry_val = {}
    eq_prev = 1.0
    for j in range(1, len(dates)):
        d, dp = dates[j], dates[j - 1]
        for s in list(pos):
            p = pre[s]
            if d in p["i"] and dp in p["i"]:
                pos[s] *= p["c"][p["i"][d]] / p["c"][p["i"][dp]]
        # orders decided yesterday fill at today's close
        for s in pend_sell:
            if s in pos:
                v = pos.pop(s) * (1 - cost)
                trades += 1
                wins += v > entry_val.pop(s, v)
                cash += v
        eq = cash + sum(pos.values())
        for s in pend_buy:
            if len(pos) >= slots or s in pos:
                continue
            amt = min(cash, eq / slots)
            if amt <= eq / slots * 0.5:
                break
            cash -= amt
            pos[s] = amt * (1 - cost)
            entry_val[s] = amt
        pend_buy, pend_sell = [], set()
        eq = cash + sum(pos.values())
        if d >= start:
            port.append(eq / eq_prev - 1)
            pdays.append(d)
        eq_prev = eq
        # decide at today's close
        for s in pos:
            p = pre[s]
            i = p["i"].get(d)
            if i is not None and p["lo"][i] is not None and p["c"][i] < p["lo"][i]:
                pend_sell.add(s)
        free = slots - len(pos) + len(pend_sell)
        if free > 0 and m200b[j] is not None and bench["c"][j] > m200b[j]:
            cand = []
            for s, p in pre.items():
                if s in pos:
                    continue
                i = p["i"].get(d)
                if i is None or i < 200 or p["hi"][i] is None or p["m200"][i] is None:
                    continue
                if p["c"][i] >= p["hi"][i] and p["c"][i] > p["m200"][i]:
                    cand.append((p["c"][i] / p["c"][i - 126] - 1, s))
            pend_buy = [s for _, s in sorted(cand, reverse=True)[:free]]
    st = periods(port, pdays)
    st.update(trades=trades, win_pct=round(wins / trades * 100, 1) if trades else None,
              avg_hold_note="exits on a 20-day low: typically weeks to months")
    return st


def equal_weight_all(stocks, start):
    by_day = {}
    for b in stocks.values():
        for i in range(1, len(b["d"])):
            if b["d"][i] >= start:
                by_day.setdefault(b["d"][i], []).append(b["c"][i] / b["c"][i - 1] - 1)
    days = sorted(by_day)
    return periods([sum(by_day[d]) / len(by_day[d]) for d in days], days)


# ----------------------------------------------------------------------------------------------- main
def line(name, st):
    f = lambda x: "-" if x is None else x
    pk, ck = st.get("pick") or {}, st.get("check") or {}
    print(f"  {name:<52} {f(st['cagr_pct']):>6}% {f(st['max_dd_pct']):>6}% {f(st['sharpe']):>5} | "
          f"{f(pk.get('cagr_pct')):>6}% {f(ck.get('cagr_pct')):>6}%")


def header(title):
    print(f"\n=== {title} ===")
    print(f"  {'':<52} {'per yr':>7} {'worst':>7} {'Shrp':>5} | {'to 2022':>7} {'2023-':>7}")


def main():
    out = {"run_at": datetime.now().isoformat(timespec="seconds"), "pick_end": str(PICK_END), "check_start": str(CHECK_START)}

    # 1. India sectors
    nifty = long_daily("^NSEI", 2007)
    sec = {s: long_daily(s, 2007) for s in IN_SECTORS}
    start_in = date(2012, 7, 1)   # a year after the youngest sector index starts
    header(f"India sector rotation, {start_in} to today (price indices, no dividends)")
    h, hd = hold(nifty, start_in)
    r_in = {"nifty_hold": periods(h, hd)}
    m200 = R.sma(nifty["c"], 200)
    pos = [1 if m200[i] is not None and nifty["c"][i] > m200[i] else 0 for i in range(len(nifty["c"]))]
    rets = R.daily_returns(nifty, pos, ETF_COST)
    idx = [i for i, d in enumerate(nifty["d"]) if d >= start_in]
    r_in["nifty_200"] = periods([rets[i] for i in idx], [nifty["d"][i] for i in idx])
    ew = equal_weight_all(sec, start_in)
    r_in["sectors_equal"] = ew
    for n in (2, 3):
        r_in[f"top{n}_6m_filter"] = rotation(sec, nifty, start_in, n, 126, 0, ETF_COST, True)
        r_in[f"top{n}_6m"] = rotation(sec, nifty, start_in, n, 126, 0, ETF_COST, False)
        r_in[f"top{n}_12_1"] = rotation(sec, nifty, start_in, n, 231, 21, ETF_COST, False)
    for k, v in r_in.items():
        line(k, v)
    out["india_sectors"] = r_in

    # 2. US sectors
    spy = long_daily("SPY", 1999)
    us = {s: long_daily(s, 1999) for s in US_SECTORS}
    start_us = date(2000, 1, 3)
    header(f"US sector rotation, {start_us} to today (dividends included)")
    h, hd = hold(spy, start_us)
    r_us = {"spy_hold": periods(h, hd), "sectors_equal": equal_weight_all(us, start_us)}
    for n in (2, 3):
        r_us[f"top{n}_6m_filter"] = rotation(us, spy, start_us, n, 126, 0, ETF_COST, True)
        r_us[f"top{n}_6m"] = rotation(us, spy, start_us, n, 126, 0, ETF_COST, False)
        r_us[f"top{n}_12_1"] = rotation(us, spy, start_us, n, 231, 21, ETF_COST, False)
    for k, v in r_us.items():
        line(k, v)
    out["us_sectors"] = r_us

    # 3. MTUM vs SPY
    mtum = long_daily("MTUM", 2013)
    start_m = date(2014, 1, 2)
    header(f"MTUM (real momentum ETF) vs SPY, {start_m} to today")
    m_r, m_d = hold(mtum, start_m)
    s_r, s_d = hold(spy, start_m)
    out["mtum"] = {"mtum": periods(m_r, m_d), "spy": periods(s_r, s_d)}
    line("MTUM", out["mtum"]["mtum"])
    line("SPY", out["mtum"]["spy"])

    # 4-5. stock portfolios on today's members
    start = date(2016, 1, 1)
    for key, m in MARKETS.items():
        bench = cols(daily(m["index"], False))
        stocks = {}
        for s in symbols(m):
            try:
                b = cols(daily(s.replace(".", "-") + m["suffix"], False))
            except Exception:
                continue
            if len(b["c"]) >= 300:
                stocks[s] = b
        header(f"{m['name']}, today's {len(stocks)} members, {start} to today (BIASED: compare with 'all stocks')")
        h, hd = hold(bench, start)
        res = {"index_hold": periods(h, hd), "all_stocks_equal": equal_weight_all(stocks, start)}
        res["momentum_top20"] = rotation(stocks, bench, start, 20, 231, 21, m["cost"], False)
        res["momentum_top20_filter"] = rotation(stocks, bench, start, 20, 231, 21, m["cost"], True)
        res["breakout_50_20"] = breakout(stocks, bench, start, m["cost"])
        for k, v in res.items():
            line(k, v)
        bt = res["breakout_50_20"]
        print(f"  breakout: {bt['trades']} trades, {bt['win_pct']}% won")
        out[f"stocks_{key}"] = res

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "swing.json").write_text(json.dumps(out, default=str))
    print("\nsaved results/swing.json")


if __name__ == "__main__":
    main()
