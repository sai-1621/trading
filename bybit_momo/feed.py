"""5-minute candle feed: REST warm-up (last HISTORY_BARS bars) + WebSocket updates via ccxt.pro.
Calls on_close(symbol, arrays) once per CLOSED candle (a candle counts as closed when a newer one starts)."""
import asyncio
import logging
from collections import deque

import numpy as np

import config as C

log = logging.getLogger("momo")
TF_MS = 5 * 60 * 1000


class CandleBook:
    def __init__(self):
        self.bars: dict[str, deque] = {}        # symbol -> deque of [ts, o, h, l, c, v] (closed candles)
        self.live: dict[str, list] = {}         # symbol -> the forming candle

    def seed(self, sym, ohlcv):
        if not ohlcv:
            return
        closed, forming = ohlcv[:-1], ohlcv[-1]
        self.bars[sym] = deque((list(map(float, r)) for r in closed), maxlen=C.HISTORY_BARS)
        self.live[sym] = list(map(float, forming))

    def update(self, sym, row) -> bool:
        """Feed one candle row from the stream. Returns True when the previous candle just closed."""
        row = list(map(float, row))
        cur = self.live.get(sym)
        if cur is None:
            self.live[sym] = row
            return False
        if row[0] > cur[0]:                      # a new candle started: the old one is final
            self.bars.setdefault(sym, deque(maxlen=C.HISTORY_BARS)).append(cur)
            self.live[sym] = row
            return True
        if row[0] == cur[0]:
            self.live[sym] = row
        return False

    def arrays(self, sym) -> dict | None:
        b = self.bars.get(sym)
        if not b or len(b) < 120:
            return None
        a = np.array(b, dtype=float)
        return {"ts": a[:, 0].astype(np.int64), "open": a[:, 1], "high": a[:, 2], "low": a[:, 3],
                "close": a[:, 4], "volume": a[:, 5], "turnover": a[:, 5] * a[:, 4]}


async def warm_up(ex, book: CandleBook, symbols, concurrency=5):
    sem = asyncio.Semaphore(concurrency)

    async def one(s):
        async with sem:
            for attempt in range(3):
                try:
                    book.seed(s, await ex.fetch_ohlcv(s, "5m", limit=C.HISTORY_BARS + 1))
                    return
                except Exception as e:  # noqa: BLE001
                    log.warning("warm-up %s: %s", s, e)
                    await asyncio.sleep(2 + attempt * 3)
    await asyncio.gather(*(one(s) for s in symbols))
    log.info("warm-up done: %d symbols with history", sum(1 for s in symbols if s in book.bars))


async def stream(ex, book: CandleBook, symbols, on_close, stop_event: asyncio.Event):
    """One task per chunk of WS_CHUNK symbols; reconnects with back-off."""
    async def chunk(ss):
        back = 2
        while not stop_event.is_set():
            try:
                data = await ex.watch_ohlcv_for_symbols([[s, "5m"] for s in ss])
                back = 2
                for s, tfs in data.items():
                    for row in tfs.get("5m", []):
                        if book.update(s, row):
                            await on_close(s)
            except Exception as e:  # noqa: BLE001 - network hiccups: wait and resubscribe
                log.warning("stream %s..: %s; retry in %ss", ss[0], e, back)
                await asyncio.sleep(back)
                back = min(back * 2, 60)
    chunks = [symbols[i:i + C.WS_CHUNK] for i in range(0, len(symbols), C.WS_CHUNK)]
    await asyncio.gather(*(chunk(c) for c in chunks))
