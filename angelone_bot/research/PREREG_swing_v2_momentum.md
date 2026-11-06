# Swing v2, momentum: pre-registration (written 2 Nov 2026, before any momentum holdout number was computed)

## Disclosure
This is the SECOND look at the 2025-01-01 to 2026-10-30 holdout. The first (swing v1, the 2-day RSI pullback)
failed. Momentum was not chosen in v1 because v1's rule ranked by win rate. Its pick-period numbers
(2022-03 to 2024-12) were seen before writing this. A second look raises the chance of a lucky pass, so the
bar below is stricter than v1's.

## Variant (fixed by a rule, not by hand)
The family M variant with the highest pick-period t-stat in swing_v1_results.json:
M n20 low20 none none: buy the next open after a close above the previous 20 closes; sell the next open after a
close below the previous 20 closes. No stock or market filter. Same execution, costs and data as swing v1.

## Holdout pass rule (one look; all must hold)
1. average return per trade after costs > 0 with t-stat >= 2.0
2. the Rs 1,00,000 portfolio (5 slots of Rs 20,000, highest 20-day return first) makes money, AND
3. beats Nifty 50 buy-and-hold over the same dates (long-only momentum on today's index members is
   flattered by survivorship, so merely being positive is not enough), AND
4. max drawdown no worse than -25%.

## Also reported (not used to decide)
All 24 momentum variants on the holdout, to show whether the result depends on the exact settings.

## Outcome
Pass: propose a PAPER swing bot (delivery, no real money) for at least 3 months of forward testing.
Fail: report it, change nothing.
