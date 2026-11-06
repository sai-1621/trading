# Pre-registration: exit levels for PumpML (written 3 Nov 2026, before any result)

## Question
Is there a take-profit / stop-loss pair that makes PumpML profitable after realistic costs?

## Variants (fixed list; take-profit / stop-loss as price moves, same for longs and shorts unless stated)
- E0 current: long +8% / -8%, short +6% / -10%
- E1 4 / 4
- E2 6 / 6
- E3 8 / 8
- E4 6 / 10
- E5 10 / 15
- E6 4 / 8
- E7 12 / 12
- E8 no target, no stop: out after 24 hours only
All variants: out after 24 hours at the latest.

## Test (same as ml_5y.py except the exits)
- Same events (5% move on 3x volume, 515 coins), same features and model settings.
- For each variant the model is retrained on that variant's own trade results (walk-forward: each month is
  predicted by models trained only on trades finished before it), trade taken if predicted net >= 0.5%.
- Realistic costs: 0.15% per side + real funding while held + 0.15% entry-delay cost.
- Hourly candles; if stop and target are touched in the same hour, the stop counts.
- Portfolio: 1,000 USDT, 100 per trade, max 5 open.

## Decision
- Pick period: trades entered before 1 Jan 2025. Holdout: 1 Jan 2025 - Oct 2026, looked at once,
  only for the chosen variant.
- Eligible in the pick period: portfolio P&L > 0 AND average trade t >= 2.0.
- Chosen: the eligible variant with the highest pick-period P&L.
- PASS if the chosen variant's holdout P&L > 0 and average trade > 0. PASS means: retrain the paper bot with
  those exits. It does not mean ready for real money.
- No eligible variant, or holdout not positive: FAIL, the bot stays as it is.

## Known weaknesses
- E0's result over all years is already known (-1,033 USDT), so E0 is not new.
- Hourly candles cannot show the order of stop and target within one hour (cautious: stop first). Earlier
  1-minute replays showed hourly tests flatter the results.
- Survivorship bias: coins delisted before today are missing.
