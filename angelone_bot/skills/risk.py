"""Risk Manager (trader-risk). PASS or BLOCK, with the reason. A BLOCK is final for that order.
Entries are checked against every limit; exits are never blocked.

The entry it measures from is the HIGHER of the plan's entry and a fresh ask taken just before the order
(trader-risk's rule): a price that ran up while the Time Machine was replaying means a bigger loss at the
same stop and a worse reward:risk, so the order is checked against that.
"""
import config
import costs
import market


def check_entry(order, plan, state, cash, news_risk=None, fresh_price=None):
    s, r = state, []
    stop, target, qty = order["stop"], order["target"], order["qty"]
    e = max(order["entry"], fresh_price or 0)
    t = market.now()
    if not isinstance(qty, int) or qty <= 0:
        r.append(f"format: quantity {qty!r} is not a whole number of shares above 0")
    if order["symbol"] in config.BLOCKED:
        r.append("blocked stock")
    if news_risk:
        r.append(f"event risk in today's news: {', '.join(news_risk)}")
    if not stop or stop >= e:
        r.append("no stop, or stop not below the entry")
    if not target or target <= e:
        r.append("target not above the entry (the move already happened)")
    value = qty * e
    max_loss = qty * (e - stop) + costs.round_trip(value, qty * stop) if e > stop else float("inf")
    if max_loss > config.MAX_LOSS_PER_TRADE + 1e-9:
        r.append(f"loss at stop {max_loss:.2f} (from entry {e}) over max {config.MAX_LOSS_PER_TRADE}")
    rr = (target - e) / (e - stop) if e > stop else 0
    if rr < config.MIN_REWARD_RISK:
        r.append(f"reward:risk {rr:.2f} (from entry {e}) under {config.MIN_REWARD_RISK}")
    risked = sum(x["max_loss"] for x in s["entries_today"])
    if risked + max_loss > config.MAX_DAILY_LOSS + 1e-9:
        r.append(f"daily loss limit: {risked:.0f} already risked + {max_loss:.0f} > {config.MAX_DAILY_LOSS:.0f}")
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
    if value / config.LEVERAGE > cash - buffer:
        r.append("would break the cash buffer")
    if value > config.MAX_ORDER + 1e-9:
        r.append("over max order size")
    if value < config.MIN_ORDER:
        r.append(f"order value {value:.0f} under the minimum {config.MIN_ORDER:.0f}")
    if value > config.BUDGET * config.MAX_POSITION_PCT / 100 * config.LEVERAGE + 1e-9:
        r.append(f"position over {config.MAX_POSITION_PCT:g}% of the budget")
    v = {"verdict": "BLOCK" if r else "PASS", "reasons": r, "order": order, "checked_entry": e,
         "max_loss": round(max_loss, 2), "reward_risk": round(rr, 3)}
    s["risk_log"] = (s["risk_log"] + [{"time": t.isoformat(), "symbol": order["symbol"], "verdict": v["verdict"], "reasons": r}])[-200:]
    return v
