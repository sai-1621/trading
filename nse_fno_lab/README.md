# NSE F&O Strategy Lab

Three rule-based strategies for Indian markets, built as plug-in engines for an Angel One SmartAPI bot:

1. **NIFTY intraday option buying:** VWAP + change in put/call OI + ATR breakout + a gamma-vs-theta filter
2. **Expiry-day iron fly:** NIFTY Tuesday / SENSEX Thursday, with wings, margin-friendly leg order and a one-time re-centre
3. **F&O stocks relative strength + opening volume breakout:** 09:30 scan of the top 50 stocks

Full rules, sizing, break-even maths, kill-switch rules and backtest results: **[STRATEGIES.md](STRATEGIES.md)**.

> **Status (1 Nov 2026, round 4): research and paper only. No strategy has shown an edge you can trust.**
> - Strategy 1: no edge at any gamma/theta threshold on 3 indices x 40 sessions. The band / pullback "climax" test
>   is pre-registered and waits for the 3-year broker download.
> - Strategy 2 depends on the real ATM premium ratio and the wing skew. The full-day real-price paper tracker
>   (`scripts/log_expiry_chain.py --loop`) answers that from Tuesday 3 Nov.
> - Strategy 3: **frozen**. The final 3A test (52-week-high filter) reached t = 1.54, short of the agreed
>   t ≥ 2.0. 3B was dropped earlier.
>
> `LIVE_TRADING` stays off. The only thing running is the expiry-day **paper** bot (no orders).

## Expiry-day paper bot (running)
`expiry_bot.py`, started hidden by `run_expiry_bot.ps1` (watchdog, restarts after a crash), like the other bots.
Places no orders.
- Runs all the time and wakes at 09:30 on trading days. On a NIFTY expiry (Tuesday, holiday-shifted) it runs the
  full-day real-price paper tracker (`scripts/log_expiry_chain.py --loop`). SENSEX Thursdays run only when Dhan /
  Angel / Kite keys are in `.env`.
- Results: `logs/paper_fly_results.csv`. Every check: `logs/paper_fly_marks.csv`. Log: `logs/expiry_bot.log`.
- Dashboard: the "NSE expiry iron fly (paper)" tab at http://localhost:8800.
- Start it again after a reboot:
  `powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File "D:\PyCharmMiscProject\begin\nse_fno_lab\run_expiry_bot.ps1"`
- Stop it: end the `run_expiry_bot.ps1` PowerShell first, then `expiry_bot.py`.
- Status: `.venv\Scripts\python expiry_bot.py --status`.

## Files
| file | what it is |
| --- | --- |
| `config.py` | all settings (capital, risk, lot sizes, expiries, charges, strategy parameters), overridable in `.env` |
| `costs.py` | charges for options / futures / equity and break-even points; `python costs.py` prints the tables |
| `greeks.py` | Black-Scholes price, delta, gamma, theta, vega, implied volatility (vectorised, trading-time) |
| `nse_calendar.py` | IST clock, holidays, weekly / monthly expiries with holiday shifts, time to expiry in trading minutes |
| `indicators.py` | causal indicators: session VWAP, ATR, ADX, opening range, prior-day values |
| `engines/opt_buy.py`, `engines/iron_fly.py`, `engines/rs_breakout.py` | `StrategyEngine` classes with `calculate_indicators`, `generate_signal`, `compute_position_size`, `manage_open_position` |
| `risk.py` | daily risk book, kill switch, `OrderGuard` (timeouts, partial fills, re-price ladder, safe multi-leg orders) |
| `broker.py` | `PaperBroker` (used by tests) and `AngelBroker` (orders, option chain with OI; not yet run, no API keys set) |
| `data_yahoo.py` | free Yahoo data with a growing cache (5-minute bars only reach back 60 days) |
| `verify_gamma_theta.py` | prints the strategy 1 gamma/theta filter step by step |
| `engines/rs_variants.py` | strategy 3A swing breakout engine (time cap, regime and exit rule are settings) |
| `paper_fly.py` | real-price paper iron fly used by the logger's `--loop` mode (executable bid/ask, strategy exits, charges) |
| `data/broker_historical.py` | 1-3 years of 5-minute index candles from DhanHQ v2, Kite Connect or Angel One, in chunks, into `cache/hist/` |
| `scripts/log_expiry_chain.py` | expiry-day logger: one snapshot (real vs VIX-model premiums, wing skew) or `--loop` full-day real-price paper flies; no orders |
| `backtests/` | `bt_opt_buy.py` (3 indices x thresholds), `bt_iron_fly.py`, `s2_breakeven.py`, `realized_vs_model.py`, `bt_rs.py`, `bt_rs_variants.py`; outputs go to `backtests/results/` |
| `tests/` | 41 tests: charges, calendar, greeks, no look-ahead, sizing, order edge cases, iron fly re-centre, leg-2 flag, climax limit entry, downloader, paper fly, tracker (cookie refresh, fallback, simulated day), 3A rules |

