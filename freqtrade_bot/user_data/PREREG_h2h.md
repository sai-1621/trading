# Pre-registration: old frozen model vs retrained live model, forward head-to-head (written 7 Nov 2026, before data)

Question from the user: which model performs best in current market conditions, the old one (trained on data to
29 Oct 2026) or the retrained one (to 7 Nov 2026)?

A backtest cannot answer it: the retrained model has already learned every recent event. So both are scored live,
on new events neither has seen.

## Setup
* From the bot restart on 7 Nov 2026, PumpML scores every event with the live model (trades) and with the old model
  `models/compare_old.joblib` (logged only, never traded). Both go to pumpml_events.jsonl.
* "Live" means whatever model the bot trades with. The planned monthly retrain (about 1 Dec) goes ahead, so the test
  really asks: a model frozen on 29 Oct vs the monthly-retrained model.
* Scoring: forward_h2h.py, same replay as forward_shadow.py (1-minute Binance candles, market entry at minute 2,
  0.15% cost per side, the bot's exits, real funding). Only events that have both predictions count.

## Measures (fixed now)
* Primary, paired: on every event where either model would trade at the 0.5% bar, each model's result is its trade's
  return (0 if it would not trade). Difference = old - live. Paired t-stat of the difference.
* Secondary (info): each model's P&L with the bot's portfolio rule (100 USDT, at most 5 open, first come), and the
  same at the 1.5% bar ranked.

## Review
On or after 5 Dec 2026, once there are at least 100 events where either model trades. If fewer, extend week by week up
to 2 Jan 2027, then decide with what there is. Looked at once for the decision; progress checks only count trades.

## Decision
* Old wins if the mean difference > 0 and paired t >= 2.0: then the bot switches back to the old frozen model and
  monthly retraining is questioned.
* Live wins if mean difference < 0 and t <= -2.0: monthly retraining confirmed.
* Otherwise no clear winner: keep the live model and monthly retraining (the method the backtest supported).
