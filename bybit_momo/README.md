# Bybit 5-minute momentum bot (flat base → volume ignition → bull flag)

> **SHELVED on 1 Nov 2026. Do not run, paper or live.** The strategy has no edge after costs (below). Nothing from
> this folder runs in the background, and there is no scheduled task.

## Post-mortem: why 5-minute OHLCV volume breakouts fail on crypto perps

**Test:** 6 months (2 May – 1 Nov 2026) of 5-minute candles for 361 Bybit crypto USDT perps.
- Universe filter: 24 h turnover $0.5M–$500M, applied day by day from the candles, no look-ahead.
- Costs: Bybit fees (0.055% taker / 0.02% maker); slippage 0.10% on entry and stop exits, 0.05% on other exits.
- Survivorship bias (delisted coins missing) favours the strategy, so reality would be worse.

| leg | entry model | trades | win % | net avg R | **gross avg R** (before costs) | **costs per trade** | profit factor | t | May–Jul | Aug–Nov |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 ignition | taker (next open) | 9,609 | 24.4 | **−0.325** | −0.033 | 0.292R (0.275% of price) | 0.47 | −25.3 | −0.321 | −0.329 |
| 1 ignition | limit chase (≤ +0.2%, 2 bars) | 9,609 | 27.8 | **−0.217** | −0.035 | 0.182R (0.140%) | 0.63 | −14.7 | −0.216 | −0.218 |
| 2 flag | taker | 118 | 26.3 | +0.009 | +0.080 | 0.072R (0.266%) | 1.02 | 0.06 | +0.128 | −0.079 |
| 2 flag | limit chase | 118 | 27.1 | +0.046 | +0.082 | 0.037R (0.131%) | 1.12 | 0.29 | +0.170 | −0.045 |
| random entries, same stops and exits | taker | 9,727 | 14.4 | −0.244 | | | | | | |

The 3-month halves are net avg R. Leg 1's average stop is 1.35–1.44% of price; leg 2's is 4.7–4.8%.

**Takeaways**
1. **Leg 1 has no edge even before costs (gross −0.03R).** The ignition candle carries no forward information on
   these coins. Costs then turn break-even into a steady loss: a 0.14–0.28% round trip against a 1.4% stop is
   0.18–0.29R a trade. Reversing the signal would not help: +0.03R gross minus the same 0.18–0.29R friction is
   still a loss.
2. **The tight structural stop is what makes the friction fatal.** The ignition candle's low sits close to the
   entry, so costs eat a large share of R. A wider stop lowers cost per R, but leg 2 shows a wide stop (4.7%) with
   a cost of only 0.04–0.07R still nets about 0.
3. **Random entries did better than the ignition signal under the taker model** (−0.24R vs −0.33R, same coins and
   stop sizes). Buying right after an 8x volume spike is worse than buying at a random time: the spike is usually
   the top of the move.
4. **Ignition signals are common, not rare.** 9,609 in 6 months, about one per coin every 7 days. On thin coins
   an 8x volume candle out of a quiet base is noise. Results improve slightly with liquidity (avg R −0.35 below
   $2M/day, −0.14 above $100M/day with only 120 trades) but never turn positive.
5. **The model coin failed too:** FLOCKUSDT fired 6 ignition signals in October 2026 (6, 14, 16 twice, 19 and
   31 Oct). **0 of 6 won** (R −0.82, −0.40, −0.15, −0.03, −1.40, −0.56). One memorable pump is not a repeatable
   pattern.
6. **Leg 2 (flag) is noise:** 118 trades, t ≈ 0, positive in May–Jul and negative in Aug–Nov. At about 20 signals
   a month, a paper run would need years to show anything. It was not started.
7. **Limit chasing helps but cannot rescue a bad signal.** Maker entries cut costs by about 0.11R a trade, and the
   0.2% chase cap never missed a fill in the backtest. The paper model ignores queue position, so this is a
   best case.

**Lesson for future crypto ideas:** test the gross edge first. Unless gross R clearly beats the round-trip cost
per R (here 0.18–0.29R for 5-minute stops), no exit or execution tweak can save it. Slower timeframes, where
stops are wider than costs by 10–20x, are the place to look.

## Reusable parts (templates for a future 4H/1D, funding-rate or open-interest strategy)
| module | reuse as | notes |
| --- | --- | --- |
| `universe.py` | the tradable list | turnover band plus the crypto-only filter: drops Bybit's 298 tokenised stock, ETF, commodity and forex perps (`symbolType`) and gold / stablecoin tokens. Refreshes on a timer |
| `feed.py` | candle stream for any timeframe | `ccxt.pro` `watch_ohlcv_for_symbols` in chunks of 10, REST warm-up, reconnect with back-off, acts on **closed** candles only. Change `"5m"` (and `TF_MS`) for 4h / 1d. Funding / OI would need a sibling loop (`watch_ticker` or REST `fetch_funding_rate`, `fetch_open_interest`) |
| `execution.py` | order engine | post-only limit chase (re-price to the best bid, abandon past a max distance, keep partial fills); live adds isolated margin, leverage and an exchange-side reduce-only stop-market after each fill. Paper mode fills against the real book and trades. The live path has never run: test on the testnet first |
| `risk.py` | sizing and guards | size from stop distance incl. fees, capped by notional, liquidity (share of average bar turnover) and equity x leverage; max open, daily loss limit, `KILL` file |
| `dl_bybit.py` | data download | complete-history guarantee (a failed page never saves a truncated file), crypto-only candidates, 4 workers within the rate limits. Change `interval` for other timeframes |
| `bt.py` | backtest harness | taker vs maker entry models, cost model, random-entry placebo, portfolio with liquidity cap. Put a new rule set in `strategy.py` (`scan()` + `exit_event()`) and rerun |
| `bot.py` | async skeleton | universe refresh, stream, signal → sized entry task, exit management, state / journal files, heartbeat |

