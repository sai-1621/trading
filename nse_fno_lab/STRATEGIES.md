# NSE F&O strategy specs

Three rule-based strategies built to the brief: capital Rs 3,00,000, max daily loss 1.5% (Rs 4,500), max 3 trades a
day, broker Angel One SmartAPI, 5-minute bars. 2026 constraints as given: NIFTY lot 65 with Tuesday weekly expiry,
SENSEX lot 20 with Thursday weekly expiry, BANKNIFTY lot 30 and FINNIFTY lot 60 monthly only, options STT 0.15% on
sell premium, futures STT 0.05%, no new positions 09:15–09:20, everything flat by 15:15.
Every number below can be changed in `config.py` / `.env`.

**Backtest verdict (1 Nov 2026, round 3), details in the Results section:**

| strategy | data tested | result |
| --- | --- | --- |
| 1. Index option buying (leg 1 + leg-2 flag) | 40 sessions x NIFTY, BANKNIFTY, SENSEX, 5-minute | **no edge shown at any threshold**: pooled profit factor 0.81–1.14, every t within ±0.5; the flag fired 4 times. Needs 500+ sessions (broker download) |
| 2. Expiry-day iron fly | 3 years hourly, model prices | break-even needs real ATM premium ≥ **0.57x** VIX price with flat wings, **0.68x** at 1.10x wing skew, **0.84–1.03x** at 1.20x, and **never** at 1.35x. The live logger measures both numbers |
| 3 (old). 09:30 RS breakout | 50 sessions, 5-minute | −Rs 42,719 (−14%), t = −4.4 |
| 3A. Swing breakout, no time cap, NIFTY > 50-day SMA, close ≥ 85% of 52-week high | 10 years daily, top 50 F&O | +Rs 90,943, +0.19R a trade, profit factor 1.28, positive in both halves; **t = 1.54 < 2.0, so strategy 3 is FROZEN** (governance rule). 7.4% a year incl. 6% on idle cash vs NIFTY 11.1% |
| 3B. VWAP first pullback | 50 sessions, 5-minute | dropped: −Rs 38,750; tight stops mean charges eat 0.64R a trade |

None is ready for real money. Next steps:
- Strategy 2: run the full-day real-price paper tracker on every expiry (`log_expiry_chain.py --loop`).
- Strategy 1: run the pre-registered climax test once on 3 years of broker data.
- Strategy 3: frozen and shelved on 1 Nov 2026. Do not re-tune it on the same 50 stocks.

---

## Shared rules

### Charges (per order, `costs.py`)
| item | options | futures | equity intraday | equity delivery |
| --- | --- | --- | --- | --- |
| brokerage (Angel One) | Rs 20 | Rs 20 | lower of Rs 20 / 0.1% | lower of Rs 20 / 0.1% |
| STT | 0.15% of sell premium; 0.15% of intrinsic if exercised | 0.05% sell | 0.025% sell | 0.1% both sides |
| exchange | NSE 0.03503% (+0.0005% IPFT), BSE 0.0325% of premium | 0.00173% | 0.00297% | 0.00297% |
| SEBI | 0.0001% | 0.0001% | 0.0001% | 0.0001% |
| stamp (buy side) | 0.003% | 0.002% | 0.003% | 0.015% |
| GST | 18% of brokerage + exchange + SEBI | | | |
| slippage (assumed) | 0.1% of price per side | 0.1% | 0.1% | 0.1% |

**Break-even per lot** (`python costs.py`). NIFTY option bought and sold at the same premium:

| premium | 1 lot round trip | break-even move |
| --- | --- | --- |
| Rs 50 | Rs 61.41 | 0.95 pts (1.9%) |
| Rs 150 | Rs 89.82 | 1.39 pts (0.9%) |
| Rs 300 | Rs 132.43 | 2.04 pts (0.7%) |

Rs 150 in detail: brokerage 40.00 + STT 14.62 + exchange 6.93 + SEBI 0.02 + stamp 0.29 + GST 8.45 + slippage 19.50.
The flat Rs 40 brokerage is the biggest part. That is why the brief's "no micro-scalping" rule matters.
A NIFTY iron fly (4 legs in, 4 out, 1 lot, shorts ~Rs 120, wings ~Rs 25) costs about **Rs 254** a day.
A Rs 1,00,000 intraday stock round trip costs **Rs 282** (0.28%), of which Rs 200 is the assumed slippage.

