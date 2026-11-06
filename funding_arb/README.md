# funding_arb: delta-neutral funding carry on Binance (long spot + short USDT-M perp)

**Verdict (2 Nov 2026): not worth running.** Over Jan 2021 – Oct 2026 the base case made 4.2% a year (Sharpe 1.79,
max drawdown −5.4%), **below USDC at 5%**. Almost all of it came in 2021 (+27%). From 2022 to 2026 it made
**−0.1% a year** (best variant, found after looking at results: +1.5%). Nothing runs in the background.

## Backtest (`backtest_funding_arb.py`)
- **Data:** Binance 1h perp candles + real funding settlements and 1h spot candles, Jan 2021 – Oct 2026. 349 perps
  that have a Binance spot pair (145 of the perps that were ever top-100 have none, mostly new meme/AI coins:
  those are often the ones with the highest funding, and they cannot be hedged with Binance spot).
  Coins delisted before Oct 2026 (LUNA, FTT, ...) are missing.
- **Rules** (fixed from the spec before the first run; details in the script's docstring): point-in-time top-100
  perps with ≥ $1M/day spot volume; enter when the last settlement AND the 24h mean are ≥ 0.03% per 8h
  (rates scaled to 8h, since most coins now settle every 4h); exit below 0.005%; next-hour execution; 10 pairs;
  perp margin 40% of notional (survives +35% at 2% maintenance); de-risk at 15%; liquidation checked on every
  hour's high.
- **Friction:** perp 0.05% / spot 0.10% taker fees, 0.05–0.10% slippage per leg, real funding paid when negative.
  No spot leverage, so no borrow cost.

| | annual return | Sharpe | Sharpe vs USDC | Sortino | max DD | pairs | avg hold | liquidations |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **base case** | **4.17%** | **1.79** | −0.34 | 2.12 | **−5.4%** | 181 | 27.9 d | 15 |
| entry 0.01%/8h | −22.5% | −6.43 | | | −81.3% | 4,379 | 4.5 d | 29 |
| entry 0.02%/8h | 4.24% | 2.04 | −0.35 | 2.57 | −6.7% | 227 | 25.2 d | 18 |
| entry 0.05%/8h | 4.58% | 2.07 | −0.17 | 2.49 | −4.4% | 120 | 33.2 d | 6 |
| no 24h persistence filter | 3.24% | 1.33 | −0.69 | 1.56 | −9.7% | 312 | 20.4 d | 19 |
| maker fees, no slippage | 5.15% | 2.22 | 0.07 | 2.67 | −4.4% | 181 | 27.9 d | 15 |
| 5 pairs | 3.37% | 1.03 | −0.46 | 1.10 | −10.7% | 119 | 23.9 d | 10 |
| 20 pairs | 4.76% | 3.04 | −0.14 | 4.61 | −2.6% | 288 | 32.5 d | 21 |
| margin 100% (1x short) | 3.56% | 1.56 | −0.60 | 1.74 | −7.9% | 170 | 29.5 d | 3 |
| margin 20% (survives ~18%) | 1.72% | 0.71 | −1.28 | 0.85 | −12.2% | 242 | 20.7 d | 78 |
| top-30 coins only | 5.14% | 3.04 | 0.09 | 4.12 | −2.7% | 118 | 38.3 d | 6 |
| liquidation on mark-price proxy | 4.17% | 1.79 | −0.34 | 2.12 | −5.4% | 181 | 27.9 d | 15 |
| *USDC benchmark (assumed constant)* | *5.00%* | | | | | | | |

The sensitivity rows were chosen in advance except "top-30 coins only", which was added to check the universe
setting (it was not part of the plan, so its slightly better result is not evidence of an edge).

**By year (base / top-30 + maker fees):** 2021 +27.4% / +27.9%, 2022 0.0% / 0.0%, 2023 −1.0% / +1.0%,
2024 +5.6% / +6.7%, 2025 −4.2% / −0.3%, 2026 to Oct −0.5% / 0.0%.

**Alt-rally windows (base):** 2021 alt season +20.7% (worst hourly drawdown −3.1%), 2021 Q4 top +3.5% (−3.3%),
2024 Q1 rally +4.6% (−0.7%), 2024 post-election −0.2% (−0.9%), 10 Oct 2025 crash week −0.3%.

**Where the base-case money went:** funding received +50.8k USDT, funding paid −0.9k, fees −5.0k, slippage −5.3k,
liquidations −13.9k (15 pairs, mostly squeezes where spot moved as hard as the perp: TRB Dec 2023 −26% of the
pair, ALPINE Sep 2025 −23% in its first hour). Start capital 100k, end 126.9k.

**Why it does not beat cash:**
1. **High funding is rare after 2021.** Most of the time the hedgeable coins pay Binance's baseline 0.01% per
   8h (about 11% a year on the perp notional, before costs and with 40% of the capital sitting as margin). On
   2 Nov 2026 the best spot-hedgeable rate in the top 100 was exactly that baseline.
2. **When funding is high, it is because a coin is squeezing.** Those are the moments the short gets liquidated:
   15 liquidations cost about a third of all the funding collected.
3. **Round trips are expensive relative to the carry:** about 0.5% in and out against about 0.1% a day at the
   0.03% threshold, so a pair needs ~5 days just to pay its costs (the 0.01% entry row shows what happens
   otherwise).
4. Idle cash earns nothing in the backtest. If idle cash also sat in USDC, the total would be roughly the USDC
   yield plus 0–1.5% a year since 2022.

## Bot (`funding_bot.py`, paper by default)
Async `ccxt` skeleton: scans the top-100 perps (real funding intervals from `fetch_funding_intervals`), buys spot
with an IOC limit (≤ 0.2% through the touch), shorts exactly the filled quantity (isolated, 3x, then extra margin
up to 40%), keeps both legs within a 1% delta band, tracks a funding ledger (exchange income history live,
settled rate x mark x quantity on paper), tops up or cuts both legs when the margin ratio tightens, exits below
0.005%, and unwinds everything on a `KILL` file. Rules live in `arb_core.py` (shared with the tests).
Smoke test 2 Nov 2026: one paper loop, no errors, no candidates (no coin above the threshold). **Not started as a
service.** The live order path has never run: test on the Binance testnet first.

```
cd "D:\PyCharmMiscProject\begin\funding_arb"
.venv\Scripts\python -m pytest -q tests                  # 13 tests
.venv\Scripts\python backtest_funding_arb.py             # base + sensitivity (~2 min); --base-only for the base
.venv\Scripts\python funding_bot.py --once               # one paper loop
.venv\Scripts\python funding_bot.py                      # paper loop every 60 s
.venv\Scripts\python funding_bot.py --status
.venv\Scripts\python dl_data.py                          # data refresh (throttled, pauses :58-:04 UTC)
```

## Files
| file | what it does |
| --- | --- |
| `backtest_funding_arb.py` | data assembly, hourly simulation, metrics, sensitivity table |
| `arb_core.py` | pure rules: 8h scaling, entry/exit, spot symbol mapping (1000PEPE -> PEPE x1000), sizing, liquidation price, delta band, de-risk |
| `funding_bot.py` | async paper/live bot |
| `dl_data.py` | spot 1h + 2021 perp/funding download |
| `tests/test_arb.py` | 13 tests: rules, funding accrual, exit timing, liquidation, de-risk, mark-proxy trigger |
| `results/` | report.json, trades_base.csv, equity_base.csv |
| `data/` | 298 MB (spot candles, 2021 perp extension, universe list) |
