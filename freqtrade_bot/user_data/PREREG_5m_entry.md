# Pre-registration: catching pumps and dumps on 5-minute candles (written 3 Nov 2026, before any result)

## Question
The hourly PumpML bot waits for the hour to close. Does spotting the same move earlier, on 5-minute candles,
give trades that make money after realistic costs?

## Data
`user_data/data_5m`: 5-minute Binance USDT-perpetual candles, 100 most-traded coins, 1 Nov 2025 - 29 Oct 2026.
Funding: the real funding payments in `data_5y` (to 29 Oct 2026 16:00 UTC).

## Event (same thresholds as the hourly bot, checked every 5 minutes)
At the close of every 5-minute candle, over the last 60 minutes (12 candles):
- move = last close / open 60 minutes ago - 1, at least +5% (pump) or at most -5% (dump),
- traded value in those 60 minutes at least 3x the average hourly traded value of the 48 hours before,
- traded value over the last 24 hours at least 2 million USDT,
- the condition was false at the previous 5-minute close (only the first moment it becomes true),
- no other event on the same coin in the previous 24 hours.

Hourly version (information only, for comparison): the same, but checked only at whole hours.

## Trades
Entry at the open of the next 5-minute candle, plus 0.15% for the live bot's ~2-minute delay
(the cost measured by ml_delay.py). Same exits as PumpML: long take-profit +8% / stop -8%,
short take-profit +6% / stop -10%, out after 24 hours. If stop and target are both touched in one
5-minute candle, the stop counts (cautious). Costs 0.15% per side + real funding while held.
Portfolio: 1,000 USDT, 100 per trade, max 5 open.

## Four fixed rules (no model: 12 months of 5-minute data is too short to train and test one)
- R1 pump -> short (fade the pump)
- R2 pump -> long (ride the pump)
- R3 dump -> long (buy the dip)
- R4 dump -> short (ride the dump)

## Decision
- Pick period: entries before 1 Jul 2026. Holdout: 1 Jul - 29 Oct 2026, looked at once.
- In the pick period a rule is eligible only if its average trade after costs has t >= 2.0 AND its portfolio
  P&L is positive. Of the eligible rules, the one with the highest t is chosen.
- PASS if the chosen rule, in the holdout, has a positive average trade AND positive portfolio P&L.
- PASS means: worth building as a paper bot. It does not mean ready for real money.
- No eligible rule, or holdout not positive: FAIL, nothing is built.

## Known weaknesses
- Only 12 months, one market phase.
- The 100 coins are the most traded at the END of the period (they survived and grew): survivorship bias,
  which favours longs.
- 5-minute candles: when stop and target fall in the same candle, the stop is assumed first.
