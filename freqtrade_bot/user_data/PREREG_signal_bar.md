# Pre-registered test: a higher prediction bar, and taking the best signals first

Written 5 Nov 2026, before running the test.

## Why
The bot takes any signal with a predicted return of 0.5% or more, first come first served, up to 5 open trades.
When several signals arrive in the same hour, which ones get the slots is arbitrary. The idea is that taking only
stronger predictions, and the strongest first, raises the average trade above the ~0.5% cost.

## Setup
- Same events, features, model, monthly walk-forward and realistic costs as `ml_5y.py` and
  `trade_limit_test.py`: fees, funding, and the 0.15% delay cost.
- 100 USDT per trade, at most 5 open.

## Variants
| Name | Bar (predicted return) | Order within the same entry hour |
|---|---|---|
| B0.5 FC (current) | 0.5% | as they come |
| B0.5 R | 0.5% | highest prediction first |
| B1.0 FC | 1.0% | as they come |
| B1.0 R | 1.0% | highest prediction first |
| B1.5 R | 1.5% | highest prediction first |
| B2.0 R | 2.0% | highest prediction first |

## Periods
- Pick period: trades entered before 2025-01-01.
- Holdout: 2025-01-01 onward, looked at once.
- Caveat: `ml_results.json` already showed a 1.0% bar from Apr 2025 on, without the delay cost (−68.7 USDT
  against +112.3 for 0.5%). So for B1.0 the holdout is not fully unseen. The other variants are new.

## Decision rule (fixed now)
1. A variant is eligible if, in the pick period:
   - P&L > 0,
   - per-trade t >= 2.0, and
   - P&L > the current variant's pick P&L.
2. If more than one is eligible, choose the one with the highest pick P&L.
3. It replaces the current rule only if its holdout P&L is > 0 and > the current rule's holdout P&L.
4. Otherwise nothing changes.

## For information
- Trades, win rate and average per trade.
- Longs and shorts.
- Per-year P&L.
- Worst drawdown.
