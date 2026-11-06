# futures_lab

Research on Binance USDT perpetual strategies, plus one paper bot. Paper only: no keys, no orders.

- `PREREG.md`, `search_v1.py` -> `results_v1.json`: 12 strategies (trend, momentum ranking; vol-targeted, max 3x).
  None passed; the near miss (55-day breakout, long only) failed 2025-26 (`results_nearmiss_v1.json`).
- `PREREG_v2.md`, `search_v2.py` -> `results_v2.json`: choppy-market fix (BTC above 200-day average + efficiency
  ratio >= 0.30). Fresh test 2018 - Oct 2021 (`dl_fresh.py`, data_fresh/): +389%, Sharpe 1.5, worst drop -31%,
  but win rate 53% vs 62% unfixed, so it failed one pre-set rule. Run on paper anyway at the user's request.

## Paper trend bot (started 3 Nov 2026)
`trend_bot.py` (logic in `trend_core.py`), started hidden by `run_trend.ps1` with the freqtrade venv.
Daily fill at the 01:00 UTC close (06:30 IST), 1,000 USDT paper, 0.10% per side + real funding.
Replaying `trend_core` over 2022-2026 matches the research backtest (2,182.47 vs 2,182.82 USDT).

    ..\freqtrade_bot\.venv\Scripts\python trend_bot.py --status
    ..\freqtrade_bot\.venv\Scripts\python -m unittest discover -s tests

Files: `trend_paper.json`, `trend_bot.log`. Dashboard tab "Crypto trend (paper)".
