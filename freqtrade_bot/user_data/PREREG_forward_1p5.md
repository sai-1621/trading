# Pre-registered forward test: 1.5% bar, strongest first

Written 6 Nov 2026, before any forward data exists.

## Why
In `signal_bar_test.py` (5 Nov 2026), "1.5% bar, ranked" was the only variant that made money in both periods: +247.8
USDT in 2021-24 and +153.8 in 2025-26. But t was only 0.80, and the 1.0% and 2.0% bars both lost, so it may be luck.
All past data has now been looked at, so the only fair test is new data.

## How
- The live PumpML strategy appends every scored event to `pumpml_events.jsonl`. This only logs; trading is unchanged.
- `forward_shadow.py` picks trades from that log under two rules:

  | Rule | Bar | Order within an hour |
  |---|---|---|
  | Shadow | 1.5% | highest prediction first |
  | Baseline | 0.5% (the current rule) | as they come |

- Each rule has its own book: 100 USDT per trade, at most 5 open.
- Finished trades are replayed on 1-minute Binance futures candles:
  - market entry at minute 2,
  - 0.15% cost per side,
  - long TP 8% / SL 8%, short TP 6% / SL 10%, 24 h hold,
  - funding from Binance.
- Only events logged from the restart onward count. No backfill.

## Decision (fixed now)
Review on **6 Feb 2027**. If the shadow rule has fewer than 100 closed trades by then, extend month by month until it
has 100, up to 6 May 2027.

The shadow rule **passes** if all three hold:
1. its forward P&L > 0,
2. its forward P&L > the baseline's forward P&L over the same events, and
3. per-trade t >= 1.0.

On a pass, the paper bot switches to the 1.5% ranked rule and stays on paper. A pass is not readiness for real money.
Otherwise the idea is dropped.
