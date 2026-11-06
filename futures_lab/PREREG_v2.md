# Trend v2 "choppy-market fix": pre-registration (written 3 Nov 2026, before any v2 result was computed)

## Problem
T don55 long_only (buy a close above the 55-day high, sell below the 20-day low) made its money in one bull year
(2024: +126%) and lost in choppy years (2022 -20%, 2025 -18%, 2026 -26%): breakouts that reversed.
Its 2025-26 holdout has been SEEN (failed: -39%), so 2025-26 can no longer judge any fix; it is reported only.

## Fixes searched (12 variants = 2 x 2 x 3), all long only, same sizing and costs as v1
- market filter: none | btc200 (new buys and holding only while BTC's daily close is above its 200-day average;
  positions are closed when it falls below)
- choppiness filter at entry: none | er30 (the coin's 55-day efficiency ratio |close - close 55 days ago| /
  sum of |daily changes| must be at least 0.30, i.e. a clean trend, not a zig-zag)
- exit: low20 (close below the 20-day low, as v1) | trail3 (close below the highest close since entry minus
  3 x 20-day ATR) | tp_trail (sell half at +2 x ATR from entry, the rest on trail3; raises the win rate)
Entry is always a close above the 55-day high. Sizing: inverse volatility, book volatility target 40%,
max 3x gross, 0.75x per coin. Costs 0.10% per side + funding.

## Choice (pick period 2022-01-01 to 2024-12-31, Binance perp data, 19 coins)
Eligible: Sharpe >= 1.0 and max drawdown no worse than -40% (same bar as v1).
Among eligible: highest WIN RATE (the user's goal); ties: higher Sharpe. Nothing eligible: stop.

## Fresh test (one look): 2018-01-01 to 2021-10-31, never used in this project
Binance SPOT 1h prices for the same 19 coins (each joins when it was listed), real perpetual funding where the perp
existed, 0.01% per 8 hours before that. Same costs and timing.
Pass (all): total return > 0, Sharpe >= 0.5, max drawdown no worse than -40%, no day worse than -30%,
and win rate higher than unfixed v1 (don55 long_only, low20) on the same fresh period.
Reported only (not deciding): 2025-26, BTC buy-and-hold.
Pass: a paper futures bot beside the 3x ML bot, judged after 3+ months. Fail: nothing changes.
