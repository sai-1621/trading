"""Paper crypto trend strategy (futures_lab/PREREG_v2.md, chosen variant): pure logic, no network.

Rule, long only, 19 large Binance USDT perpetuals:
  buy a coin whose daily close (00:00 UTC) is above its 55-day high, only while BTC's close is above its
  200-day average and the coin's 55-day efficiency ratio is at least 0.30; sell when it closes below its 20-day
  low or BTC falls below its 200-day average.
Sizing: inverse volatility, book volatility target 40% a year, max 3x gross, 0.75x per coin.
Fills at the close of the 00:00-01:00 UTC hour; costs 0.10% per side of traded notional; funding on notional held.

The accounting is the research backtest's (search_v1.backtest): each day the account earns the weights held
times the coins' returns, minus funding and trading costs, so a replay matches the research exactly.
"""
import pandas as pd

import search_v1 as S
import search_v2 as S2

RULE = {"market": "btc200", "chop": "er30", "exit": "low20"}
COINS = S.COINS
CAPITAL = 1000.0
COST = S.COST


def target_weights(C, H, L):
    """Daily bars (index = UTC day, columns = coins) -> weights decided at each day's close."""
    raw = S2.trend_v2(C, H, L, RULE["market"], RULE["chop"], RULE["exit"])
    return S.size(raw, C)


def new_state(day):
    return {"start": str(day), "equity": CAPITAL, "weights": {}, "prices": {}, "last_exec_day": None,
            "history": [], "open_trades": {}, "closed": []}


def step(st, exec_day, prices, new_w, funding):
    """One daily fill at exec_day 01:00 UTC.
    prices: {coin: fill price now}; new_w: {coin: target weight}; funding: {coin: sum of funding rates since the
    previous fill}. Returns event strings."""
    ev = []
    old = st["weights"]
    # 1) result of the weights held since the previous fill
    pnl = 0.0
    for c, w in old.items():
        p0, p1 = st["prices"].get(c), prices.get(c)
        if not w or not p0 or not p1:
            continue
        part = w * (p1 / p0 - 1) - w * funding.get(c, 0.0)
        pnl += part
        if c in st["open_trades"]:
            st["open_trades"][c]["pnl_pct"] += part * 100
    # 2) trading cost of moving to the new weights
    coins = set(old) | set(new_w)
    turnover = sum(abs(new_w.get(c, 0.0) - old.get(c, 0.0)) for c in coins)
    cost = turnover * COST
    st["equity"] *= 1 + pnl - cost
    # 3) trade log: entries and exits
    for c in coins:
        was, now = old.get(c, 0.0) > 0, new_w.get(c, 0.0) > 0
        if now and not was:
            st["open_trades"][c] = {"entry_day": str(exec_day), "entry": prices[c], "pnl_pct": 0.0}
            ev.append(f"BUY {c} at {prices[c]:.6g}, weight {new_w[c]:.2f}x")
        elif was and not now:
            t = st["open_trades"].pop(c, None)
            if t:
                t.update({"symbol": c, "exit_day": str(exec_day), "exit": prices.get(c),
                          "price_change_pct": round((prices.get(c, t["entry"]) / t["entry"] - 1) * 100, 2),
                          "pnl_pct": round(t["pnl_pct"], 3)})
                st["closed"].append(t)
                ev.append(f"SELL {c} at {prices.get(c, 0):.6g}, trade {t['pnl_pct']:+.2f}% of the account")
    st["weights"] = {c: round(w, 6) for c, w in new_w.items() if w}
    st["prices"] = {c: prices[c] for c in prices}
    st["last_exec_day"] = str(exec_day)
    st["history"].append([str(exec_day), round(st["equity"], 4), prices.get("BTC"),
                          round(sum(abs(w) for w in new_w.values()), 3)])
    return ev


def weights_on(W, day):
    row = W.loc[day] if day in W.index else None
    return {} if row is None else {c: float(w) for c, w in row.items() if w and not pd.isna(w)}
