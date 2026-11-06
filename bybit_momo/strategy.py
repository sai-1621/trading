"""Flat base -> volume ignition (leg 1) and high-tight bull flag (leg 2) on 5-minute candles. Pure functions on numpy
arrays, used by BOTH the backtest and the live bot (the live bot runs scan() on the last ~400 closed bars of each
coin and acts on signals at the newest bar), so the two can never drift apart.

A value at bar i uses bars <= i only; signals are decided at bar i's close and filled from bar i+1.

Leg 1 (ignition) at bar i:
  base range of the previous 40 bars (max high - min low) / min low < 3.5%
  volume_i > 8 x mean volume of the previous 50 bars
  close_i > max high of the previous 40 bars, and (close - low) / (high - low) >= 0.75 (top 25% of the candle)
  stop: the ignition candle's low
Leg 2 (high-tight flag):
  impulse at bar i: close_i / lowest low of the last 15 bars >= 1.12 and some bar in those 15 had RVOL > 8
  -> the coin is "stalked"; its peak (highest high) and peak volume keep updating while price makes new highs
  10-25 bars after the peak: trigger when
    the pullback's lowest low (since the peak) stays above peak - 0.618 x (peak - impulse low)
    mean volume of the 8 bars before i < 25% of the impulse's biggest candle volume
    close_i > max high of those 8 bars and volume_i >= 2.5 x their mean volume
  stop: the low of those 8 bars
Exits (both legs), evaluated on every bar after the entry bar:
  initial stop (intrabar; a gap below it fills at the open)
  climax: high - EMA9 > 3 x ATR14 and upper wick >= 50% of the candle -> sell half at that candle's close (once)
  trail: close < EMA20 -> sell the rest at the close
"""
from dataclasses import dataclass, field

import numpy as np

P = {
    "base_bars": 40, "base_range_max": 0.035, "vol_sma": 50, "ign_rvol": 8.0, "ign_body_top": 0.75,
    "imp_bars": 15, "imp_gain": 0.12, "imp_rvol": 8.0, "flag_min": 10, "flag_max": 25, "fib": 0.618,
    "flag_bars": 8, "flag_vol_frac": 0.25, "flag_break_vol": 2.5,
    "ema_fast": 9, "ema_trail": 20, "atr": 14, "climax_atr": 3.0, "climax_wick": 0.5,
    "turnover_min": 5e5, "turnover_max": 5e8, "turnover_bars": 288,          # 24 h of 5-minute bars
}


def _prev_roll(a: np.ndarray, n: int, fn) -> np.ndarray:
    """fn over the previous n values (excluding the current one); nan until n values exist."""
    out = np.full(len(a), np.nan)
    if len(a) <= n:
        return out
    w = np.lib.stride_tricks.sliding_window_view(a, n)          # w[j] = a[j:j+n]
    out[n:] = fn(w[:-1], axis=1)
    return out


def ema(a: np.ndarray, n: int) -> np.ndarray:
    out = np.empty(len(a))
    k = 2 / (n + 1)
    out[0] = a[0]
    for i in range(1, len(a)):
        out[i] = out[i - 1] + k * (a[i] - out[i - 1])
    out[: n - 1] = np.nan
    return out


def atr(h, l, c, n=14) -> np.ndarray:
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    out[n] = tr[1:n + 1].mean()
    for i in range(n + 1, len(c)):
        out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


def indicators(d: dict, p: dict = P) -> dict:
    """d: open, high, low, close, volume, turnover (numpy). Returns the derived arrays."""
    o, h, l, c, v = d["open"], d["high"], d["low"], d["close"], d["volume"]
    x = {}
    x["base_hi"] = _prev_roll(h, p["base_bars"], np.max)
    x["base_lo"] = _prev_roll(l, p["base_bars"], np.min)
    x["vol_sma"] = _prev_roll(v, p["vol_sma"], np.mean)
    x["rvol"] = v / x["vol_sma"]
    rng = np.where(h > l, h - l, np.nan)
    x["close_pos"] = (c - l) / rng
    x["upper_wick"] = (h - np.maximum(o, c)) / rng
    x["ema9"] = ema(c, p["ema_fast"])
    x["ema20"] = ema(c, p["ema_trail"])
    x["atr"] = atr(h, l, c, p["atr"])
    t = d.get("turnover")
    x["turnover24h"] = _prev_roll(t, p["turnover_bars"], np.sum) if t is not None else np.full(len(c), np.nan)
    return x


@dataclass
class Signal:
    i: int                 # bar index of the decision (its close)
    leg: str               # "ignition" | "flag"
    close: float
    stop: float
    info: dict = field(default_factory=dict)


def in_universe(x, i, p=P) -> bool:
    t = x["turnover24h"][i]
    return bool(np.isfinite(t) and p["turnover_min"] <= t <= p["turnover_max"])


def leg1_mask(d, x, p=P) -> np.ndarray:
    c = d["close"]
    with np.errstate(invalid="ignore"):
        base = (x["base_hi"] - x["base_lo"]) / x["base_lo"] < p["base_range_max"]
        return base & (x["rvol"] > p["ign_rvol"]) & (c > x["base_hi"]) & (x["close_pos"] >= p["ign_body_top"]) & \
            (c > d["open"])


