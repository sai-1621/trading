# Swing (delivery / CNC) v1: pre-registration (written 2 Nov 2026, before any swing result was computed)

## Why
176 intraday variants (ORB v2, mean reversion v1) were break-even before costs and lost after. Swing
trades aim for bigger moves, so costs are a smaller share of each trade.

## Data
Angel One daily bars (getCandleData ONE_DAY, 2000 days back: about May 2021 to 30 Oct 2026) for today's
Nifty 100 plus the bot's 58 most-traded stocks (union). Nifty 50 index daily bars for the market filter.
Known bias: today's index members (survivorship) flatter long-only results; reported as a caveat.
- Pick period (signals dated): 2022-03-01 to 2024-12-31 (the first ~200 days are indicator warm-up)
- Holdout: 2025-01-01 to 2026-10-30, looked at ONCE after the choice is fixed

## Execution (all variants)
- Signals on a day's close; buy at the NEXT day's open + 0.05% spread / 2. One position per stock at a time.
- Exit signals on a close: sell at the next day's open - spread / 2.
- Stops (where a variant has one) are checked on the daily low from the day after entry; filled at the stop,
  or at the open if the day opens below it.
- Delivery charges per round trip: brokerage min(Rs 20, 0.1%) per order, STT 0.1% buy and sell, stamp 0.015%
  on buy, exchange 0.00297% + SEBI 0.0001% + 18% GST on brokerage and fees, DP Rs 15.93 per sell.
  Trade size Rs 20,000 for the per-trade stats.
- Win = profit after costs. Per-trade return is in % of the position.

## Searched: 56 variants
Family M, momentum breakout (24): buy when the close is the highest close of N days.
- N: 20, 50
- exit: atr3 (close below the highest close since entry minus 3 x 14-day ATR), low10 (close below the
  10-day lowest close), low20 (20-day lowest close)
- stock filter: none, or close above its 200-day average
- market filter: none, or Nifty 50 close above its 200-day average

Family R, pullback in an uptrend (32): close above its F-day average and 2-day RSI below X.
- X: 5, 10
- F: 200, 50
- exit: ma5 (close above the 5-day average), rsi70 (2-day RSI above 70)
- stop: none (time exit after 10 trading days), atr3 (entry - 3 x 14-day ATR)
- market filter: none, or Nifty 50 close above its 200-day average
(Family R always has the 10-day time exit; family M has none.)

## Choice rule (pick period only)
Eligible: average return after costs > 0, t-stat >= 3.0, >= 200 trades.
Among eligible, choose the highest win rate. Ties: higher t.
Nothing eligible: report it, holdout not looked at, nothing changes.

## Portfolio (reported for the chosen variant on pick and holdout)
Rs 1,00,000, at most 5 positions of Rs 20,000; when more signals than free slots, family M takes the
highest 20-day return first, family R the lowest RSI first. Reported: total return, max drawdown, Nifty 50
buy-and-hold over the same dates.

## Holdout pass rule (one look)
1. average return after costs > 0 with t-stat >= 2.0,
2. win rate above 50% (the live ORB bot: 36.8%),
3. the Rs 1,00,000 portfolio makes money over the holdout.
Pass: propose a paper-trading swing bot (still paper; no real money). Fail: report it, change nothing.
Note: overlapping trades make per-trade t-stats optimistic; the portfolio result is the stricter check.
