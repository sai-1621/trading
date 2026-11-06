"""Backtest of the monthly ETF SIP, January 2016 to today, with the same rules as the bot (portfolio.py).

    python backtest.py

Each month, on the first trading day on or after SIP_DAY, the SIP amount buys whole units at that day's open
(plus half a spread and Angel One's delivery charges). Money that doesn't buy a whole unit waits for next month.
Nothing is ever sold. Prices include dividends. Tax is not included (none is due until you sell).

Results (trader/backtest.json):
  XIRR            the yearly return on the money you actually put in, month by month (the right measure for a SIP)
  CAGR / worst    the return and the worst fall of the investments themselves, ignoring when money was added
  start-year      the same SIP started in each year from 2016, to show how much the start date matters
"""
import json
from datetime import date

import config
import data
from portfolio import plan_buys, xirr

START = date(2016, 1, 1)
SCENARIOS = [
    ("nifty", "NIFTY 50 (NIFTYBEES)", {"NIFTYBEES": 1.0}, False),
    ("nifty_us", "NIFTY 50 70% + Nasdaq 100 30%", {"NIFTYBEES": 0.7, "MON100": 0.3}, False),
    ("nifty_us_gold", "NIFTY 50 60% + Nasdaq 100 25% + Gold 15%", {"NIFTYBEES": 0.6, "MON100": 0.25, "GOLDBEES": 0.15}, False),
    ("nifty_next50", "NIFTY 50 50% + NIFTY Next 50 50%", {"NIFTYBEES": 0.5, "JUNIORBEES": 0.5}, False),
    ("nifty_pause", "NIFTY 50, paused while below its 200-day average", {"NIFTYBEES": 1.0}, True),
]


def load(symbols):
    px = {}
    for s in symbols:
        px[s] = {d: (o, c) for d, o, c in data.daily(s)}
    return px


def simulate(weights, pause, px, nifty, start=START, amount=None):
    amount = amount or config.SIP_AMOUNT
    cal = sorted(d for d in px["NIFTYBEES"] if d >= start and all(d in px[e] for e in weights))
    n_dates = sorted(nifty)
    n_close = [nifty[d] for d in n_dates]
    above = {}
    for i in range(200, len(n_dates)):
        above[n_dates[i]] = n_close[i - 1] > sum(n_close[i - 200:i]) / 200   # yesterday's close vs its 200-day average
    hold = {e: 0 for e in weights}
    cash, invested, flows, charges = 0.0, 0.0, [], 0.0
    prev_value, twr, month_done, paused_months, curve, buys_log = None, [], None, 0, [], []
    for d in cal:
        contrib = 0.0
        key = (d.year, d.month)
        if key != month_done and d.day >= min(config.SIP_DAY, 28):
            month_done = key
            cash += amount
            invested += amount
            contrib = amount
            flows.append((d, -amount))
            if pause and not above.get(d, True):
                paused_months += 1
            else:
                prices = {e: px[e][d][0] * (1 + config.ASSUMED_SPREAD_PCT / 200) for e in weights}
                for e, u in plan_buys(cash, hold, prices, weights).items():
                    cost = u * prices[e]
                    from portfolio import buy_charges
                    ch = buy_charges(cost)
                    hold[e] += u
                    cash -= cost + ch
                    charges += ch
                    buys_log.append((d, e, u))
        value = cash + sum(hold[e] * px[e][d][1] for e in weights)
        if prev_value:
            twr.append((value - contrib) / prev_value - 1)
        prev_value = value
        if not curve or curve[-1][0][:7] != d.isoformat()[:7]:
            curve.append([d.isoformat(), round(invested), round(value)])
        else:
            curve[-1] = [d.isoformat(), round(invested), round(value)]
    flows.append((cal[-1], value))
    eq, peak, dd, by_year, ydays = 1.0, 1.0, 0.0, {}, {}
    for d, r in zip(cal[1:], twr):
        eq *= 1 + r
        peak, dd = max(peak, eq), max(dd, 1 - eq / peak)
        by_year[d.year] = by_year.get(d.year, 1.0) * (1 + r)
    years = (cal[-1] - cal[0]).days / 365.25
    x = xirr(flows)
    return {"invested": round(invested), "value": round(value), "gain": round(value - invested),
            "gain_pct": round((value / invested - 1) * 100, 1), "xirr_pct": round(x * 100, 1) if x is not None else None,
            "cagr_pct": round((eq ** (1 / years) - 1) * 100, 1), "worst_fall_pct": round(dd * 100, 1),
            "charges": round(charges), "cash_left": round(cash), "paused_months": paused_months,
            "units": hold, "by_year": {y: round((v - 1) * 100, 1) for y, v in sorted(by_year.items())},
            "curve": curve, "from": cal[0].isoformat(), "to": cal[-1].isoformat()}


def main():
    syms = sorted({e for _, _, w, _ in SCENARIOS for e in w})
    px = load(syms)
    nifty = {d: c for d, _, c in data.daily("^NSEI")}
    out = {"run_at": date.today().isoformat(), "sip_amount": config.SIP_AMOUNT, "scenarios": {}, "start_years": {}}
    print(f"Monthly SIP of Rs {config.SIP_AMOUNT:,.0f} from January 2016, whole units, after charges, before tax\n")
    print(f"  {'Portfolio':<50} {'Invested':>10} {'Value now':>11} {'XIRR':>6} {'CAGR':>6} {'Worst fall':>10}")
    for key, name, w, pause in SCENARIOS:
        r = simulate(w, pause, px, nifty)
        r["name"], r["weights"] = name, w
        out["scenarios"][key] = r
        print(f"  {name:<50} {r['invested']:>10,} {r['value']:>11,} {r['xirr_pct']:>5}% {r['cagr_pct']:>5}% {r['worst_fall_pct']:>9}%"
              + (f"  (paused {r['paused_months']} months)" if pause else ""))
    print("\n  The NIFTY 50 SIP started in different years (to today):")
    for y in range(2016, date.today().year):
        r = simulate({"NIFTYBEES": 1.0}, False, px, nifty, start=date(y, 1, 1))
        out["start_years"][y] = {"invested": r["invested"], "value": r["value"], "xirr_pct": r["xirr_pct"]}
        print(f"    from {y}: invested {r['invested']:>9,}, now {r['value']:>9,}, XIRR {r['xirr_pct']}%")
    (config.TRADER_DIR / "backtest.json").write_text(json.dumps(out, default=str), encoding="utf-8")
    print("\nSaved trader/backtest.json")


if __name__ == "__main__":
    main()