### Option pricing and time
Black-Scholes in **trading time**: remaining session minutes plus 375 per later trading day, over 375 x 252.
Index variance happens while the market is open. Calendar time would make a same-day straddle about 2x too cheap.
Theta is per trading day; intraday theta = |daily theta| x minutes / 375.

### Kill switch and edge cases (section 4 for all strategies, `risk.py`)
| event | rule |
| --- | --- |
| daily loss reaches 1.5% of capital (incl. open MTM) | FLATTEN_ALL, no more trades today |
| 3 trades done, or 2 losing trades | no new entries |
| API timeout on an order | never resend blindly: look the order up by its unique tag first, resend only if absent |
| 3 API failures in a row | no new entries; open positions keep being managed |
| no market data for 45 s | no new entries |
| partial fill on entry | cancel the rest after the re-price steps; stops and size use the filled quantity |
| partial fill on exit | the rest goes out as a market order, retried up to 5 times; if still open, FLATTEN_ALL |
| limit order not filled / rejected | re-price LTP +/- 1 tick, then +2 ticks per step, 3 steps; then entries are dropped, exits go to market |
| rejected for margin | stop trying that order and log it |
| multi-leg entry | buy the hedge wings first, then sell the shorts; if any leg fails, close everything already filled |
| multi-leg exit | buy back the shorts first, then sell the wings (never left naked short) |
| India VIX +8% vs today's open, or +4% within 15 minutes | buyers: no new entries; sellers (strategy 2): flatten |
| file named `KILL` in the project folder | FLATTEN_ALL by hand |

---

## Strategy 1: NIFTY intraday directional option buying (`engines/opt_buy.py`)

### 1. Thesis and regime filter
Buying options loses to time decay on most days. It pays only when the index moves more than the option price
implies. On trend days, once price leaves the session VWAP with expanding 5-minute ranges, realised movement
exceeds implied movement for a while, so the option's gamma earns more than its theta costs. Option writers
re-positioning (puts added under the market, calls covered above it) confirms that the move has support.

Stay in cash unless all of these hold:
- India VIX (yesterday's close, or live) between 11 and 22.
- ADX(14) on 5-minute bars at least 20.
- 09:15–09:30 opening range no wider than 1.2x daily ATR(14), so the day's move isn't already spent. No minimum:
  a quiet open followed by expansion is the setup.
- ATR(5) / ATR(20) on 5-minute bars at least 1.2: volatility is expanding now.

### 2. State machine
States: FLAT → OPEN → COOLDOWN (2 bars) → FLAT; DONE_FOR_DAY after the trade or loss limits.
Signals are checked at each 5-minute bar **close** from 09:30 to 14:30. The order goes in at the next bar.

**LONG (buy CE)**, all must hold:
1. close > VWAP + 0.25 x ATR(20)
2. close > highest high of the previous 3 bars, and close > opening-range high
3. OI shift = [(change in put OI) − (change in call OI)] / total OI, at ATM ± 2 strikes over the last 3 bars, ≥ +0.02
4. **gamma/theta = 0.5 x gamma x ATR(20)² / (|daily theta| x 5/375) ≥ 2.0**.
   In trading time this is about (ATR / the 5-minute move VIX implies)², so it needs ATR ≥ ~1.4x the implied move
   (`verify_gamma_theta.py`: VIX 14 breakout 2.19 passes; ordinary bar 0.86, VIX 22 chop 0.22 rejected).

**SHORT (buy PE)**: the mirror image (below VWAP − 0.25 ATR, below the 3-bar low and the OR low, OI shift ≤ −0.02).

**Strike:** nearest expiry at least 2 calendar days away (no same-day or next-day options). The strike with
|delta| closest to 0.50 (ATM), IV taken from the live chain.
**Logged, not filtered:** edge ratio = [delta x move + 0.5 x gamma x move²] / [theta over 6 bars + premium x 0.15%
+ 0.5 pt], with move = 1.5 x ATR x √6. Optional minimum `S1_EDGE_RATIO_MIN`.

**LEG 2: midday flag continuation** (`flag_*` settings; same strike, sizing and exits):
1. A qualified impulse = a bar that met all leg-1 conditions including gamma/theta ≥ 2.0 (taken or not).
   Its base is the day's lowest low before it (highest high for shorts); its extreme H is tracked as it extends.
2. Flag = the 6–15 bars after H, in which:
   - every close stays above the 50% retracement of base → H, and above EMA(20) on 5-minute bars;
   - average volume is below 0.8x the impulse bars' average. Index data has no volume, so true range is used
     instead; live, use NIFTY futures volume.
3. Trigger: a bar closes above the flag's high, gamma/theta ≥ 1.6, before 14:30. One flag trade per impulse.
4. Stop: the flag's low (high for shorts), within the same % limits.

**Climax test (pre-registered on 1 Nov 2026, before any 3-year data; `S1_GT_MODE`):**
- `min` (current): enter when gamma/theta ≥ the threshold.
- `band`: enter only when 1.50 ≤ gamma/theta ≤ 1.90; skip higher scores as possible climax bars.
- `band_pullback`: as band; above 1.90, place a limit at close − 30% of that bar's range (close + 30% for
  shorts), valid for the next 2 bars.
  - The stop LEVEL stays where the signal put it; the target is 2R from the fill.
  - If the fill bar also reaches the stop, the stop counts.
