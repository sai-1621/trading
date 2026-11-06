"""Two low-turnover approaches, Jan 2016 to today, on the same data as run_backtest.py.

1. Index with a crash filter: hold the index (NIFTY 50 / SPY) while it closes above its 200-day average,
   otherwise sit in cash; trades at the next open.
2. Monthly momentum: at each month end, rank the stocks by their return over the last 12 months excluding the
   most recent month (the standard "12-1" measure, fixed in advance, not tuned here) and hold the top N in
   equal weight for the next month. Optionally only while the index is above its 200-day average.
   It decides from data up to a month old, so trading at that month-end close needs no future data.
   Costs are charged on what actually changes at each rebalance.

    python momentum_test.py
"""
import json
from datetime import date

import regime_engine as R
from run_backtest import MARKETS, START_TEST, symbols, daily, cols, stats

INDEX_COST = 0.001   # ETF: brokerage + spread per side (roughly)


def index_filter(bench, cost):
    pos = [0] * len(bench["c"])
    m200 = R.sma(bench["c"], 200)
    for i in range(len(pos)):
        pos[i] = 1 if m200[i] is not None and bench["c"][i] > m200[i] else 0
    rets = R.daily_returns(bench, pos, cost)
    idx = [i for i, d in enumerate(bench["d"]) if d >= START_TEST]
    hold = [bench["c"][i] / bench["c"][i - 1] - 1 for i in idx]
    return (stats([rets[i] for i in idx], [bench["d"][i] for i in idx]), stats(hold, [bench["d"][i] for i in idx]),
            round(sum(pos[i] for i in idx) / len(idx) * 100))


def momentum(stocks, bench, cost, top_n, use_filter):
    dates = bench["d"]
    pos_of = {s: {d: i for i, d in enumerate(b["d"])} for s, b in stocks.items()}
    m200 = R.sma(bench["c"], 200)
    month_ends = [i for i in range(len(dates) - 1) if dates[i].month != dates[i + 1].month and dates[i] >= date(2015, 12, 1)]
    port, pdays, held, turnover_total, rebalances = [], [], set(), 0.0, 0
    for k, mi in enumerate(month_ends):
        d0 = dates[mi]
        if use_filter and not (m200[mi] is not None and bench["c"][mi] > m200[mi]):
            new = set()
        else:
            scores = []
            for s, b in stocks.items():
                i = pos_of[s].get(d0)
                if i is None or i < 252:
                    continue
                c = b["c"]
                scores.append((c[i - 21] / c[i - 252] - 1, s))
            new = {s for _, s in sorted(scores, reverse=True)[:top_n]}
        changed = len(held ^ new)
        if held or new:
            turnover = changed / max(1, top_n)   # fraction of the portfolio bought + sold
            turnover_total += turnover
            rebalances += 1
        held = new
        end = month_ends[k + 1] if k + 1 < len(month_ends) else len(dates) - 1
        first = True
        for j in range(mi + 1, end + 1):
            d, dp = dates[j], dates[j - 1]
            if d < START_TEST and dp < START_TEST:
                continue
            rs = []
            for s in held:
                b, p = stocks[s], pos_of[s]
                if d in p and dp in p:
                    rs.append(b["c"][p[d]] / b["c"][p[dp]] - 1)
            r = sum(rs) / len(rs) if rs else 0.0
            if first:
                r -= turnover * cost if (held or changed) else 0
                first = False
            if d >= START_TEST:
                port.append(r)
                pdays.append(d)
    st = stats(port, pdays)
    st["rebalances"] = rebalances
    st["avg_turnover_pct"] = round(turnover_total / max(1, rebalances) * 100)
    return st


def main():
    out = {}
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
        f, h, invested = index_filter(bench, INDEX_COST)
        ew = [sum(v) / len(v) for v in []]
        res = {"index_hold": h, "index_200": f, "index_200_invested_pct": invested}
        for n in (10, 20):
            for filt in (False, True):
                res[f"mom_top{n}{'_filtered' if filt else ''}"] = momentum(stocks, bench, m["cost"], n, filt)
        out[key] = res
        print(f"\n=== {m['name']} ({len(stocks)} stocks), Jan 2016 to today: per year | worst fall | Sharpe ===")
        print(f"  Index, buy and hold                     {h['cagr_pct']:>6}% | {h['max_dd_pct']:>5}% | {h['sharpe']}")
        print(f"  Index, 200-day crash filter             {f['cagr_pct']:>6}% | {f['max_dd_pct']:>5}% | {f['sharpe']}   (invested {invested}% of days)")
        for k, v in res.items():
            if k.startswith("mom"):
                print(f"  Momentum {k[7:]:<30} {v['cagr_pct']:>6}% | {v['max_dd_pct']:>5}% | {v['sharpe']}   "
                      f"(changes {v['avg_turnover_pct']}% of holdings a month)")
        print("  By year, index vs momentum top 20:", ", ".join(
            f"{y}: {h['by_year'].get(y)}% / {res['mom_top20']['by_year'].get(y)}%" for y in res["mom_top20"]["by_year"]))
    with open("results/momentum.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, default=str)


if __name__ == "__main__":
    main()
