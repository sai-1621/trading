"""Risk Manager (trader-risk). PASS or BLOCK, with the reason. A BLOCK is final for that order.
Entries are checked against every limit; exits are never blocked.
"""
import config
import market


def check_entry(order, plan, state, cash, news_risk=None):
    s, r = state, []
    e, stop, target = order["entry"], order["stop"], order["target"]
    t = market.now()
    if order["symbol"] in config.BLOCKED:
        r.append("blocked stock")
    if news_risk:
        r.append(f"event risk in today's news: {', '.join(news_risk)}")
    if not stop or stop >= e:
        r.append("no stop, or stop not below the entry")
    if order["max_loss"] > config.MAX_LOSS_PER_TRADE + 1e-9:
        r.append(f"loss at stop {order['max_loss']} over max {config.MAX_LOSS_PER_TRADE}")
    rr = (target - e) / (e - stop) if e > stop else 0
    if rr < config.MIN_REWARD_RISK:
        r.append(f"reward:risk {rr:.2f} under {config.MIN_REWARD_RISK}")
    risked = sum(x["max_loss"] for x in s["entries_today"])
    if risked + order["max_loss"] > config.MAX_DAILY_LOSS + 1e-9:
        r.append(f"daily loss limit: {risked:.0f} already risked + {order['max_loss']:.0f} > {config.MAX_DAILY_LOSS:.0f}")
    if len(s["entries_today"]) >= config.MAX_TRADES_PER_DAY:
        r.append(f"{config.MAX_TRADES_PER_DAY} trades already today")
    if s["orders_today"] >= config.MAX_ORDERS_PER_DAY:
        r.append(f"{config.MAX_ORDERS_PER_DAY} orders already today")
    if len(s["positions"]) >= config.MAX_OPEN_POSITIONS:
        r.append(f"{config.MAX_OPEN_POSITIONS} positions already open")
    if order["symbol"] in s["positions"] or any(x["symbol"] == order["symbol"] for x in s["entries_today"]):
        r.append("duplicate: already traded this stock today")
    if not market.is_open(t):
        r.append("market closed")
    elif not (market.hhmm(config.NO_ENTRIES_BEFORE) <= market.minutes(t) <= market.hhmm(config.NO_ENTRIES_AFTER)):
        r.append("outside the entry window")
    buffer = config.BUDGET * config.CASH_BUFFER_PCT / 100
    if order["value"] / config.LEVERAGE > cash - buffer:
        r.append("would break the cash buffer")
    if order["value"] > config.MAX_ORDER + 1e-9:
        r.append("over max order size")
    v = {"verdict": "BLOCK" if r else "PASS", "reasons": r, "order": order}
    s["risk_log"] = (s["risk_log"] + [{"time": t.isoformat(), "symbol": order["symbol"], "verdict": v["verdict"], "reasons": r}])[-200:]
    return v