- The leg-2 impulse definition (gamma/theta ≥ 2.0) is unchanged in every mode.
- Run once on the broker history with `python backtests\bt_opt_buy.py climax`. Its pooled t-stat is the answer;
  the thresholds are not to be retuned afterwards.

**Stops and exits:**
- Initial stop on the index: 1.2 x ATR(20), kept within 0.06%–0.18% of spot (15–45 points on NIFTY at 25,000;
  percent-based so BANKNIFTY and SENSEX get the same width).
- Premium stop: −30% of the entry premium (backstop).
- Target: 2R on NIFTY.
- After +1R the stop moves to the entry price, then trails at best close − 1.0 x ATR(20), adjusted each bar.
- Time stop: exit if the trade hasn't reached +0.5R within 6 bars (30 minutes).
- Square-off: 15:15.

### 3. Position sizing
Risk per trade = daily limit / losing trades allowed = 4,500 / 2 = **Rs 2,250**, charges included.
Loss per unit at the stop = min(|delta| x stop points + theta over the 6-bar time stop, 30% of premium).
Lots = the largest n where n x 65 x loss per unit + round-trip charges ≤ 2,250, and premium x n x 65 ≤ 25% of capital.

Example (VIX 14, 4 days to expiry, ATM premium Rs 156.3, delta 0.522, stop 30 pts):
- Loss per unit = 0.522 x 30 + 2.37 = Rs 18.04.
- 1 lot = 65 x 18.04 + 88 = Rs 1,261 at risk, so **1 lot** (2 lots would need about Rs 2,520).

### 5. Code
`StrategyEngine.calculate_indicators(df, daily, vix)`, `generate_signal(current_bar, option_chain)`,
`compute_position_size(capital, stop_loss_points, premium)`, `manage_open_position(tick_data)`.

---

## Strategy 2: expiry-day iron fly, NIFTY Tuesday / SENSEX Thursday (`engines/iron_fly.py`)

### 1. Thesis and regime filter
On expiry day, the ATM options' remaining value is pure time value, and it decays to zero by 15:30. When the
premium priced in is larger than the move the index actually makes over the rest of the day, a seller who sold
it keeps the difference (the volatility risk premium). Wings cap the loss and cut margin.
Measured over 3 years: the realised 10:15 → 15:15 move averaged **0.62x** (NIFTY) and **0.65x** (SENSEX) of a
straddle priced at India VIX (`backtests/realized_vs_model.py`). Whether real premiums are that rich is the open
question (see Results).

