# meta_lab: LightGBM meta-labeling on crypto perp breakouts (research only, no bot)

## Pre-registered rules (written 2 Nov 2026, before the first run)
- **Data:** Binance USDT-M futures, 515 coins currently listed, 1h candles resampled to 4h, Nov 2021 – Oct 2026,
  real funding rates. Coins delisted earlier are missing (survivorship bias, flatters longs).
- **Friction:** per side, taker fee 0.05% plus slippage 0.10%, so 0.30% a round trip, plus real funding on longs.
  A gap through the stop fills at the open; a bar that touches both stop and target counts as a stop.
- **Validation:** purged walk-forward. Each test quarter uses a model trained only on trades that **closed** more
  than 1 day before the quarter starts. Threshold chosen inside the training window (inner purged split). No K-fold.
- **Research OOS:** 2023-01-01 to 2025-12-31 (12 quarterly folds). All iterations are judged here.
- **Holdout:** 2026-01-01 to 2026-10-29. Run **once**, with the final frozen iteration, whatever it shows.
- **Pass (all on the meta-filtered slot portfolio):** profit factor ≥ 1.35; t of trade returns ≥ 2.0;
  t of weekly portfolio returns ≥ 2.0 (trades cluster on the same days, so the trade t overstates); payoff ratio
  ≥ 1.5. Note: win% × avg win / (loss% × avg loss) is algebraically the profit factor, so this rule means PF ≥ 1.5.
- **Placebo:** a random filter keeping the same number of trades per quarter must do clearly worse.
- **Every iteration is kept** (config.ITERATIONS), never edited after its run. The number of iterations tried is
  reported with the result, because each extra try on the same OOS makes a pass easier to get by luck.

## Result after 3 iterations (2 Nov 2026): FAIL, stopped. Holdout NOT used.
Research OOS 2023-01 – 2025-12 (iteration 3 has folds from 2024-01 only: daily data has too few trades to train
on 2022 alone). Costs as above. "Meta portfolio" = the trades the model kept, through 10 slots of 1,000 USDT each
(fixed size, no compounding, so a drawdown can pass -100% of the starting 10,000).

| | iter 1 | iter 2 | iter 3 |
| --- | --- | --- | --- |
| setup | 4h Donchian-20, stop 2 ATR / target 3 ATR / 30 bars, all coins ≥ $5M/day | 4h, no target: 3-ATR chandelier trail, 60 bars; rank features; keep top 30% | 1d, top-40 liquidity, BTC > EMA200 gate, trade-specific features only |
| primary: trades, **gross** / net per trade, PF | 41,061, **−0.37%** / −0.69%, 0.82 | 41,061, **−0.65%** / −0.95%, 0.74 | 803, **−2.82%** / −3.53%, 0.59 |
| meta, all kept: trades, net per trade, PF | 10,376, −0.98%, 0.74 | 12,719, −0.84%, 0.75 | 372, −5.34%, 0.41 |
| **meta portfolio: trades** | 1,873 | 2,412 | 194 |
| win % | 39.7 | 30.7 | 25.8 |
| avg win / avg loss | +8.8% / −6.4% | +9.7% / −5.2% | +18.2% / −12.2% |
| **profit factor** (= payoff ratio) | 0.91 | 0.83 | 0.52 |
| max drawdown (of 10,000) | −72% | −147% | −86% |
| net return | −67% | −153% | −85% |
| t (trades) / t (weekly) | −1.81 / −1.05 | −2.96 / −1.83 | −3.52 / −1.61 |
| random filter, same count: PF mean / 95th pct | 0.84 / 0.89 | 0.79 / 0.86 | 0.62 / 0.79 |
| OOS AUC per fold: mean (min–max) | 0.525 (0.45–0.58) | 0.505 (0.35–0.57) | 0.506 (0.38–0.83) |
| benchmark: BTC buy and hold | +430%, max DD −34% | same | +427%, −32% |

Targets: PF ≥ 1.35, t ≥ 2.0, payoff ≥ 1.5. No iteration came close.

**Diagnosis**
1. **The primary signal has no edge before costs** in any version (gross −0.4% to −2.8% a trade). Meta-labeling can
   only filter trades; it cannot create an edge the primary does not have.
2. **Breakout timing is no better than random:** random entries on the same coins, bars and exits scored the same
   (daily: −0.10R random vs −0.11R breakout; 4h: −0.12R vs −0.15R). The same code is positive on BTC alone
   (+0.15R to +0.39R a trade), so the engine is not the problem: altcoins bled against BTC in 2023–2025, and alt
   breakouts trailed simply holding BTC by 1.1% a trade.
3. **The model cannot rank trades:** fold AUCs average 0.50–0.53, and in iteration 1 the top-probability decile was
   the worst decile. With regime features it learned which past quarters were good (BTC trend, breadth, coin
   "age", which is a calendar proxy because the data starts Nov 2021). Without them (iteration 3) nothing was left.
4. The model's kept trades beat the random filter only in iteration 1 (PF 0.91 vs 95th pct 0.89), and were still
   losing.

**Why stop at 3:** more iterations on the same OOS would be a search through strategy variants until one passes by
luck. The holdout is kept for a candidate that first passes the research OOS.

**Options for a next study (each a new pre-registered hypothesis, not iteration 4 of this one):**
- Trend following on BTC/ETH only (where the primary is already positive), with ML only as a position-size filter.
- Funding-rate / open-interest signals (the data has funding; OI would need downloading).
- Cross-sectional, market-neutral: long strongest / short weakest coins, removing the alt-vs-BTC drift that sank
  every long-only version.

## Files
| file | what it does |
| --- | --- |
| `config.py` | costs, dates, pass rules, LightGBM settings, every iteration's settings |
| `data.py` | 1h → 4h and 1d panels (cache/) |
| `features.py` | point-in-time indicators, features, cross-sectional ranks |
| `primary.py` | signal and trade simulator (next-open entry, stop before target, gaps, fees, slippage, funding) |
| `model.py` | purged walk-forward LightGBM meta-labeler, inner threshold choice |
| `portfolio.py` | slot portfolio, mark-to-market equity, metrics, pass check |
| `run.py` | one iteration: `.venv\Scripts\python run.py --iter 3` (add `--holdout` only for a final frozen candidate) |
| `tests/` | 5 tests: no look-ahead in features, next-open entry and costs, stop before target, purging, trail stop |
| `results/` | iter1–3 JSON reports and OOS trade lists |
