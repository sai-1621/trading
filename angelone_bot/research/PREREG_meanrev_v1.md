# Intraday mean reversion v1: pre-registration (written 2 Nov 2026, before any result was computed)

## Why
ORB v2 (research/PREREG_orb_v2.md) found no edge: all 128 breakout variants were about break-even before
costs and lost after. This tests the opposite idea: buy a stretched intraday dip and sell the bounce.

## Rule (long only, intraday, one trade per stock per day)
- ATR = the stock's 14-day average true range from DAILY bars up to the previous day (no look-ahead).
- VWAP = today's volume-weighted average price from 9:15, on 5-minute bars.
- Signal: the first 5-minute bar that CLOSES between 09:45 and 14:00 at least D x ATR below VWAP.
- Entry: that bar's close + half the 0.05% spread.
- Stop: entry - S x ATR. Filled at the stop, or at the bar's open if it opens below the stop.
- Target (a resting sell order, filled if the bar's high reaches it):
  - "vwap": VWAP as of the previous bar's close (moves through the day);
  - "open": today's 9:15 opening price.
  If a bar touches both the stop and the target, the stop counts (conservative).
- Exit at the 15:10 close if neither was hit.
- Costs and sizing exactly as the live bot: Angel One charges + 0.05% spread, max loss Rs 500 per trade,
  max order Rs 40,000. Bot limits for the Rs result: 3 trades a day, 2 open, Rs 1,500 daily loss.

## Searched: 48 variants, every combination
- D (how far below VWAP): 0.5, 0.75, 1.0 x ATR
- S (stop distance): 0.5, 1.0 x ATR
- target: vwap, open
- stock filter: none, or previous close above the 50-day average
- market filter: none, or Nifty 50's previous close above its 20-day average

## Data and split (same as ORB v2)
Same 58 stocks, cached Angel One bars. Pick period 2024-12-03 to 2025-12-31; holdout 2026-01-01 to 2026-10-30.

## Choice rule (pick period only)
Eligible: average R after costs > 0, t-stat >= 3.0, >= 200 trades.
Among eligible, choose the highest win rate (win = profit after costs). Ties: higher t.
Nothing eligible: report it, holdout not looked at, nothing changes.

## Holdout pass rule (one look)
1. average R after costs > 0 with t-stat >= 2.0,
2. win rate above the current live ORB rules' win rate on the same holdout (and reported vs 50%),
3. the bot (with its daily limits) makes money over the holdout.
Pass: propose it for PAPER trading only. Fail: report it, change nothing.
