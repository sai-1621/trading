# Interactive Brokers US Stock Day-Trader Bot

A fully automated intraday bot for US stocks through **Interactive Brokers**, built on the
[day-trader skill pack](https://github.com/Jakeschincariol/day-trader-skill) (which was written for US stocks),
with **TradingView** support. Long only. **Paper mode** by default; refuses real money until `readiness.py` passes.

> CoinDCX was the first choice of broker, but CoinDCX lists no US stocks (only crypto, plus gold and silver futures).

> **Results on 26 Oct 2026:** the opening-range rule lost 0.05–0.11R per trade after IBKR charges on 20 unseen
> sessions (40 stocks), about break-even before charges. The market-condition training (`regime_research.py`)
> learned to simply hold in every condition and trailed buy-and-hold (+125% vs +154%, Jan 2024 – Oct 2026).
> Do not trade these rules with real money.

## How it trades (New York time)
| step | skill | what it does |
| --- | --- | --- |
| 1 | Scanner | Before 9:30: the 30 most traded S&P 500 stocks (20-day dollar volume); tags 20-day breakout, bull flag, reclaim of the 20-day average, oversold, oversold bounce, and the uptrend filter |
| 2 | News | Yahoo headlines, SEC 8-K filings and Nasdaq's earnings calendar. **No new trade** in a stock that reports before today's open, reported after yesterday's close, or has a halt in the news. An after-close report today is fine (the bot is flat by 15:55) |
| 3 | Entry | Opening range 9:30–9:45; the first 5-minute bar that **closes** above it (or above the 20-day closing high), 9:45–15:30 (12:30 on early-close days) |
| 4 | Exit plan | Stop at the range midpoint (ATR for the 20-day setup), rounded down to the cent; target 2R; flat by 15:55 (12:55 on early closes) |
| 5 | Time Machine | 10,000 replays from the stock's last 60 days + the same rule on every past session; **trades only if the odds beat break-even after charges** |
| 6 | Budget | Whole shares; smallest of max loss ($50 incl. charges), position cap (75%), max order ($4,000), cash after a 10% buffer |
| 7 | Risk | Daily loss limit ($150), 3 trades a day, entry window, market open, duplicates, news risk, optional pattern-day-trader guard |
| 8 | Broker | Market order through TWS / IB Gateway (or paper) |
| 9 | Alerts | Every 20 seconds: sells at the stop, the target or the flat-by time |

NYSE holidays and 1:00 p.m. early closes for 2026–2027 are built in (from the skill pack); daylight saving is handled.

## Charges (IBKR Pro Fixed; verify on interactivebrokers.com)
$0.005 per share, $1 minimum, max 1% of the trade; on sells the SEC fee ($0.0000206 × value) and FINRA TAF
($0.000195 per share, max $9.79). A $4,000 round trip in a $40 stock costs about $2.10 (0.05%).

## Setup
1. Install **TWS** or **IB Gateway** and log in (paper account first: its account number starts with DU).
2. In TWS: Configure → API → Settings → tick "Enable ActiveX and Socket Clients"; note the port
   (TWS paper 7497, TWS live 7496, Gateway paper 4002, Gateway live 4001) and put it in `.env` as `IB_PORT`.
3. Without a live US market data subscription, keep `IB_DELAYED_DATA=true` (free 15-minute-delayed quotes).
   Without TWS at all, the bot still paper trades on Yahoo data.
4. Put your name and email in `SEC_USER_AGENT` to turn on the SEC 8-K check (SEC EDGAR asks callers to identify themselves).

| command (in this folder) | what it does |
| --- | --- |
| `.venv\Scripts\python bot.py` | run (one copy at a time) |
| `.venv\Scripts\python bot.py --status` | cash, PnL, positions, news risk |
| `.venv\Scripts\python bot.py --scan` | scanner + news only |
| `.venv\Scripts\python backtest.py --stocks 40` | test the intraday rule on 60 days, after charges |
| `.venv\Scripts\python regime_research.py --stocks 100` | market-condition training, walk-forward on 5 years |
| `.venv\Scripts\python readiness.py` | the go-live checklist |
| `.venv\Scripts\python bot.py --test-order F` | one 1-share buy + sell in whichever account TWS is logged into |

Keep it running: `powershell -ExecutionPolicy Bypass -File install_task.ps1`, then `Start-ScheduledTask -TaskName "IBKR US Bot"`.
TWS/Gateway must also stay running and logged in (IBKR logs it out once a day; enable auto-restart in its settings).

## TradingView
- `tradingview/orb_strategy.pine`: the bot's exact intraday rule for a 5-minute US chart (Strategy Tester = TradingView's backtest).
- `tradingview/ma_trend_spy.pine`: daily trend strategy with an SPY filter.
- Webhook: `TV_WEBHOOK=true`, a long random `TV_SECRET`, a tunnel such as `cloudflared tunnel --url http://localhost:8788`,
  and `<tunnel URL>/tv` as the alert's Webhook URL (paid TradingView plan). Alerts go through the same exit plan,
  Time Machine, budget and risk checks; a sell/exit alert closes the position. `TV_ONLY=true` = TradingView entries only.