Stay in cash on expiry day if any of these:
- India VIX > 18, or VIX more than 7% above yesterday's close.
- Opening gap more than 0.8%.
- Open-to-entry range more than 0.6 x expected daily move x √(minutes since open / 30), where expected move =
  spot x VIX / 100 / √252.
- ATM straddle less than 1.05x the recent realised move: average |close − open| of the last 10 days x
  √(remaining session fraction).
- An event day listed in `EVENT_DAYS` (RBI policy, budget, election results, US Fed day).

### 2. State machine
FLAT → (09:45 checks pass) → OPEN → [RECENTRED once] → exit → DONE_FOR_DAY. One structure per day.

**Entry at 09:45:** K = ATM strike (nearest 50 for NIFTY, 100 for SENSEX). Sell K CE + K PE, buy wings at
K ± W, where W = ceil(straddle / step) x step (the wings sit just beyond both breakevens).
- Net credit C = straddle − wings.
- Breakevens = K ± C.
- Max loss per unit = W − C.

**Order of legs (margin):** buy both wings first, then sell the shorts. With hedges in place, SPAN treats the
position as defined-risk, so margin is about max loss + exposure. On exit, buy back the shorts first.

**Exits and adjustments**, checked every tick (re-centre and breakeven checks on 5-minute closes):
1. Profit take: open P&L ≥ 40% of credit → close all.
2. Stop: loss ≥ 60% of max loss → close all.
3. **A 5-minute close beyond a breakeven (first time):** if it's before 13:30 and the loss is under 50% of max loss,
   **re-centre**: close all four legs, open a new fly at the new ATM with the same lots (W recalculated).
4. A breach after the one re-centre, or after 13:30 → close all.
5. India VIX spike (kill switch) → close all.
6. 15:15 → close all (never hold short options into the close or exercise).

### Wing skew and the live calibration logger
Backtest pricing (no chain) uses a smile:
IV(K) = ATM IV x (1 + (wing_iv_mult − 1) x min(|K − spot| / W, 1)), with `S2_WING_IV_MULT` = 1.35 by default.
So the wings sit at 1.35x ATM IV at entry, and a strike's IV falls back toward ATM IV as spot moves to it.

`scripts/log_expiry_chain.py` (run at 09:45 and 10:15 on expiry day; no orders) records:
- `atm_ratio` = real ATM straddle / Black-Scholes straddle at India VIX (trading time)
- `credit_ratio_exec` = real iron-fly credit (shorts at the bid, wings at the ask) / model credit at flat VIX
- `credit_vs_smile_exec` = the same against the 1.35x-smile model
- `wing_skew` = the wings' implied vol / ATM implied vol

It records ±150 / ±200 (NIFTY), ±500 / ±700 (SENSEX) and the strategy's own width, and appends to
`logs/expiry_chain_log.csv`. Source `nse` needs no keys (NIFTY only); `dhan` or `angel` work for SENSEX.
First reading (Friday 30 Oct close, 3 Nov expiry, 2 days left, **not** an expiry-day reading): atm_ratio 0.91,
wing skew 1.01–1.04x.

**Full-day real-price paper tracker** (`--loop`, logic in `paper_fly.py`; start before 09:40 and leave it running).
Stops, profit-take and the VIX rule are checked every `--interval` seconds (default 60). The breakeven re-centre is
checked on 5-minute closes only. A summary row is written every 5 minutes, plus a row whenever a fly acts.
Quote sources: `--broker nse|dhan|angel|kite`. If it fails, every other source with keys in `.env` is tried in turn.
The NSE client reuses one session and refreshes its cookies with back-off on 401/403/429. SENSEX needs dhan, angel
or kite keys.
- At 09:45 and 10:15 it opens paper flies at the ATM strike, for each wing width (fixed widths + the rule width).
  Shorts are sold at the **bid**, wings bought at the **ask**.
- Every check until 15:25 it re-quotes the same 4 strikes and marks the cost to close at executable prices
  (shorts bought at the ask, wings sold at the bid).
- It applies the strategy's exits: profit 40% of credit, stop 60% of max loss, one re-centre on a breakeven breach
  before 13:30 (with real prices at the new strikes), VIX +8%, and 15:15 square-off.
