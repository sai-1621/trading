"""Price Alerts (trader-alerts), wired to the broker: for each open position it sells at the stop, the target or the
flat-by time (15:55 ET, 12:55 on an early-close day), win or lose: no overnight hold."""
import logging

import config

import journal
import market
from skills import broker

log = logging.getLogger("bot")


def check_positions(ib, data, state, day_bars=None):
    t = market.now()
    for sym, pos in list(state["positions"].items()):
        if day_bars and not config.LIVE_TRADING and pos["day"] < t.date().isoformat():
            # the bot was not running at that day's close: settle the paper trade as it would have ended that day
            try:
                bars, flat_min = day_bars(sym, pos["day"])
                got = journal.settle_from_bars(bars, pos["opened_at"], pos["stop"], pos["target"], flat_min)
            except Exception as e:  # noqa: BLE001
                log.warning("ALERTS: no %s bars for %s (%s): closing at the current price", pos["day"], sym, e)
                got = None
            if got:
                log.warning("ALERT %s: %s on %s, settled from that day's 5-minute bars", sym, got[1], pos["day"])
                broker.sell(ib, state, pos, got[0], f"{got[1]} on {pos['day']}, settled from that day's bars "
                                                       "(the bot was not running at the close)")
                continue
        try:
            bid, _, last = data.quote(sym)
        except Exception as e:
            log.warning("ALERTS: no price for %s (%s)", sym, e)
            continue
        pos["last"] = last  # current price, for the dashboard
        reason = None
        if last <= pos["stop"]:
            reason = f"stop hit ({last} <= {pos['stop']})"
        elif last >= pos["target"]:
            reason = f"target hit ({last} >= {pos['target']})"
        elif pos["day"] != t.date().isoformat() or market.minutes(t) >= market.flat_by_min(t.date()):
            reason = "flat by the close"
        if reason:
            log.warning("ALERT %s: %s", sym, reason)
            broker.sell(ib, state, pos, bid, reason)
