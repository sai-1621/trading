# Pre-registered test: limit-order entries instead of market orders

Written 5 Nov 2026, before running the test.

## Why
Costs are about 0.5% per trade: 0.15% per side for fee plus slippage, a further 0.15% for entering about 2 minutes
late, and funding. The model's edge before costs is about 0.4-0.6%. A resting limit order pays the maker fee
(0.02% on Binance USDT-M futures) with no slippage, instead of 0.15%. The risk is that limit orders fill mostly when
the price moves against the trade, and miss the trades that run away at once.

## Setup
- Same events, features, model, monthly walk-forward and 0.5% prediction bar as `ml_5y.py` and
  `signal_bar_test.py`.
- Each chosen trade is replayed on Binance 1-minute futures candles from the hour open (cached in
  `data_1m_events/`, the same cache as `ml_delay.py`).
- Exits stay the same: long TP 8% / SL 8%, short TP 6% / SL 10%, 24 h hold measured from the fill. The checks start
  in the fill minute, and the stop is checked before the target (conservative).
- Exit cost stays 0.15%. Funding is paid over the actual holding time.
- 100 USDT per trade, at most 5 open, as they come.
- Only trades with 1-minute data are used, the same set for every variant.

## Variants
P0 is the open of the entry hour, which is the price the signal was computed at.

| Name | Entry | Entry cost |
|---|---|---|
| MKT2 (current) | market at the open of minute 2 | 0.15% |
| L0 skip | limit at P0, resting minutes 2-9; no fill = no trade | 0.02% |
| L0 chase | as L0; no fill = market at the open of minute 10 | 0.02%, or 0.15% if chased |
| L0.2 skip | limit 0.2% better than P0 (below for longs, above for shorts) | 0.02% |
| L0.2 chase | as L0.2; no fill = market at minute 10 | 0.02% / 0.15% |
| L0.5 skip | limit 0.5% better than P0 | 0.02% |

A limit counts as filled only if the price trades **through** it: a minute's low strictly below the limit for a long,
or a high strictly above it for a short. Touching is not enough, because of queue position.

## Periods
- Pick period: trades entered before 2025-01-01.
- Holdout: 2025-01-01 onward, looked at once.
- Caveat: the 2025-26 period has been used to judge this model many times. Entry by limit order has not been tested
  on it.

## Decision rule (fixed now)
1. A variant is eligible if, in the pick period:
   - P&L > 0,
   - per-trade t >= 2.0, and
   - P&L > MKT2's pick P&L.
2. If more than one is eligible, choose the one with the highest pick P&L.
3. It replaces market entries only if its holdout P&L is > 0 and > MKT2's holdout P&L.
4. Otherwise nothing changes.

## For information
- Fill rate.
- Average return of filled against missed trades (the missed ones measured with a market entry at minute 2).
- Win rate.
- Longs and shorts.
- Per-year P&L.
