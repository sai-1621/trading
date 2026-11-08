# Pre-registration: best trailing-stop distance on pump/dump events (written 8 Nov 2026, before any result)

Question from the user: which trailing-stop distance works best on the events the ML bot trades? Research only; the
bot is not changed by this test.

## Setup (fixed now)
* Events: every pump/dump event in the ml_5y data (move >= 5% in an hour on >= 3x volume, >= 2M USDT a day).
* Entry at the next hour's open. Costs 0.15% per side + 0.15% entry delay + real funding (same as all earlier tests).
* Exits compared:
  * FIXED (the bot today): long TP +8% / SL -8%, short TP -6% / SL +10%, out after 24 h (stop first if both in one hour).
  * TRAIL d% for d in 3, 5, 8, 10, 12, 15, 20, 25: stop d% behind the best price since entry, checked each hour
    against the best price of earlier hours (a gap through the stop fills at the open). Max hold 24 h, and separately
    72 h.
* Trade groups: (a) the trades the ML model picks (monthly walk-forward predictions, bar 0.5%, the side it prefers;
  the model was trained on FIXED exits, so this favours FIXED a little); (b) all events, four ways: long and short
  after pumps, long and short after dumps.
* Measure: average net return per trade (no portfolio limit), with t-stat.

## Periods and decision
* Pick 2021-2024: for group (a), the exit with the best average is chosen (FIXED included).
* Holdout 2025-2026, looked at once: the chosen exit "wins" only if its holdout average is > 0 and above FIXED's.
* If FIXED is chosen, or the chosen trail fails the holdout: fixed exits stay the best known choice.
* Even a win only justifies a full test (retraining the model on trailing exits); it does not change the bot.