- It records real per-order charges, the filters' pass/fail at entry, and a shadow "hold to 15:15" P&L.
- Output: `logs/paper_fly_marks.csv` (every mark) and `logs/paper_fly_results.csv` (one row per fly per day).
  State is kept in `logs/paper_fly_<SYMBOL>_<date>.json`, so a restart resumes. `--force` test runs write to
  `test_*` files.
- Limit: stops are checked once a minute. A spike that crosses and recovers within the minute is missed, and a
  breached stop fills at the next check's prices, often beyond the stop.
- P&L is per lot (1 lot by default, `--lots`), not sized by the strategy's risk rules.
- One fly costs about Rs 240 a day in charges for 1 NIFTY lot (8 orders). That is the bar it has to clear.

### 3. Position sizing and margin
- Stop per unit = 60% x (W − C).
- Lots = the largest n where n x 65 x stop per unit + charges for entry, one re-centre and exit ≤ Rs 4,500.
- Worst case at expiry (n x 65 x (W − C)) ≤ 2 x 4,500.
- Estimated margin ≤ 60% of capital.

Margin estimate per lot = max loss + 2% exposure + 2% extra expiry-day ELM (SEBI, Nov 2024) on the short notional.
Confirm with Angel One's margin API before live trading.

Example (NIFTY 25,000, VIX 13, 09:45 Tuesday):
- Legs: CE 81.3 + PE 75.4 = straddle 156.7, so W = 200; wings 16.9 + 14.7.
- C = 125.1, breakevens 24,875 / 25,125, max loss 74.9 per unit = Rs 4,869 per lot.
- Stop = 44.9 per unit = Rs 2,921 per lot + about Rs 470 charges, so **1 lot**.
- Margin about Rs 69,900.

### 5. Code
`calculate_indicators(df, daily, vix)`, `generate_signal(current_bar, option_chain)`,
`compute_position_size(capital, stop_loss_points, fly, spot)`, `manage_open_position(tick_data)` (returns
hold / exit / recenter), `recenter(...)`.

---

## Strategy 3: F&O stocks relative strength + volume breakout (`engines/rs_breakout.py`)

### 1. Thesis and regime filter
Stocks that open strong against the index on unusually heavy volume are being bought by informed or institutional
money. If that demand continues, the opening-range breakout runs. Optionally, the previous day's delivery %
above its 20-day average confirms accumulation.

Stay in cash if NIFTY's first 15-minute range is more than 1.2% or VIX > 24. Longs only when NIFTY is above its
VWAP at 09:30; shorts only when it is below.

### 2. State machine
At 09:30 (when the 09:25 bar closes), for the 50 most traded F&O stocks:
- RS = stock % change since yesterday's close − NIFTY's.
- Opening-volume ratio = 09:15–09:30 volume / average of the same window over the last 10 sessions.
- Score = z(RS) + z(log volume ratio).
- **Long candidates:** ratio ≥ 1.5 and RS ≥ +0.3%, top 3 by score. **Short:** ratio ≥ 1.5 and RS ≤ −0.3%, top 3.
- Entry (09:35–13:30, one trade per stock per day): a 5-minute close above the opening-range high **and** above
  VWAP (short: below the OR low and VWAP).
- Stop: the tighter of 1.5 x ATR(14) and the opening-range opposite side, kept within 0.4%–2.0% of price.
- Target: 2R. Break-even at +1R, then trail at 2 x ATR. Time stop: 12 bars under +0.5R. Square-off: 15:15.
- `S3_MODE=swing3`: a long that is ≥ +1R and closing in the top 25% of its range at 15:10 is carried, up to 3
  sessions (delivery charges). Shorts stay intraday. *(Swing mode is not backtested yet.)*

> **FROZEN (1 Nov 2026).** Governance rule set before the final test: keep 3A only if the 52-week-high version
> reached t ≥ 2.0. It reached t = 1.54. The code stays for reference; no further tuning on this universe.

### Variant 3A (`engines/rs_variants.py`), which replaced the 09:30 scalp
**3A: swing breakout, long only, delivery.**
- Scan near the close (15:15) over the top 50 F&O stocks by 20-day traded value. Conditions: close > the previous
  20-day high, close in the top 20% of the day's range, volume ≥ 2.0x its 20-day average, and
  **NIFTY close > its 50-day SMA** (`regime`).
