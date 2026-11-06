# Strategy lab: order blocks and VWAP pullback

Backtests two intraday strategies on the same data, charges and Time Machine gate as the bots, with the bots'
opening-range breakout (ORB) as the baseline. Long and short for stocks, long only for crypto (spot).
Settings are chosen on the first 2/3 of the days and judged on the last 1/3.

```
..\angelone_bot\.venv\Scripts\python lab.py --market nse
..\ibkr_us_bot\.venv\Scripts\python  lab.py --market us
..\.venv\Scripts\python              lab.py --market crypto-inr     (or crypto-usdt)
```
Results: `results/<market>.json`. `results/big/` holds runs with larger positions
(env overrides `MAX_ORDER`, `BUDGET`, `MAX_LOSS_PER_TRADE`), where fixed brokerage weighs less.

## Result (run 27 Oct 2026; NSE/US 60 stocks, 60 days of 5m bars; crypto 120 days of 15m bars)
No setting of either strategy made money after charges on both periods, in any market. Before charges both are
near zero R per trade, so they have no edge for charges to eat into. Not added to the bots.

## Market-trend and relative-strength filters (`filters.py`, run 29 Oct 2026)
```
..\angelone_bot\.venv\Scripts\python filters.py --market nse
..\ibkr_us_bot\.venv\Scripts\python  filters.py --market us
```
Adds to the bots' opening-range breakout: index above its 50/200-day average, and the stock beating the index
over 1/3 months (or top 30%). Test A: exact rule, 5m bars, ~60 days. Test B: first-hour range on hourly bars,
Feb/Mar 2024 to Oct 2026. 100 stocks per market. Results: `results/filters_<market>.{json,txt}`.

Result: 0 of 240 combinations per test made money on both periods. The filters cut losses (US best -0.05R vs
-0.11R per trade; NSE -0.13R vs -0.23R) but before charges the trades are still about 0R, so no edge. Not added to the bots.

## Long vs short + machine learning (`stocks_candidates.py` + `stocks_ml.py`, run 30 Oct 2026)
```
..\angelone_bot\.venv\Scripts\python stocks_candidates.py --market nse
..\ibkr_us_bot\.venv\Scripts\python  stocks_candidates.py --market us
..\freqtrade_bot\.venv\Scripts\python stocks_ml.py --market nse      (and --market us)
```
Hourly bars, 100 stocks per market, Feb 2024 – Oct 2026. Long = first close above the first hour's high; short =
first close below its low; stop at the middle of the first hour, target 2R, out at the close; bots' charges.
ML: two gradient-boosting models (long / short) retrained every month on earlier months only; trade the better
side if predicted ≥ 0.1R, max 3 a day (fixed in advance). R per trade after charges on unseen months:

| | NSE (Aug 2024 – Oct 2026) | US (Sep 2024 – Oct 2026) |
|---|---|---|
| Bots' rule, every long | −0.218R (35.6% won, 0/27 months up) | −0.104R (42.1% won, 2/26) |
| Every short | −0.199R (38.1%, 0/27) | −0.115R (40.1%, 3/26) |
| ML ≥ 0.1R, max 3/day | −0.098R (40.7%, 7/27) | −0.103R (42.0%, 6/26) |
| Best ML variant | −0.078R (≥0.2R) | −0.080R (≥0.0R) |

Before charges every version is about 0R (−0.05 to +0.09R). Shorts are no better than longs. ML halves the NSE
loss but no version makes money in any year. Not added to the bots.

## Buy-the-dip swing test (dips.py, 30 Oct 2026)
Rule: close above the 200-day average and 2-day RSI below 5, buy at the next open; sell at the next open after
2-day RSI > 70 (max 10 days). Daily bars 2017-2026, 99 stocks per market, chosen on 2017-2023, checked on Oct 2023 - Oct 2026.

| Unseen years | trades | won | avg per trade after charges | any uptrend day, same hold | best portfolio (6 positions) | index |
|---|---|---|---|---|---|---|
| NSE | 929 | 66% | +0.77% | +0.18% | +17.0%, worst drop 11% | +17.3% |
| US  | 1132 | 70% | +1.10% | +0.66% | +81.3%, worst drop 17% | +81.1% |

Win rate high and the dip adds edge per trade, but as a portfolio it only matched the index (before 20% short-term
tax in India, and with survivorship bias on today's stocks). 2 positions: NSE +6.7%, US +52.7%, worst drop ~31%.
