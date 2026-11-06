# ORB v2: pre-registration (written 2 Nov 2026, before any v2 result was computed)

## Why
The 2-year Angel One backtest (3 Dec 2024 - 30 Oct 2026, 58 stocks) of the live rules
(15-min range, or-mid stop, 2R target, entries 09:30-14:30, flat 15:10, stock above 20-day average)
lost: 2,562 trades, win rate 36.8%, -0.27R per trade after costs (t -11.8). Before costs it was about
break-even (-0.02R): costs (Rs 20 per order + taxes, on a position capped at Rs 40,000) take ~0.25R
because the or-mid stop is tight. The Time Machine filter did not help (a random skip beat it 78% of the time).

## Goal
Higher win rate AND a profit after costs. A higher win rate alone is not enough (a tiny target raises
the win rate and still loses money), so both are required.

## Data
Cached Angel One 5-minute and daily bars of the same 58 stocks (cache/angel_bt_<SYM>_730.json).
Nifty 50 daily closes for the market filter.
- Pick period: 2024-12-03 to 2025-12-31 (choose the rules here only)
- Holdout: 2026-01-01 to 2026-10-30 (looked at ONCE, after the choice is fixed)

## Fixed (not searched)
Long only, one trade per stock per day, first bar closing above the range high, volume check 1.0x,
stock above its 20-day average (as live), entries from 09:30, flat by 15:10, Angel One costs and
0.05% spread, the bot's sizing (max loss Rs 500, max order Rs 40,000), the bot's limits
(3 trades a day, 2 open, Rs 1,500 daily loss). No Time Machine (it did not help).

## Searched: 128 variants, every combination
- opening range: 15 or 30 minutes
- stop: or-mid, or-low, atr:3 (3 x 5-minute ATR), pct:1.0
- target: 0.5R, 1R, 1.5R, 2R
- last entry: 10:30 or 14:30
- market filter: none, or Nifty 50's previous close above its 20-day average

## Choice rule (pick period only)
Eligible: average R after costs > 0, t-stat >= 3.0 (strict because 128 variants are tried), >= 200 trades.
Among eligible, choose the highest win rate (win = profit after costs). Ties: higher t.
If nothing is eligible: report that, change nothing.

## Holdout pass rule (one look)
The chosen variant passes if, on the holdout:
1. average R after costs > 0 with t-stat >= 2.0, and
2. its win rate is higher than the current live rules' win rate on the same holdout, and
3. the bot (with its daily limits) makes money over the holdout.
Pass: propose it for paper trading (still paper; no real money). Fail: report it, change nothing live.
