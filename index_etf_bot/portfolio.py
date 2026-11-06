"""The SIP rules, shared by the backtest and the live bot so both behave exactly the same.

Each month the SIP amount (plus any cash left over) buys whole ETF units. New money goes first to whatever is
furthest below its target weight, so the portfolio stays balanced without ever selling (no tax, no exit costs).
"""
import math

import config


def buy_charges(value):
    """Rupees charged on one delivery buy order of `value` rupees."""
    if value <= 0:
        return 0.0
    brokerage = max(config.BROKERAGE_MIN, min(config.BROKERAGE_MAX, value * config.BROKERAGE_PCT / 100))
    txn = value * config.EXCHANGE_TXN_PCT / 100
    sebi = value * config.SEBI_FEE_PCT / 100
    stamp = value * config.STAMP_BUY_PCT / 100
    return brokerage + txn + sebi + stamp + (brokerage + txn + sebi) * config.GST_PCT / 100


def plan_buys(cash, holdings, prices, weights):
    """cash: rupees to invest; holdings: {etf: units}; prices: {etf: price to pay}; weights: {etf: target share}.
    Returns {etf: units} to buy, whole units, keeping charges inside `cash`."""
    value = {e: holdings.get(e, 0) * prices[e] for e in weights}
    total = sum(value.values()) + cash
    gap = {e: max(0.0, weights[e] * total - value[e]) for e in weights}
    gap_sum = sum(gap.values()) or 1.0
    alloc = {e: cash * gap[e] / gap_sum for e in weights}       # money to each, by how far below target it is
    buys = {}
    for e in sorted(weights, key=lambda e: -alloc[e]):
        money = alloc[e]
        units = math.floor(money / prices[e])
        while units > 0 and units * prices[e] + buy_charges(units * prices[e]) > money:
            units -= 1
        if units > 0:
            buys[e] = units
    # leftover money: one more unit of whatever is most underweight, if it fits
    spent = sum(u * prices[e] + buy_charges(u * prices[e]) for e, u in buys.items())
    left = cash - spent
    for e in sorted(weights, key=lambda e: -(gap[e] - buys.get(e, 0) * prices[e])):
        extra_cost = prices[e] + buy_charges((buys.get(e, 0) + 1) * prices[e]) - buy_charges(buys.get(e, 0) * prices[e])
        if extra_cost <= left:
            buys[e] = buys.get(e, 0) + 1
            left -= extra_cost
            break
    return buys


def xirr(flows):
    """flows: [(date, amount)], money in negative, value out positive. Annual rate, or None."""
    if not flows or all(a >= 0 for _, a in flows) or all(a <= 0 for _, a in flows):
        return None
    t0 = flows[0][0]

    def npv(r):
        return sum(a / (1 + r) ** ((d - t0).days / 365.25) for d, a in flows)

    lo, hi = -0.99, 10.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2
