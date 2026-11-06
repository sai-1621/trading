# Pre-registered test: the ML bot's open-trade limit

Written 5 Nov 2026, before running the test.

## Why
The limit of 5 open trades was inherited from the old PumpRider config, not chosen by a test. On 4 Nov all 5 slots
filled with shorts, 4 of them opened in the same hour on US-stock coins, so they were really one bet.

## Setup
- Same events, features, model, monthly walk-forward and realistic costs as `ml_5y.py`: fees, funding, and the
  0.15% delay cost.
- 100 USDT per trade, MIN_PRED 0.005.
- Only the open-trade rule changes.

## Variants
| Name | Open-trade limit | Same-hour cap |
|---|---|---|
| L3 | 3 | none |
| L5 (current) | 5 | none |
| L8 | 8 | none |
| L10 | 10 | none |
| H2-5 | 5 | at most 2 new trades with the same entry hour |
| H2-10 | 10 | at most 2 new trades with the same entry hour |

## Periods
- Pick period: trades entered before 2025-01-01.
- Holdout: 2025-01-01 onward, looked at once.

## Decision rule (fixed now)
1. A variant is eligible if, in the pick period:
   - P&L > 0,
   - per-trade t >= 2.0, and
   - P&L > L5's pick P&L.
2. If more than one variant is eligible, choose the one with the highest pick P&L.
3. It replaces the limit of 5 only if its holdout P&L is > 0 and > L5's holdout P&L.
4. Otherwise the limit stays at 5.

## For information
- Worst drawdown in USDT.
- Worst single entry hour (the sum of trades opened in the same hour).
- Trades taken.
- Per-year P&L.
