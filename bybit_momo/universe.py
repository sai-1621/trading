"""Tradable universe: active USDT pairs (perps or spot) with 24 h turnover between TURNOVER_MIN and TURNOVER_MAX."""
import logging

import config as C

log = logging.getLogger("momo")


async def load_universe(ex) -> dict:
    """{ccxt symbol: {"turnover24h": float, "min_amount": .., "min_cost": ..}} for the configured market."""
    await ex.load_markets(reload=True)
    want = "swap" if C.MARKET == "linear" else "spot"
    non_crypto = {"stock", "ETF", "commodity", "forex"}       # Bybit lists tokenised stocks etc.: not for this strategy
    pegged = {"XAUT", "PAXG", "USDC", "USDE", "FDUSD"}
    mk = {s: m for s, m in ex.markets.items()
          if m.get("active") and m.get("type") == want and m.get("quote") == "USDT"
          and (want != "swap" or m.get("linear")) and m.get("settle", "USDT") in ("USDT", None)
          and (m.get("info") or {}).get("symbolType", "") not in non_crypto and m.get("base") not in pegged}
    tickers = await ex.fetch_tickers(list(mk), params={"category": C.MARKET})
    out = {}
    for s, t in tickers.items():
        info = t.get("info") or {}
        turnover = float(info.get("turnover24h") or t.get("quoteVolume") or 0)
        if C.TURNOVER_MIN <= turnover <= C.TURNOVER_MAX and s in mk:
            lim = mk[s].get("limits") or {}
            out[s] = {"turnover24h": turnover, "min_amount": (lim.get("amount") or {}).get("min"),
                      "min_cost": (lim.get("cost") or {}).get("min")}
    log.info("universe: %d of %d %s USDT pairs in the turnover band", len(out), len(mk), C.MARKET)
    return out