- Buy the top 3 by volume ratio; at most 8 open; total value ≤ capital.
- Stop: 1.5 x daily ATR(14) below entry (a gap below the stop exits at the open).
- Exit at the close when it is below EMA(20) (`exit_rule` ema20) or below the previous 10-day low (low10).
- **No time cap** (`hold_days` None). The first version exited after 5 sessions.
- Final diagnostic only: `near_52w` 0.85 (close ≥ 85% of the 252-day high).
- Risk 0.5% of capital per trade incl. charges; a position ≤ 33% of capital.

**3B (dropped 1 Nov 2026):** intraday first pullback to VWAP. Its ~0.46% stops made charges 0.64R a trade
(−Rs 38,750 in 50 sessions); the code was removed.

### 3. Position sizing
- Risk per trade = 4,500 / 3 = **Rs 1,500**.
- Shares = floor(1,500 / (stop distance + round-trip charges per share)). The stop is ATR-based, so volatile stocks
  get smaller positions.
- Value capped at 33% of capital x 5 (MIS leverage).

Example: Rs 1,000 stock, Rs 10 stop, charges Rs 2.82 a share, so **116 shares** (Rs 1.16 lakh).

---

## Results (`backtests/`, 1 Nov 2026, round 3)

All on free Yahoo data, with charges. Short samples. Option prices come from a model, not real quotes.

**Strategy 1** (`bt_opt_buy.py`). 40 sessions per index; OI filter off (no free OI history); leg-2 flag at
gamma/theta ≥ 1.6. Percent-based stops (the first SENSEX/BANKNIFTY runs used NIFTY's fixed 15–45 points, which is
0.06% on SENSEX, and lost on almost every trade).

| gamma/theta ≥ | NIFTY trades / Rs | BANKNIFTY trades / Rs | SENSEX trades / Rs | pooled trades, win %, profit factor, Rs, t |
| --- | --- | --- | --- | --- |
| 1.50 | 13 / −29 | 17 / +3,597 | 19 / −2,846 | 49, 45%, 1.02, +722, 0.06 |
| 1.75 | 10 / −1,470 | 13 / +4,874 | 15 / +469 | 38, 45%, 1.14, +3,873, 0.35 |
| 2.00 | 5 / +1,260 | 12 / −2,246 | 12 / −951 | 29, 45%, 0.91, −1,938, −0.21 |
| 2.25 | 4 / +25 | 8 / −488 | 11 / −2,867 | 23, 43%, 0.81, −3,330, −0.43 |

No threshold is distinguishable from zero. The leg-2 flag fired 4 times in 120 index-sessions (NIFTY +122,
BANKNIFTY −1,433, SENSEX +2,499 over 2 trades). To judge it, download 500+ sessions:
`python -m data.broker_historical --broker dhan --symbol ALL --years 3` (or kite / angel), then rerun.

**Strategy 2** (`bt_iron_fly.py`, `s2_breakeven.py`). Hourly bars, about 150 expiries each over 3 years.
Break-even ATM premium ratio (real straddle ÷ straddle priced at India VIX) by wing skew:

| wing IV / ATM IV | 1.00 | 1.05 | 1.10 | 1.20 | 1.35 |
| --- | --- | --- | --- | --- | --- |
| NIFTY break-even ratio | 0.57 | 0.65 | 0.68 | 0.84 | none up to 1.04 (−Rs 27k at 1.0x) |
| SENSEX break-even ratio | 0.60 | 0.63 | 0.68 | 1.03 | none up to 1.04 (−Rs 1.2 lakh at 1.0x) |

Realised moves: the 10:15 → 15:15 move averaged 0.62x (NIFTY) and 0.65x (SENSEX) of the VIX-priced straddle
(`realized_vs_model.py`). With flat wings that is right at break-even. The strategy works only if real
expiry-day premiums are rich **and** the wings are close to flat. Log `atm_ratio` and `wing_skew` on 10+ expiries
before deciding anything.

