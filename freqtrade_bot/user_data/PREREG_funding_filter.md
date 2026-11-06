# Pre-registration: funding filter for PumpML (written 3 Nov 2026, before any result)

## Why
The live paper short on 1000CAT (opened 2 Nov 2026) paid about 5% of the position in funding in one day
(rates of -0.6% to -1.4% every 4 hours). The model only sees price and volume, so it cannot see this cost.

## Rule F1 (the one being tested)
Do not open a SHORT when the coin's last settled funding rate, known at entry, is below -0.075% per hour
(= -0.3% per 4-hour payment, -0.6% per 8-hour payment). The per-hour rate is the last settled rate divided by
the hours since the settlement before it (Binance uses 1, 4 or 8 hours, and changes it per coin).
A blocked short is skipped; no long is taken instead (same as the live bot would do).
The threshold is fixed here. It is not tuned.

## Test
- Same as `ml_5y.py`: same events, features, model, exits, monthly walk-forward retraining (only trades
  finished before each month), 1,000 USDT, 100 per trade, max 5 open, realistic costs
  (0.15% per side + real funding over the holding time + 0.15% entry-delay cost).
- Baseline = the model with those costs (no filter). F1 = baseline with the filter.
- Period: every unseen month the 5-year test produces (2022 to Oct 2026).

## Decision (all three must pass, otherwise the bot stays unchanged)
1. The model picks F1 would block lose money on average after realistic costs, with t <= -2.0.
2. The portfolio P&L with F1 is higher than the baseline.
3. F1 is at least as good as the baseline in a majority of the calendar years with trades.

If F1 passes it is added to the paper bot only. It does not make the bot ready for real money: the
baseline already loses money over 5 years with realistic costs, and that is judged separately.

## Information only (not used for the decision)
- F2: F1 plus the mirror rule for longs (skip a long when funding is above +0.075% per hour).
- Thresholds -0.0375% and -0.15% per hour, to show whether the result depends on the exact number.

## Known weaknesses
- The 5-year data was already used (the baseline result is known: -1,033 USDT). Only the filter is new.
- Survivorship bias: coins delisted before today are missing.
- The live bot could use Binance's predicted next rate, which is fresher than the last settled rate used here.