The 6 months of 5-minute data in `data/` (389 MB) are not needed for a higher-timeframe test and can be deleted.

---

## What was built (for reference)
Async multi-coin scanner and execution bot for Bybit USDT perpetuals (spot with `MARKET=spot`), built with
`ccxt.pro`: WebSocket 5-minute candles, stop-distance position sizing, post-only limit-chase entries, an
exchange-side stop on live orders. Paper mode by default.

## Strategy (`strategy.py`, shared by the backtest and the bot)
- **Universe:** crypto USDT perps with 24 h turnover $0.5M–$500M (BTC and ETH excluded by turnover). Bybit's
  tokenised stocks, ETFs, commodities, forex and gold / stablecoin tokens are excluded.
- **Leg 1, ignition** at a 5-minute close:
  - range of the previous 40 bars < 3.5%;
  - volume > 8x the 50-bar average;
  - close above the 40-bar high, in the top 25% of the candle (green candle).
  - Stop: the ignition candle's low.
- **Leg 2, high-tight flag:**
  - Stalked: a close ≥ 12% above the lowest low of the last 15 bars, with RVOL > 8 inside them.
  - 10–25 bars after the peak, while the pullback stays above the 0.618 retracement:
    - the mean volume of the last 8 bars is < 25% of the impulse's biggest candle;
    - the close breaks the 8-bar high on ≥ 2.5x their mean volume.
  - Stop: the 8-bar low.
- **Exits:**
  - initial stop (intrabar);
  - sell half when a candle's high is > 3 x ATR(14) above EMA(9) with an upper wick ≥ 50% of the candle;
  - the rest on a close below EMA(20).
- **Choices not in the spec:** the stops above, "rally" measured close-to-low, and the wick definition.

## Backtest (`bt.py`)
| | trades | win % | avg R | profit factor | t | trades ≥ 3R |
| --- | --- | --- | --- | --- | --- | --- |
| Ignition, taker (next open + 0.10%, 0.055% fee) | 9,609 | 24 | −0.325 | 0.47 | −25.3 | 2.2% |
| Ignition, limit chase (close + 0.2%, 2 bars, 0.02% fee) | 9,609 | 28 | −0.217 | 0.63 | −14.7 | 2.9% |
| Flag, taker | 118 | 26 | +0.009 | 1.02 | 0.06 | 2.5% |
| Flag, limit chase | 118 | 27 | +0.046 | 1.12 | 0.29 | 3.4% |
| Random entries, same stops and exits (ignition) | 9,609 | 14 | −0.246 | | | |

- **Ignition by 24 h turnover** (taker, avg R): <$2M −0.35 (6,540 trades), $2–5M −0.35, $5–20M −0.24,
  $20–100M −0.23, >$100M −0.14 (120 trades).
- **Why it fails:**
  - On thin coins an 8x volume candle is common (about one every 7 days per coin) and usually fades.
  - About 70% of trades end on the EMA20 close, typically within an hour.
  - The ignition-low stop averages 1.4%, so costs take about 0.17R a trade.
- **Data:** Bybit public API (`dl_bybit.py`), complete history per coin, no gaps. Coins delisted before today are
  missing (survivorship bias, which favours the strategy).
- **Not included:** funding (holds average 70–80 minutes) and queue position for limit orders. Paper limit fills
  are optimistic.

## Files
| file | what it does |
| --- | --- |
| `bot.py` | async main loop: universe refresh (30 min), WebSocket candles, signals, entries, exits, state files |
| `feed.py` | REST warm-up + `watch_ohlcv_for_symbols` in chunks of 10, reconnect with back-off, acts on closed candles only |
| `universe.py` | turnover band + crypto-only filter |
| `risk.py` | size = 1% of equity at the stop (incl. fees), capped at 250 USDT, 10% of average 5-minute turnover, equity x leverage; max 5 open, 3% daily loss limit, `KILL` file |
| `execution.py` | limit chase (post-only at the best bid, re-priced every 8 s up to 6 times, abandoned if price runs 0.2% above the signal); live: isolated margin, exchange stop-market reduce-only, reduce-only market exits. Paper: real order book and trades, no orders |
| `strategy.py` | the rules (pure numpy) |
| `bt.py`, `dl_bybit.py` | backtest and data download |
| `tests/` | 10 tests: ignition / flag patterns, 0.618 cancel, no look-ahead, climax half-sell, sizing caps, guards, candle-close detection |
| `state/` | status.json, positions.json, trades.jsonl, signals.jsonl, stalkers.json, bot.log |

## Commands
```
cd "D:\PyCharmMiscProject\begin\bybit_momo"
.venv\Scripts\python -m pytest -q tests
.venv\Scripts\python bt.py                  # backtest (needs data/ from dl_bybit.py)
.venv\Scripts\python bot.py                 # paper mode
.venv\Scripts\python bot.py --status
```
- Settings go in `.env`: `LEGS=flag` to run leg 2 only, `RISK_PCT`, `MAX_OPEN`, `MAX_POSITION_USDT`, `MARKET=spot`, ...
- Live needs `BYBIT_API_KEY`, `BYBIT_API_SECRET`, `LIVE_TRADING=true` **and** `--live`.
- The live order code has never been run. Test it on the testnet (`BYBIT_TESTNET=true`) before anything else.
- Paper smoke test (1 Nov 2026): 231 coins streamed, every 5-minute close processed, no errors.
