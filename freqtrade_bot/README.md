# Crypto bot (Freqtrade)

A separate crypto bot built on [Freqtrade](https://github.com/freqtrade/freqtrade) 2026.9 (stable, cloned into `freqtrade/`).
Used only for crypto; the NSE, US and SIP bots do not use it.

- Exchange: **Binance spot, USDT pairs** (registered with India's FIU). Fee 0.1% per side.
- Mode: **paper (dry-run)**, 1,000 USDT fake wallet. No API keys are stored anywhere.
- Coins: BTC, ETH, SOL, BNB, XRP, ADA, DOGE, LINK, AVAX, DOT. Up to 4 held at once.
- Strategy: `DonchianBreakout`, checked once a day.
  - Buy when a coin closes above its previous 55-day high, and only while BTC is above its 200-day average.
  - Sell when it closes below its previous 20-day low, or if it falls 25% (disaster stop).
  - Holds for weeks; expect only a few trades a month and long spells in cash.

## Web screen
http://127.0.0.1:8080 (this PC only). Username `admin`; the password is in `user_data/config-private.json`.
Keep that file private.

## Commands
```
cd "D:\PyCharmMiscProject\begin\freqtrade_bot"
powershell -ExecutionPolicy Bypass -File run_bot.ps1      # start (restarts itself if it stops)
.venv\Scripts\freqtrade show-trades --db-url sqlite:///user_data/tradesv3.dryrun.sqlite --userdir user_data
.venv\Scripts\freqtrade download-data -c user_data\config.json --timeframes 1d 4h --timerange 20190101- --userdir user_data
.venv\Scripts\freqtrade backtesting -c user_data\config.json -s DonchianBreakout --timerange 20230101- --fee 0.001 --export none --userdir user_data
```
To stop it, end the hidden `powershell.exe` running `run_bot.ps1` and its `freqtrade.exe`.

## Backtest results (29 Oct 2026, Binance daily data, 0.1% fee, no tax)

Yearly return (worst fall in brackets). "10 coins" = holding all 10 coins equally.

| Period | TrendHoldDaily | DonchianBreakout | EmaTrend4h | BTC hold | 10 coins hold |
|---|---|---|---|---|---|
| Aug 2019 – now | 51% (41–47%) | 50% (40–60%) | 78% (16–37%) | 33% (77%) | 58% (82%) |
| Aug 2019 – 2022 | 123% | 96% | 230% | 15% | 101% |
| 2023 – now | 6.7% | **17.9%** | 5.5% | 51.5% | 28% |
| Last 12 months (total) | −1.8% | **+3.7%** | −5.5% | −26% | −47% |

What this shows:
- None of the strategies beat simply holding the coins since 2023. Their value is **avoiding crashes**:
  they sat in cash during the last year's −47% fall.
- The coin list is today's survivors, so all results (and the holding numbers) look better than reality.
- EmaTrend4h made only 32 trades in 7 years; its big number rests on a handful of trades and is mostly luck.
- India taxes crypto gains at 30% with no loss offset, plus 1% TDS on sells. Not included above.

Stay on paper. Do not add API keys or set `dry_run` to false without a clear reason and a decision to accept these risks.

## Pump / dump strategies (tested 30 Oct 2026) — none passed
Binance USDT futures, 1x, 515 coins, hourly, fee+slippage 0.15%/side, 100 USDT per trade, 1,000 USDT wallet.
Strategies (code removed 1 Nov 2026): `PumpRider` (buy pumps), `PumpFade` (short pumps), `DumpRider` (short dumps), `MoodFilter` (the same with a
market-mood filter: longs only when >60% of coins are above their 20-day average, shorts only when <40%;
mood built by `user_data/build_breadth.py`).

| Strategy | 2025 | 2026 to 29 Oct |
|---|---|---|
| Buy pumps (PumpRider) | −90% | −90% |
| Buy pumps + mood | −68% | −90% |
| Buy big pumps (12%, 10x vol) + mood | −22% | −90% |
| Short dumps, trailing stop + mood | +51% | −59% |
| Short dumps, TP 6% / SL 10% | +121% | −79% |
| Short dumps, TP 6% / SL 10% + mood | **+129%** (76% won) | **−53%** (49% won) |
| Short pumps, TP 10% / SL 15% + mood | −64% | −79% |

Shorting dumps worked in 2025 and failed in 2026 even with the mood filter, so there is no reliable edge.
The PumpRider paper bot (`run_pump.ps1`) was stopped and its code removed on 1 Nov 2026 (3 trades, −0.76 USDT); its trade history and log are in `archive_old_pump/`.

## 3x leverage copy of PumpML (3 Nov 2026, REMOVED the same day at the user's request)
- Stopped after 1 trade (-24.76 USDT). Its script, config, database and log are in `archive_pumpml3x/`.
  `PumpML.py` keeps the leverage setting (`pumpml_leverage`, default 1), so the 1x bot is unchanged.
- Same model, entries and exits as the 1x bot below, on Binance USDT futures, isolated margin, **3x leverage**
  (`config-pumpml-3x.json`: `pumpml_leverage` 3, `stoploss` -0.30). 100 USDT margin per trade = 300 USDT position.
- Take-profit and stop stay at the same PRICE moves (+8%/-8% long, +6%/-10% short), so gains and losses on the margin
  are 3x. Checked: Sep-Oct 2026 backtest at 1x and 3x gives the same 107 trades and exit prices, margin returns exactly
  3.00x. At 3x, liquidation is about 30% away, beyond every stop.
- Paper only. Database `tradesv3.pumpml3x.dryrun.sqlite`, log `logs/pumpml3x.log`; web UI on port 8080 (config-private.json).
- Warning: the realistic 5-year test (below) loses with costs at 1x; leverage multiplies that loss as well as any gain.
- The Donchian spot bot's start script is kept in `archive_old_pump/run_bot_donchian.ps1` (it had no trades).

## Machine-learning paper experiment: `PumpML` (started 30 Oct 2026)
- Model: two gradient-boosting models (expected net return of going long / short after a move), trained on
  ~35,000 past pumps and dumps from 515 coins (Jan 2025 – Oct 2026). Features in `user_data/ml_features.py`.
- Walk-forward test (retrained monthly, unseen data, live-compatible features): 2,569 trades, 58.8% won,
  **+0.04% per trade**, +112 USDT on 1,000 (2025 +160, 2026 −48). An extra 0.05% slippage per side makes it negative.
- Trades when a coin moves ≥5% in an hour on ≥3x volume and the model expects ≥0.5% after fees; long +8%/−8%,
  short +6%/−10%, out after 24 h, 100 USDT each, max 5 open, 1x. Paper only.
- Start: `powershell -ExecutionPolicy Bypass -File run_pumpml.ps1`
- Retrain monthly:
  ```
  .venv\Scripts\freqtrade download-data -c user_data\config-pump.json --timeframes 1h --userdir user_data
  .venv\Scripts\python user_data\build_breadth.py
  .venv\Scripts\python user_data\ml_pumpdump.py --save
  ```
  then restart the bot.
- `config-pump.json` and `config-pump-dynamic.json` (exchange, coin list) are shared with this bot; `config-pumpml.json` picks the strategy.
- Later tests: the real ~2-minute entry delay plus funding fees turn the edge negative (`ml_delay.py`, −62 USDT over 19 months),
  and a 5-year walk-forward (`dl_5y.py` + `ml_5y.py`, Apr 2022 – Oct 2026) loses −1,033 USDT on 1,000 with those costs.
  Not fit for real money.

## Funding filter test (3 Nov 2026): FAILED, not added
- Rule fixed in advance (`user_data/PREREG_funding_filter.md`): skip shorts when the last funding rate is below -0.075%/hour.
- 5-year walk-forward, realistic costs (`user_data/funding_filter.py` -> `funding_filter_results.json`):
  baseline -1,033 USDT; with the filter -917 USDT. The 435 blocked shorts averaged -0.38% but t -1.07 (needed <= -2.0).
  Other thresholds (-0.0375%, -0.15%/hour) did worse than the baseline, so the gain is noise. Bot unchanged.

## 5-minute pump/dump detection test (3 Nov 2026): FAILED, nothing built
- Rules fixed in advance (`user_data/PREREG_5m_entry.md`); script `user_data/entry_5m.py` -> `entry_5m_results.json`.
- 100 coins, Nov 2025 - Jun 2026 (pick period), realistic costs: all four rules (fade/ride pumps, buy/short dumps)
  lose money on 5-minute detection (best t -1.02); none eligible, so the Jul - Oct 2026 holdout was not used.
  Spotting the move earlier did not help: 5-minute results were similar to or worse than hourly detection.

## Exit-level test (3 Nov 2026): FAILED, exits unchanged
- 9 take-profit/stop pairs fixed in advance (`user_data/PREREG_exits.md`), model retrained for each
  (`user_data/exits_test.py` -> `exits_results.json`). Pick period Apr 2022 - Dec 2024, realistic costs.
- Best: 6/10 for both sides +107 USDT (t +0.30) and 8/8 +62 (t +0.16); far from t >= 2.0. Tight (4/4, 4/8), wide
  (10/15, 12/12) and 24h-only exits lose clearly. None eligible, so the 2025-26 holdout was not used.
  Check: the current exits reproduce ml_5y.py exactly (-1,032.7 USDT over the full period).

## Open-trade limit test (5 Nov 2026): FAILED, keep 5

Pre-registered in `user_data/PREREG_trade_limit.md`, script `user_data/trade_limit_test.py`, results
`user_data/trade_limit_results.json`. The model, events and realistic costs are the same as ml_5y.py; L5 reproduces
its -1,032.7 exactly.

| Variant | Pick P&L (to 2024) | t | Holdout P&L (2025–26) | Holdout worst drawdown |
|---|---|---|---|---|
| L3 | +141.2 | 0.46 | -774.6 | -802 |
| **L5 (current)** | -110.8 | -0.30 | -921.9 | -1,168 |
| L8 | -453.4 | -1.06 | -1,143.9 | -1,444 |
| L10 | -695.0 | -1.54 | -1,549.0 | -1,767 |
| H2-5 (max 2 per hour) | +137.3 | 0.37 | -951.1 | -1,197 |
| H2-10 | -261.2 | -0.59 | -1,748.5 | -1,894 |

No variant reached t >= 2. More slots means more of a losing trade, so more loss. Fewer slots loses less only
because it trades less. The per-hour cap halves the worst single hour (-46 to -21) but does not change the result.

## Why the ML bot lost in 2025–26 (diagnosis, 5 Nov 2026)

These are the trades the bot would have taken, with a limit of 5 open and realistic costs.

| Period | Trades | Before costs | Costs (fees + delay + funding) | Net per trade | USDT |
|---|---|---|---|---|---|
| 2021–24 | 2,910 | +0.42% | -0.46% | -0.04% | -111 |
| 2025 | 1,550 | -0.12% | -0.50% | -0.62% | -955 |
| 2026 | 1,231 | +0.59% | -0.57% | +0.03% | +34 |

- The edge before costs (about 0.4–0.6% per trade) is smaller than the costs (about 0.5%), so a normal year
  breaks even and a bad year loses.
- 2025 was the bad year: before costs it was negative. Long stop-outs rose to 43% and shorts made 0.00%.
- Longs lost in every period. Shorts are the only part that ever made money, and their funding cost grew:
  -0.05% (2021–24), -0.11% (2025), -0.18% (2026).
- The universe grew from 258 to 506 coins and events quadrupled. The 5-slot limit takes the first signals, not the
  best ones, and the model's average predicted return halved (1.9% to 1.0%).

## Test: higher prediction bar and ranking (5 Nov 2026) - FAIL, no change
Pre-registered in `user_data/PREREG_signal_bar.md`, script `user_data/signal_bar_test.py`, results in
`user_data/signal_bar_results.json`. Pick period 2021-24, holdout 2025-26 (USDT, 100 per trade, max 5 open).

| Variant | Pick trades | Pick avg | Pick P&L | t | Holdout P&L |
|---|---|---|---|---|---|
| 0.5% as they come (current) | 2,910 | -0.04% | -110.8 | -0.30 | -921.9 |
| 0.5% ranked | 2,910 | -0.03% | -96.6 | -0.26 | -951.3 |
| 1.0% as they come | 2,484 | -0.12% | -290.0 | -0.85 | -224.0 |
| 1.0% ranked | 2,484 | -0.07% | -178.6 | -0.52 | -144.0 |
| 1.5% ranked | 2,015 | +0.12% | +247.8 | +0.80 | +153.8 |
| 2.0% ranked | 1,596 | -0.02% | -27.7 | -0.10 | -15.3 |

No variant reached t >= 2.0, so none was eligible. 1.5% made money in both periods, but its neighbours at 1.0%
and 2.0% lost, so it looks like a lucky setting rather than a steady effect. Ranking within the hour made almost
no difference. A higher bar lost less in 2025 mainly because it traded less.

## Test: limit-order entries (6 Nov 2026) - FAIL, keep market entries
Pre-registered in `user_data/PREREG_limit_entry.md`, script `user_data/limit_entry_test.py`, results in
`user_data/limit_entry_results.json`. All 19,399 chosen trades replayed on 1-minute Binance candles (none missing).
Market entry costs 0.15%, a limit fill 0.02%. A limit counts as filled only if the price trades through it.

| Entry | Fill rate | Pick trades | Pick avg | Pick P&L | t | Holdout P&L |
|---|---|---|---|---|---|---|
| Market at minute 2 (current) | - | 4,354 | -0.03% | -107.5 | -0.24 | -380.5 |
| Limit at hour open, skip if unfilled | 77% | 3,698 | -0.45% | -1,666.3 | -3.89 | -2,559.9 |
| Limit at hour open, else market at min 10 | 77% | 4,363 | -0.34% | -1,466.6 | -3.18 | -2,302.0 |
| Limit 0.2% better, skip | 72% | 3,544 | -0.32% | -1,128.0 | -2.68 | -2,221.3 |
| Limit 0.2% better, else market | 72% | 4,372 | -0.24% | -1,067.0 | -2.31 | -2,082.3 |
| Limit 0.5% better, skip | 63% | 3,267 | -0.07% | -226.0 | -0.56 | -1,923.6 |

Adverse selection is the whole story: the trades the limit order missed would have made +1.27% to +1.65% each with
a market entry, because they are the ones that moved the right way at once. Saving 0.13% in fees cannot pay for
losing those. Market entries stay.

## Forward shadow test: 1.5% bar, strongest first (from 6 Nov 2026)
Pre-registered in `user_data/PREREG_forward_1p5.md`. Since the 6 Nov 08:12 IST restart, the strategy appends every
scored event to `user_data/pumpml_events.jsonl` (logging only, trading unchanged). `user_data/forward_shadow.py`
builds the 1.5%-ranked shadow book and the current 0.5% book from it on 1-minute prices -> `forward_shadow.json`.
Events logged more than 15 minutes after their entry time (restart replays) are ignored. Review on 6 Feb 2027.

## PumpML15: separate paper bot for the 1.5% strongest-first rule (from 6 Nov 2026)
`user_data/strategies/PumpML15.py` subclasses PumpML: bar 1.5%, and when signals outnumber free slots in an hour the
highest predictions get them (via confirm_trade_entry). Own wallet (1,000 USDT), database
`tradesv3.pumpml15.dryrun.sqlite`, log `logs/pumpml15.log`. Started by `run_pumpml15.ps1` (restarts it if it stops).
Dashboard tab "Crypto ML 1.5% strongest-first". The pre-registered verdict still comes from `forward_shadow.py`.
