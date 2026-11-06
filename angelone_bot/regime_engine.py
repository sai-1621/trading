"""Market-condition (regime) engine, shared by the CoinDCX and Angel One bots.

Every day is labelled with the market's condition, using only what was known at the previous close:
    trend:       the benchmark (BTC or NIFTY) above / below its 100-day average   -> bull / bear
    volatility:  its 20-day volatility above / below its median of the past year  -> volatile / calm
giving four regimes: bull_calm, bull_volatile, bear_calm, bear_volatile.

A pool of daily strategies (including "hold" and "cash") is scored in each regime, and the bot uses the
best one for the regime it is in, or stays in cash when nothing made money in that regime.

Walk-forward testing (the honest way to "train" it): train only on the past, trade the next quarter,
then retrain with that quarter added, and repeat. Every reported number comes from quarters the model
had NOT seen when it chose. Orders fill at the next day's open; costs are paid on every change.
"""
import math

REGIMES = ["bull_calm", "bull_volatile", "bear_calm", "bear_volatile"]


# ---------------- indicators ----------------
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


# ---------------- regimes ----------------
def regime_labels(bench, trend_ma=100, vol_win=20, vol_lookback=250):
    """{date: regime} for the day TRADED, using the benchmark's closes up to the day before."""
    c, d = bench["c"], bench["d"]
    m = sma(c, trend_ma)
    rets = [0.0] + [math.log(c[i] / c[i - 1]) for i in range(1, len(c))]
    vol = [None] * len(c)
    for i in range(vol_win, len(c)):
        w = rets[i - vol_win + 1:i + 1]
        mu = sum(w) / vol_win
        vol[i] = math.sqrt(sum((r - mu) ** 2 for r in w) / (vol_win - 1))
    out = {}
    for i in range(len(c) - 1):
        if m[i] is None or vol[i] is None:
            continue
        past = sorted(v for v in vol[max(0, i - vol_lookback):i + 1] if v is not None)
        if len(past) < 60:
            continue
        med = past[len(past) // 2]
        trend = "bull" if c[i] > m[i] else "bear"
        out[d[i + 1]] = f"{trend}_{'volatile' if vol[i] > med else 'calm'}"
    return out


# ---------------- strategies: desired position (0/1) after each day's close ----------------
def _stateful(n, enter, exit_, max_bars=None):
    pos, held, out = 0, 0, []
    for i in range(n):
        if pos:
            held += 1
            if exit_[i] or (max_bars and held >= max_bars):
                pos, held = 0, 0
        elif enter[i]:
            pos, held = 1, 0
        out.append(pos)
    return out


def strategies(b, bench_up):
    """{name: positions} for one market. bench_up: {date: benchmark above its 100-day average (known that day)}."""
    c, d, n = b["c"], b["d"], len(b["c"])
    out = {"cash": [0] * n, "hold": [1] * n}
    for ma in (20, 50, 100):
        m = sma(c, ma)
        out[f"ma{ma}"] = [1 if m[i] is not None and c[i] > m[i] else 0 for i in range(n)]
    for e, x in ((20, 10), (55, 20)):
        hi = [None if i < e else max(c[i - e:i]) for i in range(n)]
        lo = [None if i < x else min(c[i - x:i]) for i in range(n)]
        out[f"donchian{e}_{x}"] = _stateful(n, [hi[i] is not None and c[i] > hi[i] for i in range(n)],
                                            [lo[i] is not None and c[i] < lo[i] for i in range(n)])
    r2, m5, m200 = rsi(c, 2), sma(c, 5), sma(c, 200)
    out["dip_rsi2"] = _stateful(n, [r2[i] is not None and m200[i] is not None and c[i] > m200[i] and r2[i] < 10 for i in range(n)],
                                [m5[i] is not None and c[i] > m5[i] for i in range(n)], max_bars=10)
    out["dip_rsi2_any"] = _stateful(n, [r2[i] is not None and r2[i] < 5 for i in range(n)],
                                    [m5[i] is not None and c[i] > m5[i] for i in range(n)], max_bars=10)
    return out


# ---------------- simulation ----------------
def daily_returns(b, pos, cost, tax=0.0):
    """Daily returns of following `pos` (decided at each close, executed at the next open).
    cost: per side (fee + half spread). tax: on each profitable round trip at exit (crypto in India: 0.30)."""
    o, c, n = b["o"], b["c"], len(b["c"])
    rets = [0.0] * n
    held, trade_mult = 0, 1.0
    for t in range(1, n):
        want = pos[t - 1]
        r = 1.0
        if want and not held:            # buy at today's open
            r = (1 - cost) * c[t] / o[t]
            trade_mult = r
        elif want and held:              # keep holding
            r = c[t] / c[t - 1]
            trade_mult *= r
        elif not want and held:          # sell at today's open
            r = o[t] / c[t - 1] * (1 - cost)
            trade_mult *= r
            if tax and trade_mult > 1:   # pay tax on the round trip's gain
                gain = trade_mult - 1
                r *= (trade_mult - tax * gain) / trade_mult
        held = want
        rets[t] = r - 1
    return rets


def equity_stats(rets):
    eq, peak, dd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        dd = max(dd, 1 - eq / peak)
    return eq - 1, dd


# ---------------- walk-forward ----------------
def walk_forward(markets, bench, cost_of, tax=0.0, start_frac=0.4, step=63, min_days=30):
    """markets: {name: cols}; cost_of(name) -> cost per side. Returns the out-of-sample report."""
    labels = regime_labels(bench)
    bc, bm = bench["c"], sma(bench["c"], 100)
    bench_up = {bench["d"][i + 1]: bc[i] > bm[i] for i in range(len(bc) - 1) if bm[i] is not None}

    # every strategy's positions and daily returns, per market, on the full history (each is causal on its own)
    srets, spos, dates_of = {}, {}, {}
    for name, b in markets.items():
        cost = cost_of(name)
        spos[name] = strategies(b, bench_up)
        srets[name] = {s: daily_returns(b, p, cost, tax) for s, p in spos[name].items()}
        dates_of[name] = b["d"]
    snames = list(next(iter(srets.values())).keys())
    switch_pos = {name: [0] * len(markets[name]["c"]) for name in markets}
    test_idx = {name: [] for name in markets}

    all_dates = sorted(d for d in labels)
    start = int(len(all_dates) * start_frac)
    oos = {name: [] for name in markets}          # (date, return) for the switcher
    bh = {name: [] for name in markets}
    choices = []
    for k in range(start, len(all_dates), step):
        train_end, test = all_dates[k], all_dates[k:k + step]
        # score each (regime, strategy) on the past only: mean daily return across market-days
        score = {r: {s: [0.0, 0] for s in snames} for r in REGIMES}
        for name in markets:
            for i, d in enumerate(dates_of[name]):
                if d >= train_end:
                    break
                rg = labels.get(d)
                if rg:
                    for s in snames:
                        sc = score[rg][s]
                        sc[0] += srets[name][s][i]
                        sc[1] += 1
        pick = {}
        for rg in REGIMES:
            best, best_v = "cash", 0.0
            for s in snames:
                tot, cnt = score[rg][s]
                if cnt >= min_days and tot / cnt > best_v:
                    best, best_v = s, tot / cnt
            pick[rg] = best
        choices.append({"from": str(test[0]), "to": str(test[-1]), "pick": pick})
        test_set = set(test)
        for name in markets:
            for i, d in enumerate(dates_of[name]):
                if i > 0 and d in test_set and labels.get(d):
                    # what to hold today was decided at yesterday's close: the chosen strategy for today's regime
                    chosen = spos[name].get(pick[labels[d]])  # "cash" -> no position
                    switch_pos[name][i - 1] = chosen[i - 1] if chosen else 0
                    test_idx[name].append(i)
    # the switcher's OWN trades, so every switch between strategies pays its costs (and tax)
    for name, b in markets.items():
        rets = daily_returns(b, switch_pos[name], cost_of(name), tax)
        for i in test_idx[name]:
            oos[name].append((dates_of[name][i], rets[i]))
            bh[name].append((dates_of[name][i], srets[name]["hold"][i]))
    return _report(oos, bh, choices, all_dates[start])


def _report(oos, bh, choices, oos_start):
    rows = []
    for name in oos:
        if len(oos[name]) < 60:
            continue
        ret, dd = equity_stats([r for _, r in oos[name]])
        bret, bdd = equity_stats([r for _, r in bh[name]])
        rows.append({"market": name, "ret": ret, "dd": dd, "bh": bret, "bh_dd": bdd, "days": len(oos[name]),
                     "in_market_pct": sum(1 for _, r in oos[name] if r != 0) / len(oos[name])})
    if not rows:
        return None
    n = len(rows)
    rets = sorted(r["ret"] for r in rows)
    # equal-weight portfolio of the markets that have data on each day
    by_day, bh_day = {}, {}
    for name in oos:
        for d, r in oos[name]:
            by_day.setdefault(d, []).append(r)
        for d, r in bh[name]:
            bh_day.setdefault(d, []).append(r)
    port = [sum(v) / len(v) for _, v in sorted(by_day.items())]
    bport = [sum(v) / len(v) for _, v in sorted(bh_day.items())]
    pret, pdd = equity_stats(port)
    bpret, bpdd = equity_stats(bport)
    years = {}
    for (d, v), (_, bv) in zip(sorted(by_day.items()), sorted(bh_day.items())):
        y = str(d)[:4]
        years.setdefault(y, [[], []])
        years[y][0].append(sum(v) / len(v))
        years[y][1].append(sum(bv) / len(bv))
    return {
        "oos_start": str(oos_start), "markets": n,
        "avg_ret_pct": round(sum(rets) / n * 100, 1), "median_ret_pct": round(rets[n // 2] * 100, 1),
        "markets_up_pct": round(sum(r > 0 for r in rets) / n * 100),
        "bh_avg_ret_pct": round(sum(r["bh"] for r in rows) / n * 100, 1),
        "portfolio_ret_pct": round(pret * 100, 1), "portfolio_dd_pct": round(pdd * 100, 1),
        "bh_portfolio_ret_pct": round(bpret * 100, 1), "bh_portfolio_dd_pct": round(bpdd * 100, 1),
        "by_year": {y: {"bot_pct": round(equity_stats(a)[0] * 100, 1), "hold_pct": round(equity_stats(b)[0] * 100, 1)}
                    for y, (a, b) in sorted(years.items())},
        "choices": choices, "per_market": rows,
    }


def verdict(rep):
    """PASS only if the out-of-sample portfolio made money, at least half the markets made money, and it beat
    buy-and-hold on return, or on return per unit of drawdown while losing less in the worst fall."""
    if not rep:
        return False, "not enough data"
    p, pdd, b, bdd = rep["portfolio_ret_pct"], rep["portfolio_dd_pct"], rep["bh_portfolio_ret_pct"], rep["bh_portfolio_dd_pct"]
    calmar = p / pdd if pdd else 0
    bcalmar = b / bdd if bdd else 0
    beats = p > b or (calmar > bcalmar and pdd < bdd)
    ok = p > 0 and rep["markets_up_pct"] >= 50 and beats
    why = (f"portfolio {p:+.1f}% (worst fall {pdd:.1f}%) vs buy-and-hold {b:+.1f}% (worst fall {bdd:.1f}%), "
           f"{rep['markets_up_pct']}% of markets up")
    return ok, why
