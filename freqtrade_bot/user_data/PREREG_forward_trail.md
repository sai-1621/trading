# Pre-registration: forward test of a 20% trailing stop (written 8 Nov 2026, before any data)

Lead from the trailing-stop backtest (README, 8 Nov 2026): a 15-20% trail with a 24 h limit beat the fixed exits in
both periods, but it was noticed only after the holdout was opened. This tests it on trades that do not exist yet.

## Setup (fixed now)
* Trades: every trade the main ML paper bot (PumpML, 0.5% rule) opens after 2026-11-08 03:30 UTC (09:00 IST).
* Each trade is replayed on Binance 1-minute futures candles from its actual entry minute and entry price, twice:
  * FIXED: the bot's exits (long TP +8% / SL -8%, short TP -6% / SL +10%, 24 h);
  * TRAIL20: stop 20% behind the best price since entry (checked each minute against the best of earlier minutes; a
    gap through the stop fills at the open), out after 24 h.
  Same costs for both: 0.15% per side, plus real funding over each version's own holding time.
  (Replaying FIXED too, instead of using the bot's own result, keeps the comparison like for like.)
* Script: forward_trail.py -> forward_trail.json. Read-only towards the bot.

## Decision
* Primary: per trade, difference = TRAIL20 - FIXED; paired t-stat.
* Review on or after 5 Dec 2026 with at least 100 replayed trades; if fewer, extend week by week up to 2 Jan 2027.
* TRAIL20 wins if the mean difference > 0 and t >= 2.0. A win means a full test next (retrain the model on trailing
  exits, walk-forward with a fresh holdout), never a direct change to the bot.
* Otherwise the fixed exits stay.
