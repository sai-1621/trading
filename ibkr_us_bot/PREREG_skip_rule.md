# Pre-registered test: is the Time Machine skip rule throwing away good trades?

Written 5 Nov 2026, before running the test.

## Why
In the first 7 live paper sessions (27 Oct – 4 Nov 2026) the bot skipped 93 opening-range signals because the Time
Machine said the odds did not beat break-even. Followed anyway, they made +$525 (avg +0.36R). The bot's own 2 trades
lost $50. Those 7 days prompted the question, so they cannot answer it.

## Data
- Yahoo 5-minute bars: the backtest cache (to about 29 Oct) merged with a fresh download (to 4 Nov).
  Stocks: every stock in the backtest cache.
- Daily bars for the 20-day-average trend filter.

## Signals
- The live rule exactly (skills/rule.py, live settings):
  - opening range 9:30–9:45; entry on the first 5-minute close above it, between 9:45 and 15:30;
  - stop at the opening-range midpoint, target 2R, flat by 15:55;
  - trend filter on; 0.02% spread; IBKR charges.
- One signal per stock per day at most. The 20-day-high "level" setup is not included (opening-range breakouts
  only).

## Time Machine decision
- Re-run as in skills/timemachine.py, using only full sessions before the signal day.
- It needs 20 or more of them, and each session must have at least 20 days of prior data.
- 2,000 replays per signal instead of 10,000 (for speed), with the same seed.
- The signal is "taken" if the bot would take it: the share of target-first beats break-even after costs and the
  average R after costs is positive. Otherwise it is "skipped".

## Periods
- **Decision period:** test sessions before 27 Oct 2026.
- **Live week:** 27 Oct – 4 Nov 2026, reported for information only.

## Decision rule (fixed now)
Remove or loosen the skip rule only if, in the decision period, BOTH hold:
1. skipped signals average R after costs > 0 with t >= 2.0, and
2. all signals together average R after costs > 0.

Otherwise the skip rule stays as it is.

## For information (does not change the decision)
- Average R after costs of taken signals.
- Whether the Time Machine's own score (average R after costs) ranks outcomes: correlation, plus the top and bottom
  halves.
- The live week's numbers.
