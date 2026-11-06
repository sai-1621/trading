# Strategy Backtest 2016–2026

A separate project: tests the trading bots' daily strategies on NSE (NIFTY 100) and US (S&P 500) stocks from
January 2016 to today, plus a "trained" bot that learns which strategy (or cash) works in each market condition.

```
cd "D:\PyCharmMiscProject\begin\backtest_2016"
..\.venv\Scripts\python run_backtest.py      # downloads prices (cached 24 h) and writes results/results.json
..\.venv\Scripts\python build_page.py        # writes results/backtest-2016.html
```

- Strategies and the walk-forward training live in `regime_engine.py` (this project's own copy).
- Prices are split- and dividend-adjusted. Signals at the close trade at the next open, with costs on every buy and sell
  (NSE 0.175% per side, US 0.04% per side). No tax.
- The trained bot learns on 2016–2018 and is retrained every quarter on everything before it, trading from 2019.
- Stocks are today's index members (survivorship bias: results look better than reality).
- The intraday opening-range strategy can't be tested this far back (free 5-minute data covers only 60 days).

Results on 27 Oct 2026, per year since 2016 (worst fall):

| | NSE | US |
| --- | --- | --- |
| Index | 10.2% (38%) | 14.8% (34%) |
| Buy and hold every stock | 22.7% (38%) | 18.1% (38%) |
| Best strategy: above 200-day average | 14.5% (13%) | 9.3% (13%) |
| Trained bot, from 2019 (hold, same years) | 22.5% (24.6%) | 19.9% (19.6%) |

## Holding for weeks (`swing_test.py`, run 29 Oct 2026)
`..\.venv\Scripts\python swing_test.py` → `results/swing.json`. Fixed textbook settings, no tuning.

| | per year | worst fall | to 2022 | 2023 on |
|---|---|---|---|---|
| NIFTY 50 (price), 2012– | 10.7% | 38% | 12.5% | 6.1% |
| India sectors, top 3 by 6-month return, NIFTY > 200d | 14.3% | 32% | 11.0% | 23.8% |
| SPY, 2000– | 8.3% | 55% | 6.2% | 21.7% |
| US sectors, top 3 by 12-1 month return | 12.2% | 52% | 11.6% | 16.2% |
| US sectors, top 2 by 6-month return, SPY > 200d | 9.6% | 24% | 10.5% | 4.5% |
| MTUM vs SPY, 2014– (real momentum ETF) | 14.8% vs 13.7% | 34% vs 34% | | |
| NIFTY 200 Momentum 30 ETF vs NIFTYBEES, Aug 2022– | 7.9% vs 7.4% | | | |

Stock-level momentum on today's members (30–40% a year) is mostly survivorship bias: equal-weighting all of today's
members already gives 22.7% (NSE) and 18.1% (US). Breakout trend-following (50-day high / 20-day low) did worse
than that on NSE (16.8%). Conclusion: holding for weeks has a real but small edge (about 1–4% a year over the
index, not every period), and monthly switching makes gains short-term for tax.