## Commands
```
cd "D:\PyCharmMiscProject\begin\nse_fno_lab"
.venv\Scripts\python -m pytest -q tests
.venv\Scripts\python costs.py
.venv\Scripts\python verify_gamma_theta.py
.venv\Scripts\python backtests\bt_opt_buy.py
.venv\Scripts\python backtests\bt_iron_fly.py
.venv\Scripts\python backtests\bt_rs.py
.venv\Scripts\python backtests\realized_vs_model.py
.venv\Scripts\python backtests\s2_breakeven.py
.venv\Scripts\python backtests\bt_rs_variants.py
.venv\Scripts\python -m data.broker_historical --broker dhan --symbol ALL --years 3     (token in .env)
.venv\Scripts\python scripts\log_expiry_chain.py --symbol NIFTY --loop               (expiry day, start before 09:40; 60 s checks)
.venv\Scripts\python scripts\log_expiry_chain.py --symbol SENSEX --loop --broker dhan  (Thursday; needs Dhan/Angel/Kite keys)
.venv\Scripts\python backtests\bt_opt_buy.py climax                                     (after the broker download)
```

## Plugging an engine into a bot
```python
from engines.iron_fly import StrategyEngine
from risk import DailyRisk, KillSwitch, OrderGuard
from broker import PaperBroker            # AngelBroker once keys are set and paper results justify it

eng = StrategyEngine()
x = eng.calculate_indicators(bars_5m, daily=daily_bars, vix=vix_daily)   # every completed 5-minute bar
sig = eng.generate_signal(x.iloc[-1], option_chain)                      # chain: strike, cp, expiry, ltp, oi
if sig: ...  # OrderGuard.open_spread(legs) -> eng.on_entry(sig)
act = eng.manage_open_position({"ts": now, "spot": spot, "prices": leg_ltps, "bar_close": True})
```

## Not done yet
- A runner for strategy 1 (option buying). Strategy 2's paper bot is running; strategy 1 waits for the 3-year test.
- Real OI history (strategy 1) and NSE delivery % (strategy 3's optional filter).
- A backtest of strategy 3's original `swing3` mode (3A replaces it as the tested swing variant).
- Holidays for years other than 2026 (add them with `NSE_HOLIDAYS`).

## Margin / capital for the paper iron flies (added 4 Nov 2026)
`scripts/fly_margin.py` asks Angel One's margin calculator (read-only, no orders) what 1 lot of the NIFTY iron fly needs
on the next expiry for wings 150 and 200, and writes `logs/fly_margin.json`; the dashboard tab shows margin per fly,
the most a fly can lose, capital needed at the busiest moment, and return on that capital. Uses the Angel One bot's
login, so run it with that bot's Python from `begin`: `angelone_bot\.venv\Scripts\python nse_fno_lab\scripts\fly_margin.py`.
4 Nov 2026 (9 Nov expiry): about Rs 88,800 (wings 150) and Rs 90,000 (wings 200) per fly; 4 flies = about Rs 3.58 lakh.

## Fill-price bug fixed (4 Nov 2026)
On 3 Nov every paper fill used the last traded price instead of the bid/ask: NSE's option-chain-v3 sends the best
bid/offer as `buyPrice1` / `sellPrice1` and the tracker read the old names (`bidprice` / `askPrice`), getting 0.
Fixed in `scripts/log_expiry_chain.py` (test in `tests/test_tracker.py`). The 3 Nov results (+Rs 3,137) paid no
bid-ask spread; at 4 Nov spreads that is roughly Rs 300-360 per fly per round trip, about Rs 2,000 for that day's
6 round trips (4 flies + 2 re-centres), so the fair figure is nearer +Rs 1,000-1,200. From the next expiry the fills
are real bid/ask again.
