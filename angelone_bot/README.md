# Angel One NSE Day-Trader Bot

A fully automated intraday (MIS) bot for NSE stocks through **Angel One SmartAPI**, built on the rules of the
[day-trader skill pack](https://github.com/Jakeschincariol/day-trader-skill), with **TradingView** support.
Long only. Runs in **paper mode** (fake money, real prices) and refuses real money until `readiness.py` passes.

> **Results on 27 Oct 2026:** the opening-range rule lost about 0.2R per trade after charges on 20 unseen sessions
> (40 stocks). None of the swing strategies in `research.py` beat buy-and-hold over the last ~20 months.
> Do not trade these rules with real money. Paper trade, re-test later, or bring a new rule and test it the same way.

## How it trades (all times IST)
| step | skill | what it does |
| --- | --- | --- |
| 1 | Scanner | Before 9:15: the 30 most traded NIFTY 100 stocks (by 20-day traded value), tags 20-day breakout, bull flag, reclaim of the 20-day average, oversold, oversold bounce, and the uptrend filter |
| 2 | News | Today's Google News headlines per stock; **results or board meeting news = no new trade in that stock today** |
| 3 | Entry | Opening range 9:15–9:30 on 5-minute bars; the first bar that **closes** above it (or above the 20-day closing high) with volume at least the range average, between 9:30 and 14:30 |
| 4 | Exit plan | Stop at the range midpoint (ATR for the 20-day setup), rounded to the 5-paise tick; target 2R; out by 15:10 |
| 5 | Time Machine | 10,000 replays from the stock's last 60 days of 5-minute bars + the same rule on every past session. **Trades only if the odds beat break-even after charges** |
| 6 | Budget | Whole shares; the smallest of max loss (Rs 500 incl. charges), position cap, max order, cash after a 10% buffer |
| 7 | Risk | Daily loss limit, 3 trades a day, entry window, market open, duplicates, 2 open positions, news risk |
| 8 | Broker | Intraday market order on Angel One (or paper) |
| 9 | Alerts | Every 20 seconds: sells at the stop, the target or 15:10 (before Angel One's auto square-off, which costs extra) |

Every step writes its result to `trader/`; every decision is logged in `bot.log`. Not ported: the Pelosi tracker
(US Congress) and the SEC-based Earnings Analyst.

## Market-condition training (`regime_research.py`)
Labels every day bull/bear (NIFTY vs its 100-day average) and calm/volatile, learns which daily strategy (or cash)
did best in each condition, and is tested walk-forward (train on the past, trade the next quarter, retrain).
Result on 27 Oct 2026, 96 NIFTY 100 stocks, out-of-sample Jan 2024 – Oct 2026: **bot +22.3% vs buy-and-hold +31.8%**
(worst fall 19.2% vs 19.9%): fail. The training ended up choosing "hold" in every condition.

## Charges (per order, intraday; verify on angelone.in)
Brokerage lower of Rs 20 or 0.1% (min Rs 5), STT 0.025% on sells, NSE 0.00297%, SEBI 0.0001%, stamp 0.003% on buys,
18% GST on brokerage + exchange + SEBI. A Rs 40,000 round trip costs about Rs 61 (0.15%).

## Setup
```
cd "D:\PyCharmMiscProject\begin\angelone_bot"
.venv\Scripts\python bot.py --status      # this folder has its own Python environment (.venv)
```
Settings live in `.env` (copy of `.env.example`). Without Angel One keys the bot paper trades on Yahoo data,
which can lag. With keys it uses Angel One for **all** market data, even in paper mode: live 5-minute bars and
prices, the scanner's daily closes, the Time Machine's 60 days of 5-minute history, the trend filter and the
Nifty 50 index. If Angel One fails for one request (rate limit, session), that request falls back to Yahoo and
`bot.log` says so. Every 5-minute log line ends with `data: Angel One N, Yahoo N, fallbacks N`.
Check the connection read-only (no orders) with `.venv\Scripts\python bot.py --test-data RELIANCE`; it compares
Angel One's bars with Yahoo's. Tests: `.venv\Scripts\python -m unittest discover -s tests`.

| command | what it does |
| --- | --- |
| `.venv\Scripts\python bot.py` | run (one copy at a time) |
| `.venv\Scripts\python bot.py --status` | cash, PnL, positions, news risk |
| `.venv\Scripts\python bot.py --scan` | scanner + news only |
| `.venv\Scripts\python backtest.py --stocks 40` | test the intraday rule on 60 days, after charges |
| `.venv\Scripts\python research.py --stocks 100` | test swing strategies on 5 years of daily data |
| `.venv\Scripts\python readiness.py` | the go-live checklist |
| `.venv\Scripts\python bot.py --test-order SBIN` | one REAL 1-share buy + sell (market hours) |

Keep it running: `powershell -ExecutionPolicy Bypass -File install_task.ps1`, then `Start-ScheduledTask -TaskName "AngelOne Bot"`.
It sleeps outside market hours and on NSE holidays (2026 list built in; add later years with `NSE_HOLIDAYS`).

## TradingView
- `tradingview/orb_strategy.pine`: the bot's exact intraday rule. Open the Pine Editor, paste, add to a 5-minute NSE
  chart, and use the Strategy Tester for TradingView's own backtest.
- `tradingview/ma_trend_nifty.pine`: the daily trend strategy from `research.py`.
- **Webhook (optional):** set `TV_WEBHOOK=true` and a long random `TV_SECRET` in `.env`. TradingView only posts to
  public HTTPS addresses, so run a tunnel such as `cloudflared tunnel --url http://localhost:8787` and use
  `<tunnel URL>/tv` as the alert's Webhook URL (needs a paid TradingView plan). Put the same secret in the Pine
  script's input. Alerts go through the **same** exit plan, Time Machine, budget and risk checks; a sell/exit alert
  closes the position. `TV_ONLY=true` makes TradingView the only source of entries.

## Going live: only when the bot is ready
With `LIVE_TRADING=true` the bot refuses to start until `readiness.py` passes all five:
1. `backtest.py` (under 7 days old) made money after charges on the unseen check period, with the settings in `.env`
2. 30+ paper trades over 28+ days, profitable (`trader/paper_trades.jsonl`)
3. `bot.py --test-order SBIN` filled a real buy and sell
4. Angel One login works (API key, client code, PIN, TOTP secret in `.env`)
5. This computer's public IP is the **static IP registered with Angel One**: SEBI requires it for API orders
   (effective April 2026). Your home internet IP usually changes, so you need a static IP from your ISP or a server.

Intraday profits are taxed as speculative business income at your slab rate. Most people who day trade lose money.

## Paper momentum swing bot (forward test, since 2 Nov 2026)

A second, separate PAPER bot (`swing_bot.py`, started hidden by `run_swing.ps1`). It has no order code.
Rule (research/PREREG_swing_v2_momentum.md): buy at the next open after a close above the previous 20 closes,
sell at the next open after a close below the previous 20 closes. Rs 1,00,000 paper account, at most
5 positions of Rs 20,000 (strongest 20-day return first), delivery charges, 120 stocks frozen in
`swing_universe.json`, stocks need 200 days of history. Runs once per trading day after 16:00 IST and catches up
on missed days.

    .venv\Scripts\python swing_bot.py --status     the paper account
    .venv\Scripts\python swing_bot.py --once       one cycle now

Files: `trader/swing_paper.json`, `trader/swing_trades.jsonl`, `swing.log`. Why: 234 tested variants
(research/) found no intraday edge after costs; momentum made money on held-back 2025-26 data but did not
prove it (t 1.13), so it is being tested forward on new prices before any real money.
Replaying `swing_core` over 2025-26 matches the research backtest within 0.3% (tests/test_swing.py).