**Strategy 3** (`bt_rs.py`, `bt_rs_variants.py`). 3A: daily bars 2017–2026, top 50 F&O stocks, delivery
charges, entry at the close, volume counted at 90% for the 15:15 scan. The variants were fixed before running:

| variant | trades | win % | PF | avg R | gross R | cost/R | total Rs | t | avg days | trades ≥ 3R | % a year on 3L | max DD | placebo R |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A 5-day cap, no regime (old) | 756 | 45 | 0.87 | −0.067 | 0.080 | 0.147 | −61,249 | −1.59 | 4.4 | 1.1% | −2.1 | 85,567 | −0.10 |
| B 5-day cap + regime | 630 | 45 | 0.91 | −0.050 | 0.099 | 0.149 | −35,572 | −0.98 | 4.4 | 1.3% | −1.2 | 64,936 | |
| **C no cap, EMA20 exit + regime** | 482 | 32 | 1.18 | **+0.121** | 0.271 | 0.150 | **+77,458** | 1.21 | 15.9 | 9.5% | 2.6 | 49,282 | −0.05 |
| D no cap, 10-day-low exit + regime | 449 | 30 | 1.19 | +0.130 | 0.279 | 0.149 | +79,373 | 1.20 | 19.1 | 10.2% | 2.7 | 46,628 | +0.07 |
| E no cap, EMA20 exit, no regime | 582 | 32 | 1.16 | +0.105 | 0.252 | 0.147 | +82,394 | 1.20 | 16.0 | 8.9% | 2.8 | 51,793 | |

By half (2017–2021 / 2022–2026):

| variant | 2017–2021 | 2022–2026 |
| --- | --- | --- |
| A | +16,879 (+0.03R) | −78,127 (−0.17R) |
| C | +40,632 (+0.11R) | +36,826 (+0.14R) |
| D | +61,514 (+0.18R) | +17,859 (+0.07R) |
| E | +70,113 (+0.17R) | +12,281 (+0.04R) |

- **Letting winners run is what changed the result.** Gross R went from 0.08 to about 0.27 against 0.15R of
  charges. About 10% of trades now reach 3R or more (1% with the cap).
- C is the only variant positive in both halves by a similar amount, and its random-pick placebo is −0.05R, so
  the entry adds about 0.17R. D's placebo is +0.07R: with a 10-day-low exit, even random entries made money in this
  rising market, so most of D's result comes from the exit, not the scan.
- **It does not beat the benchmark.** About 2.6% a year on Rs 3 lakh while using about a third of the capital on
  average, vs NIFTY's 11.1% a year (price only) for 2017–2026. Even scaled to full use (~8% a year) it trails.
  Its edge is a smaller drawdown (Rs 49k, 16%).
- t = 1.2: a 1-in-4 chance of a result this good from luck alone.
- Biased in its favour: today's F&O list (survivors) and entry at the exact close.
- Old 09:30 breakout: 85 trades, −Rs 42,719. 3B (dropped): 73 trades, −Rs 38,750, charges 0.64R a trade.

**3A final diagnostic** (`s3a_diagnostic.py`, rule fixed before running: keep only if t ≥ 2.0). Idle cash earns
6% a year in LIQUIDBEES in the second return column:

| variant | trades | win % | PF | avg R | total Rs | t | % a year | % a year + LIQUIDBEES | capital used | max DD | placebo R |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C | 482 | 32 | 1.18 | +0.121 | +77,458 | 1.21 | 2.6 | 6.5 | 34% | 49,282 | −0.05 |
| C + close ≥ 0.85 x 52-week high | 373 | 33 | 1.28 | +0.187 | +90,943 | **1.54** | 3.1 | 7.4 | 27% | 42,037 | −0.01 |

By half for the filtered version: 2017–2021 +Rs 46,688 (+0.18R), 2022–2026 +Rs 44,255 (+0.20R).
NIFTY buy-and-hold: 11.1% a year, price only (about 1.2–1.5% a year more with dividends).

**Verdict: t = 1.54 < 2.0, so strategy 3 is frozen.** This was the 6th 3A variant tested on the same universe,
so even a pass would have needed a fresh out-of-sample check.
