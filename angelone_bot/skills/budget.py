"""Budget Manager (trader-budget). Whole shares; the size is the smallest of four limits:

  max loss      MAX_LOSS_PER_TRADE / (risk per share + charges per share)
  position cap  BUDGET x MAX_POSITION_PCT x LEVERAGE / entry
  max order     MAX_ORDER / entry
  cash          (cash minus the buffer) x LEVERAGE / entry
A stop too far for the max loss is a refusal, not a smaller order.
"""
import math

import config
import costs


def size(plan, cash):
    e, stop = plan["entry"], plan["stop"]
    risk = e - stop
    buffer = config.BUDGET * config.CASH_BUFFER_PCT / 100
    usable = max(0.0, min(cash, config.BUDGET) - buffer)
    limits = {
        "position cap": config.BUDGET * config.MAX_POSITION_PCT / 100 * config.LEVERAGE / e,
        "max order": config.MAX_ORDER / e,
        "cash": usable * config.LEVERAGE / e,
    }
    # charges depend on the order size, so find the largest share count whose loss incl. charges fits
    n = int(config.MAX_LOSS_PER_TRADE / risk) + 1
    while n > 0 and n * risk + costs.round_trip(n * e, n * stop) > config.MAX_LOSS_PER_TRADE:
        n -= 1
    limits["max loss"] = n
    decided_by = min(limits, key=limits.get)
    qty = int(math.floor(limits[decided_by]))
    value = qty * e
    max_loss = qty * risk + costs.round_trip(value, qty * stop)
    out = {"symbol": plan["symbol"], "side": "buy", "order_type": "MARKET", "product": config.PRODUCT, "qty": qty,
           "entry": e, "value": round(value, 2), "stop": stop, "target": plan["target"],
           "max_loss": round(max_loss, 2), "charges_round_trip": round(costs.round_trip(value, value), 2),
           "decided_by": decided_by, "limits": {k: int(v) for k, v in limits.items()}}
    if qty <= 0:
        return {**out, "ok": False, "reason": f"0 shares ({decided_by} decided it)"}
    if value < config.MIN_ORDER:
        return {**out, "ok": False, "reason": f"order value {value:.0f} under the minimum {config.MIN_ORDER:.0f}"}
    return {**out, "ok": True}
