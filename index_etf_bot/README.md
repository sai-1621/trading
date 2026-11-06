# Index ETF SIP Bot

Invests a fixed amount every month in index ETFs on NSE through Angel One, and holds. Paper mode by default.
New money goes to whatever ETF is furthest below its target weight, so it stays balanced without selling.

## Backtest: Rs 10,000 a month from January 2016 (after charges, before tax)

| Portfolio | Invested | Worth (Oct 2026) | Yearly return (XIRR) | Worst fall |
| --- | --- | --- | --- | --- |
| NIFTY 50 (NIFTYBEES) | 13.0 L | 23.1 L | 10.2% | 36% |
| NIFTY 50 70% + Nasdaq 100 30% (MON100) | 13.0 L | 31.3 L | 15.4% | 32% |
| NIFTY 50 60% + Nasdaq 100 25% + Gold 15% | 13.0 L | 32.6 L | 16.2% | 28% |
| NIFTY 50 50% + NIFTY Next 50 50% | 13.0 L | 24.5 L | 11.2% | 35% |
| NIFTY 50, paused below its 200-day average | 13.0 L | 22.7 L | 9.9% | 36% |

- Choosing the Nasdaq or gold mix because of this table is hindsight: US tech and gold did unusually well from 2016
  to 2026 (helped by a weaker rupee). Spreading across them reduced the worst fall, which is the sturdier reason.
- Pausing the SIP when the market is falling lowered returns: buying during falls is what makes a SIP work.
- Start date matters: the same NIFTY SIP started in 2024 or 2025 is below what was put in today. SIPs need years.
- Yahoo shows wrong prices for a few days around ETF unit splits (NIFTYBEES and GOLDBEES Dec 2019, MON100 Jun 2021);
  `data.py` removes them.

## Commands (in this folder)
```
.venv\Scripts\python sip.py --status      holdings, invested, worth, XIRR, next SIP date
.venv\Scripts\python sip.py               run (one copy at a time); run_bot.ps1 restarts it after a crash
.venv\Scripts\python backtest.py          re-run the backtest
.venv\Scripts\python readiness.py         go-live checks
```
Settings are in `.env`: `ETFS` (e.g. `NIFTYBEES:70,MON100:30`), `SIP_AMOUNT`, `SIP_DAY`, `PAUSE_BELOW_200DMA`.

## Going live
Set the Angel One keys and `STATIC_IP` in `.env`, run `test_order.py` once in market hours (buys 1 real unit, kept),
check `readiness.py` passes, then `LIVE_TRADING=true`. Orders are delivery (CNC): units go to your demat and stay.
Keep at least the SIP amount in your Angel One balance before each SIP day; the bot tells you if it is short.
Tax only when you sell: long-term (over a year) equity gains 12.5% above Rs 1.25 lakh a year (2024 budget rules; gold
and the Nasdaq ETF are taxed differently). Check current rules.
