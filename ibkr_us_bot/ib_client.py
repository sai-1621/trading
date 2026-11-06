"""Interactive Brokers wrapper (pip package ib_async). TWS or IB Gateway must be running and logged in,
with Configure > API > Settings > "Enable ActiveX and Socket Clients" ticked.

Ports: TWS paper 7497, TWS live 7496, IB Gateway paper 4002, IB Gateway live 4001.
Paper accounts start with "DU". The bot checks that a paper session is not used for live trading and the
other way round.
"""
import logging
import math
from datetime import timedelta

from ib_async import IB, Stock, MarketOrder, util

import config
from market import ET

log = logging.getLogger("bot")


class IBError(Exception):
    pass


class IBClient:
    def __init__(self):
        self.ib = IB()
        try:
            self.ib.connect(config.IB_HOST, config.IB_PORT, clientId=config.IB_CLIENT_ID, timeout=10,
                            account=config.IB_ACCOUNT)
        except Exception as e:
            raise IBError(f"cannot reach TWS/IB Gateway at {config.IB_HOST}:{config.IB_PORT} ({e}). "
                          f"Is it running and logged in, with the API enabled?") from e
        accounts = self.ib.managedAccounts()
        self.account = config.IB_ACCOUNT or (accounts[0] if accounts else "")
        self.paper = self.account.upper().startswith("DU")
        if config.IB_DELAYED_DATA:
            self.ib.reqMarketDataType(3)   # delayed data when there is no live subscription
        self._contracts = {}
        log.info("Interactive Brokers: connected, account %s (%s)", self.account, "PAPER" if self.paper else "LIVE")

    def ensure(self):
        if not self.ib.isConnected():
            log.warning("Interactive Brokers: reconnecting")
            self.ib.connect(config.IB_HOST, config.IB_PORT, clientId=config.IB_CLIENT_ID, timeout=10, account=config.IB_ACCOUNT)

    def contract(self, symbol):
        if symbol not in self._contracts:
            c = Stock(symbol.replace(".", " "), "SMART", "USD")   # BRK.B -> "BRK B"
            q = self.ib.qualifyContracts(c)
            if not q:
                raise IBError(f"{symbol}: no US stock contract found")
            self._contracts[symbol] = q[0]
        return self._contracts[symbol]

    def candles(self, symbol, days=1):
        self.ensure()
        bars = self.ib.reqHistoricalData(self.contract(symbol), endDateTime="", durationStr=f"{max(days, 1)} D",
                                         barSizeSetting="5 mins", whatToShow="TRADES", useRTH=True, formatDate=2)
        return [{"t": b.date.astimezone(ET), "o": b.open, "h": b.high, "l": b.low, "c": b.close, "v": b.volume} for b in bars]

    def quote(self, symbol):
        self.ensure()
        t = self.ib.reqTickers(self.contract(symbol))[0]
        last = t.last if t.last and not math.isnan(t.last) else t.close
        bid, ask = t.bid, t.ask
        if not last or math.isnan(last):
            return None
        if not bid or not ask or math.isnan(bid) or math.isnan(ask) or bid <= 0 or ask <= 0:
            half = last * config.ASSUMED_SPREAD_PCT / 100 / 2
            bid, ask = last - half, last + half
        return bid, ask, last

    def available_funds(self):
        self.ensure()
        for v in self.ib.accountSummary(self.account):
            if v.tag == "AvailableFunds" and v.currency == "USD":
                return float(v.value)
        raise IBError("AvailableFunds not reported")

    def market_order(self, symbol, side, qty, tag="bot"):
        """Places a market order and waits up to 30 s for the fill. Returns {"status", "avg_price", "filled"}."""
        self.ensure()
        order = MarketOrder(side.upper(), int(qty), tif="DAY", orderRef=tag[:30])
        trade = self.ib.placeOrder(self.contract(symbol), order)
        for _ in range(60):
            self.ib.sleep(0.5)
            if trade.isDone():
                break
        st = trade.orderStatus
        return {"status": st.status, "avg_price": float(st.avgFillPrice or 0), "filled": int(st.filled or 0),
                "orderid": trade.order.orderId, "text": "; ".join(e.message for e in trade.log if e.message)}
