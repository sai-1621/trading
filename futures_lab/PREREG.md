# Futures strategy search v1: pre-registration (written 3 Nov 2026, before any result was computed)

## Goal
A Binance USDT-perpetual strategy for the paper futures bot that uses leverage deliberately (through volatility
targeting, max 3x gross) and makes money after fees, slippage and funding on data it was not chosen on.

## Universe (fixed)
19 large coins with Binance perpetual 1h data since Nov 2021: BTC ETH SOL BNB XRP DOGE ADA AVAX LINK DOT LTC BCH
TRX ATOM ETC NEAR FIL UNI AAVE. Caveat: coins that died (LUNA, FTT...) are not in the data (survivorship).

## Data and timing
1h futures candles and 1h funding rates (freqtrade_bot/user_data/data_5y). Daily signals use the 00:00 UTC
daily close; trades fill at the close of the 01:00 UTC hour (1 hour later); positions are held from one
01:00 fill to the next. Rebalanced daily (family X: every 7 days).
Costs: 0.10% of traded notional per side (0.05% taker fee + 0.05% slippage); funding paid/received each hour
on the notional held (longs pay positive funding, shorts receive it).

## Sizing (all variants): volatility targeting
Raw weights from the signal -> scaled so the book's 30-day realised volatility (of today's weights on the
last 30 days of returns) is 40% a year -> capped at 3.0x gross leverage and 0.75x per coin.

## Variants (12)
Family T, trend per coin (raw weight +1 long / -1 short / 0, inverse to the coin's 30-day volatility):
- signal: don20 (enter on a close above the 20-day high / below the 20-day low, exit on the opposite 10-day
  channel), don55 (55 / 20), ema10_50 (sign of EMA10 - EMA50, always in), ema20_100 (sign of EMA20 - EMA100)
- side: long-short, or long-only
Family X, cross-sectional momentum (market neutral): every 7 days rank by the past L-day return, long the top
k, short the bottom k, inverse-volatility weights; L: 7, 28; k: 3, 5.

## Periods
Warm-up Nov-Dec 2021. Pick period 2022-01-01 to 2024-12-31. Holdout 2025-01-01 to 2026-10-29 (one look).

## Choice rule (pick period only)
Eligible: annualised Sharpe >= 1.0 and max drawdown no worse than -40%. Choose the highest Sharpe.
Nothing eligible: report it, holdout not looked at.

## Holdout pass rule (one look; all must hold)
1. total return > 0 and annualised Sharpe >= 0.5,
2. max drawdown no worse than -40%,
3. no day with a loss over 30% of equity (liquidation-style blow-up check).
Reported but not deciding: BTC buy-and-hold, win rate of trades/periods, average leverage.
Pass: build a paper futures bot for it (no real money). Fail: report it, change nothing.

## Addendum (3 Nov 2026, after v1 found nothing eligible; before any holdout number was computed)
The user approved one labelled NEAR-MISS check: T don55 long_only (best pick-period Sharpe 0.92, max DD -42.6%;
it failed the choice rule). It gets ONE look at the holdout with the same pass rule as above (return > 0,
Sharpe >= 0.5, max DD no worse than -40%, no day worse than -30%). This is weaker evidence than a clean pass.
Pass: a paper bot running beside the 3x ML bot, judged after at least 3 months. Fail: nothing changes.