## Going live: only when the bot is ready
With `LIVE_TRADING=true` the bot refuses to start until all four pass, and also refuses if TWS is logged into a paper account:
1. `backtest.py` (under 7 days old) made money after charges on the unseen period, with the settings in `.env`
2. 30+ paper trades over 28+ days, profitable (`trader/paper_trades.jsonl`)
3. `bot.py --test-order F` filled a buy and sell on the **live** account
4. TWS/Gateway connected and logged into the live account

Pattern-day-trader rule: FINRA removed the $25,000 minimum (Regulatory Notice 26-10, effective June 4, 2026; firms
may phase it in until October 20, 2027). If IBKR still applies it to your account, set `PDT_GUARD=true`.
For an Indian resident: money sent abroad goes under LRS (TCS may apply), and US share gains are taxed in India.
Most people who day trade lose money.

## Skip-rule test (5 Nov 2026): FAILED, keep the skip rule

Pre-registered in `PREREG_skip_rule.md`, script `skip_rule_test.py`, results `trader/skip_rule_results.json`.
Replayed the live opening-range rule on 100 stocks; the Time Machine was re-run for each signal using only earlier sessions.

| Decision period (3 Sep – 26 Oct, 37 sessions) | Signals | Avg R after costs | t |
|---|---|---|---|
| All signals | 928 | -0.111 | -3.14 |
| Taken by the Time Machine | 60 | -0.210 | -1.39 |
| Skipped by the Time Machine | 868 | -0.104 | -2.87 |

The skipped signals lost money reliably, so taking them would not help. The rule itself has no edge: the 60-day
backtest found the same. The Time Machine's score does not rank outcomes either (correlation -0.01), so it is
not adding anything; it mostly just keeps the bot out of a losing rule.

**Why the live "skipped, followed anyway" record looked good (+$525):** a recording bug. On 28 Oct the bot stopped
logging at about 13:30 ET, and 14 skipped trades were still open. They were closed the next morning at 09:54 ET at
the next day's prices instead of at 15:55 on the day, with "targets" of 3R–5.5R from the overnight move. Replayed
from the 5-minute bars, the 90 live skipped opening-range trades average +0.08R before costs, not +0.40R.
`journal.check_shadows` closes a previous day's shadow at the current price; it should settle it from that day's bars.

**Fixed 5 Nov 2026** (journal.py is shared with angelone_bot, same change in both):
- A shadow trade from an earlier day is now settled from that day's 5-minute bars (`journal.settle_from_bars`: stop
  first, then target, else the last close before the flat-by time) instead of at the next price seen.
- The same applies to a paper position left open past the close (`skills/alerts.py`); a live position is still sold
  at once.
- The 17 IBKR shadow records closed late (27–28 Oct) and 1 Angel One record (LT, 29 Oct) were re-settled.
  Each keeps its original values under `orig`; backups are `trader/shadow_trades.jsonl.bak-20261105`.
- IBKR skipped-trade total went from +$525.43 to -$28.75; Angel One went from -Rs 3,083 to -Rs 3,090.
- Tests: angelone_bot/tests/test_journal.py.
