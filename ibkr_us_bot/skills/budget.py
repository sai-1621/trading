"""Budget Manager (trader-budget), as in the original pack. Whole shares; the size is the smallest of four limits:
  max loss      MAX_LOSS_PER_TRADE / (risk per share), after IBKR's charges
  position cap  BUDGET x MAX_POSITION_PCT / entry
  max order     MAX_ORDER / entry
  cash          (cash minus the buffer) / entry
A stop too far for the max loss is a refusal (0 shares), not a smaller order.
"""
import config
import costs


def size(plan, cash):
    e, stop = plan["entry"], plan["stop"]
    risk = e - stop
    buffer = config.BUDGET * config.CASH_BUFFER_PCT / 100
    usable = max(0.0, min(cash, config.BUDGET) - buffer)
    limits = {"position cap": config.BUDGET * config.MAX_POSITION_PCT / 100 / e,
              "max order": config.MAX_ORDER / e, "cash": usable / e}
    n = int(config.MAX_LOSS_PER_TRADE / risk) + 1
    while n > 0 and n * risk + costs.round_trip(n * e, n * stop, n) > config.MAX_LOSS_PER_TRADE:
        n -= 1
    limits["max loss"] = n
    decided_by = min(limits, key=limits.get)
    qty = int(limits[decided_by])
    value = qty * e
    max_loss = qty * risk + (costs.round_trip(value, qty * stop, qty) if qty else 0)
    out = {"symbol": plan["symbol"], "side": "buy", "order_type": "MKT", "tif": "DAY", "qty": qty, "entry": e,
           "value": round(value, 2), "stop": stop, "target": plan["target"], "max_loss": round(max_loss, 2),
           "charges_round_trip": round(costs.round_trip(value, value, qty), 2) if qty else 0,
           "decided_by": decided_by, "limits": {k: int(v) for k, v in limits.items()}}
    if qty <= 0:
        return {**out, "ok": False, "reason": f"0 shares ({decided_by} decided it)"}
    if value < config.MIN_ORDER:
        return {**out, "ok": False, "reason": f"order ${value:.0f} under the minimum ${config.MIN_ORDER:.0f}"}
    return {**out, "ok": True}
