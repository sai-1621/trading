"""Pure rules shared by the bot and the tests (no exchange calls). Numbers match backtest_funding_arb.Params."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Rules:
    entry_rate: float = 0.0003          # per 8h equivalent
    entry_mean24: float = 0.0003
    exit_rate: float = 0.00005
    slots: int = 10
    margin_frac: float = 0.40           # perp margin / notional: survives ~+35% with 2% maintenance (as in the backtest)
    leverage: int = 3                   # set on the exchange (integer only); extra isolated margin is then added to 40%
    mmr: float = 0.02
    derisk_at: float = 0.15             # perp margin equity / notional below this -> de-risk
    hard_derisk_at: float = 0.08        # below this -> cut both legs, whatever else happens
    delta_band: float = 0.01            # rebalance when |spot - perp| / spot qty > 1%
    min_spot_dvol: float = 1e6
    top_n: int = 100
    max_pair_usdt: float = 2_000.0      # hard cap per pair (paper default capital is small on purpose)


def rate8(rate: float, interval_hours: float) -> float:
    """Scale a settlement rate to its 8-hour equivalent (Binance now settles many perps every 4h or 1h)."""
    return rate * 8.0 / max(1.0, min(8.0, interval_hours))


def entry_ok(r: Rules, last8: float, mean24_8: float) -> bool:
    return last8 >= r.entry_rate and mean24_8 >= r.entry_mean24


def exit_ok(r: Rules, last8: float) -> bool:
    return last8 < r.exit_rate


def spot_symbol(perp_base: str, spot_bases: set) -> tuple[str | None, float]:
    """'1000PEPE' -> ('PEPE', 1000): the perp price is 1000 x the spot price."""
    for pre, m in (("1000000", 1e6), ("1000", 1000.0), ("1M", 1e6)):
        if perp_base.startswith(pre) and perp_base[len(pre):] in spot_bases:
            return perp_base[len(pre):], m
    return (perp_base, 1.0) if perp_base in spot_bases else (None, 0.0)


def pair_size(r: Rules, equity: float, free_usdt: float) -> float:
    """Notional per leg (USDT): equity / slots, split into spot cost + perp margin, capped."""
    slot = min(equity / r.slots, free_usdt, r.max_pair_usdt * (1 + r.margin_frac))
    return max(0.0, slot / (1 + r.margin_frac))


def liq_price_short(margin: float, qty: float, entry: float, mmr: float) -> float:
    """Isolated short: liquidated when margin + qty*(entry - P) <= mmr * qty * P."""
    return (margin + qty * entry) / (qty * (1 + mmr))


def margin_ratio(margin: float, qty: float, entry: float, mark: float) -> float:
    """Perp margin equity / notional."""
    return (margin + qty * (entry - mark)) / (qty * mark)


def derisk_action(r: Rules, ratio: float) -> str:
    if ratio < r.hard_derisk_at:
        return "cut"            # reduce both legs now
    if ratio < r.derisk_at:
        return "top_up"         # sell part of the spot, move USDT to the perp margin, shrink both legs equally
    return "ok"


def delta_fix(r: Rules, spot_qty: float, perp_short_qty: float, step: float) -> float:
    """Perp quantity to SELL (+) or BUY back (-) so both legs match, rounded to the perp step; 0 inside the band."""
    if spot_qty <= 0:
        return -perp_short_qty
    gap = spot_qty - perp_short_qty
    if abs(gap) / spot_qty <= r.delta_band:
        return 0.0
    return math.floor(abs(gap) / step) * step * (1 if gap > 0 else -1)


def topup_resize(spot_qty: float, margin: float, entry: float, spot_px: float, mark: float, margin_frac: float):
    """New common quantity so the pair is back at margin_frac, keeping total pair equity (before costs)."""
    equity = spot_qty * spot_px + margin + spot_qty * (entry - mark)
    q2 = equity / (1 + margin_frac) / spot_px
    return q2, spot_qty - q2
