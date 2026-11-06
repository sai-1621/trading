"""Order Executor (trader-broker), automated, for Angel One.

The Risk Manager's PASS is the approval; the order goes to the paper account by default, and to Angel One
(intraday MIS market order) only when LIVE_TRADING=true. Stops and targets are watched by the bot
(alerts.py), so the bot must be running while a trade is open; if it is not, Angel One squares off
intraday positions itself near the close (and charges extra for it).
"""
import json
import logging

import config
import costs
import notifier
from market import now

log = logging.getLogger("bot")


def cash_available(angel, state):
    return angel.available_cash() if config.LIVE_TRADING else state["paper_balance"]


def buy(angel, state, order, plan, ask):
    state["orders_today"] += 1
    qty = order["qty"]
    if config.LIVE_TRADING:
        res = angel.market_order(order["symbol"], "buy", qty, tag="bot-entry")
        if res["status"] != "complete" or res["filled"] <= 0:
            log.error("BROKER: buy %s not filled: %s %s", order["symbol"], res["status"], res["text"])
            notifier.notify(f"Buy {order['symbol']} NOT filled: {res['status']} {res['text']}")
            return None
        price, qty = res["avg_price"], res["filled"]
    else:
        price = ask
        state["paper_balance"] -= qty * price / config.LEVERAGE + costs.order_charges(qty * price, "buy")

    pos = {"symbol": order["symbol"], "qty": qty, "entry": price, "stop": plan["stop"], "target": plan["target"],
           "setup": plan["setup"], "max_loss": order["max_loss"], "opened_at": now().isoformat(),
           "day": now().date().isoformat(), "risk_per_share": price - plan["stop"]}
    state["positions"][order["symbol"]] = pos
    state["entries_today"].append({"symbol": order["symbol"], "max_loss": order["max_loss"]})
    state["history"].append({"time": pos["opened_at"], "side": "BUY", "symbol": order["symbol"], "qty": qty,
                             "price": price, "stop": plan["stop"], "target": plan["target"], "setup": plan["setup"]})
    msg = f"BUY {qty} {order['symbol']} @ {price:.2f} | stop {plan['stop']} target {plan['target']} | max loss Rs {order['max_loss']:.0f}"
    log.info("BROKER: %s [%s]", msg, "LIVE" if config.LIVE_TRADING else "PAPER")
    notifier.notify(msg)
    return pos


def sell(angel, state, pos, bid, reason):
    state["orders_today"] += 1
    qty = pos["qty"]
    if config.LIVE_TRADING:
        res = angel.market_order(pos["symbol"], "sell", qty, tag="bot-exit")
        if res["status"] != "complete":
            log.error("BROKER: sell %s not filled: %s %s (will retry)", pos["symbol"], res["status"], res["text"])
            notifier.notify(f"SELL {pos['symbol']} not filled ({res['status']}), retrying", key=f"sf-{pos['symbol']}", every_seconds=300)
            return None
        price = res["avg_price"]
    else:
        price = bid
        state["paper_balance"] += qty * pos["entry"] / config.LEVERAGE + qty * (price - pos["entry"]) \
            - costs.order_charges(qty * price, "sell")

    charges = costs.round_trip(qty * pos["entry"], qty * price)
    pnl = qty * (price - pos["entry"]) - charges
    r = (price - pos["entry"]) / pos["risk_per_share"] if pos["risk_per_share"] else 0
    state["realized_pnl"] += pnl
    state["positions"].pop(pos["symbol"], None)
    rec = {"time": now().isoformat(), "side": "SELL", "symbol": pos["symbol"], "qty": qty, "price": price,
           "entry": pos["entry"], "pnl": round(pnl, 2), "charges": round(charges, 2), "r": round(r, 2), "reason": reason}
    state["history"].append(rec)
    if not config.LIVE_TRADING:  # the paper record the go-live check reads; kept outside state.json
        with open(config.TRADER_DIR / "paper_trades.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({**rec, "opened_at": pos["opened_at"]}) + "\n")
    msg = f"SELL {qty} {pos['symbol']} @ {price:.2f} ({reason}) | PnL Rs {pnl:.2f} after Rs {charges:.2f} charges ({r:+.2f}R)"
    log.info("BROKER: %s", msg)
    notifier.notify(msg)
    return pnl
