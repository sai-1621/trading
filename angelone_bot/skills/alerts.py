"""Price Alerts (trader-alerts), wired to the broker: for each open position it sells at the stop, the target
or the flat-by time (15:10 IST by default, before Angel One's own auto square-off)."""
import logging

import config
import journal
import market
from skills import broker

log = logging.getLogger("bot")


def check_positions(angel, data, state, day_bars=None):
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
                broker.sell(angel, state, pos, got[0], f"{got[1]} on {pos['day']}, settled from that day's bars "
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
        elif pos["day"] != t.date().isoformat() or market.minutes(t) >= market.hhmm(config.FLAT_BY):
            reason = f"flat by {config.FLAT_BY}"
        if reason:
            log.warning("ALERT %s: %s", sym, reason)
            broker.sell(angel, state, pos, bid, reason)