def impulse_mask(d, x, p=P):
    """(mask, impulse_low array): close / lowest low of the last imp_bars >= 1 + imp_gain with RVOL > imp_rvol inside."""
    n = p["imp_bars"]
    lo, c = d["low"], d["close"]
    low_n = np.full(len(c), np.nan)
    rv_n = np.full(len(c), np.nan)
    if len(c) >= n:
        low_n[n - 1:] = np.lib.stride_tricks.sliding_window_view(lo, n).min(axis=1)
        rv = np.nan_to_num(x["rvol"], nan=0.0)
        rv_n[n - 1:] = np.lib.stride_tricks.sliding_window_view(rv, n).max(axis=1)
    with np.errstate(invalid="ignore"):
        m = (c / low_n >= 1 + p["imp_gain"]) & (rv_n > p["imp_rvol"])
    return m, low_n


def flag_signals(d, x, p=P, start: int = 0, stalk_log: list | None = None) -> list[Signal]:
    """Stateful stalker over the bars (only loops near impulses). stalk_log gets the coins' stalk episodes."""
    h, l, c, v = d["high"], d["low"], d["close"], d["volume"]
    imp, imp_low = impulse_mask(d, x, p)
    out, i, n = [], max(start, p["vol_sma"]), len(c)
    fb = p["flag_bars"]
    while i < n:
        if not imp[i]:
            i += 1
            continue
        # a new stalk episode begins at impulse bar i
        w0 = max(0, i - p["imp_bars"] + 1)
        ilow = imp_low[i]
        peak_j = w0 + int(np.argmax(h[w0:i + 1]))
        peak = h[peak_j]
        peak_vol = float(np.max(v[w0:i + 1]))
        ep = {"start": i, "impulse_low": float(ilow), "end": None, "result": "expired"}
        j = i + 1
        while j < n:
            if h[j] > peak:                                  # impulse still extending
                peak, peak_j = h[j], j
                peak_vol = max(peak_vol, float(v[j]))
                j += 1
                continue
            since = j - peak_j
            fib = peak - p["fib"] * (peak - ilow)
            if l[peak_j + 1:j + 1].min() <= fib:
                ep["result"] = "broke the 0.618 level"
                break
            if since > p["flag_max"]:
                break
            if since >= p["flag_min"] and j - fb >= peak_j + 1 - 0:
                win = slice(j - fb, j)
                mv = float(v[win].mean())
                top = float(h[win].max())
                if mv < p["flag_vol_frac"] * peak_vol and c[j] > top and v[j] >= p["flag_break_vol"] * mv:
                    out.append(Signal(j, "flag", float(c[j]), float(l[win].min()),
                                      {"peak": float(peak), "impulse_low": float(ilow), "bars_after_peak": since,
                                       "flag_vol_vs_peak": round(mv / peak_vol, 3), "break_vol_x": round(v[j] / mv, 2)}))
                    ep["result"] = "triggered"
                    break
            j += 1
        ep["end"] = j
        if stalk_log is not None:
            stalk_log.append(ep)
        i = max(j, i + 1)                                    # next episode only after this one ends
    return out


def scan(d: dict, x: dict | None = None, p: dict = P, start: int = 0, universe: bool = True,
         stalk_log: list | None = None) -> list[Signal]:
    x = x if x is not None else indicators(d, p)
    sigs = []
    m1 = leg1_mask(d, x, p)
    for i in np.where(m1)[0]:
        if i >= start:
            sigs.append(Signal(int(i), "ignition", float(d["close"][i]), float(d["low"][i]),
                               {"rvol": round(float(x["rvol"][i]), 1),
                                "base_range_pct": round(float((x["base_hi"][i] - x["base_lo"][i]) / x["base_lo"][i] * 100), 2)}))
    sigs += flag_signals(d, x, p, start, stalk_log)
    if universe:
        sigs = [s for s in sigs if in_universe(x, s.i, p)]
    return sorted(sigs, key=lambda s: s.i)


def exit_event(d, x, j, pos, p=P):
    """Exit decisions for an open position on bar j (after the entry bar). pos: dict with stop, qty_left, partial_done.
    Returns a list of (fraction_of_original, price, reason)."""
    o, h, l, c = d["open"][j], d["high"][j], d["low"][j], d["close"][j]
    out = []
    if l <= pos["stop"]:
        px = o if o < pos["stop"] else pos["stop"]
        out.append((pos["left"], px, "stop"))
        return out
    if not pos["partial"] and np.isfinite(x["atr"][j]) and np.isfinite(x["ema9"][j]) and \
            h - x["ema9"][j] > p["climax_atr"] * x["atr"][j] and x["upper_wick"][j] >= p["climax_wick"]:
        out.append((0.5, c, "climax: half sold"))
        pos["partial"] = True
        pos["left"] -= 0.5
    if np.isfinite(x["ema20"][j]) and c < x["ema20"][j] and pos["left"] > 1e-9:
        out.append((pos["left"], c, "close below EMA20"))
    return out
