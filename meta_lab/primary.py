"""Primary model (the technical signal) and the trade simulator with friction.

Signal on bar t's close -> enter at bar t+1's open (+ slippage, + taker fee). Exits are checked bar by bar; the stop is
checked before the target, so a bar that touches both counts as a stop (conservative). A gap through the stop fills at
the open. Longs pay the real Binance funding for every funding stamp inside the holding period."""
import numpy as np
import pandas as pd

import config as C

H4 = pd.Timedelta(hours=4)


def signals(d: pd.DataFrame, cfg: dict) -> np.ndarray:
    c, dh = d["close"].values, d["dh"].values
    ok = d["liquid"].values & d["gate"].values.astype(bool) & np.isfinite(d["atr"].values) & np.isfinite(dh)
    if cfg["signal"] == "donchian":
        prev_in = np.r_[False, c[:-1] <= dh[:-1]]
        s = ok & (c > dh) & prev_in
    else:
        raise ValueError(cfg["signal"])
    s[-1] = False
    return np.flatnonzero(s)


def simulate(d: pd.DataFrame, fund: pd.Series, idx: np.ndarray, cfg: dict) -> list:
    o, h, l, c = (d[k].values for k in ("open", "high", "low", "close"))
    a, e20 = d["atr"].values, d["ema20"].values
    t = d.index
    n = len(d)
    bar = pd.Timedelta(cfg.get("tf", "4h"))
    f_t = fund.index.values if len(fund) else np.array([], dtype="datetime64[ns]")
    f_cum = np.r_[0.0, np.cumsum(fund.values)] if len(fund) else np.array([0.0])
    out = []
    for i in idx:
        e = i + 1
        px0 = o[e]
        stop = px0 - cfg["stop_atr"] * a[i]
        target = px0 + cfg["target_atr"] * a[i] if cfg.get("target_atr") else np.inf
        last = e + cfg["max_bars"] - 1
        if last >= n:
            continue                                   # not enough future bars to finish the trade: skip
        exit_px, j_exit, why, at_open = None, None, None, False
        for j in range(e, last + 1):
            if j > e and o[j] <= stop:
                exit_px, j_exit, why, at_open = o[j], j, "stop_gap", True
                break
            if l[j] <= stop:
                exit_px, j_exit, why = stop, j, "stop"
                break
            if j > e and o[j] >= target:
                exit_px, j_exit, why, at_open = o[j], j, "target_gap", True
                break
            if h[j] >= target:
                exit_px, j_exit, why = target, j, "target"
                break
            if cfg["exit"] == "ema20" and c[j] < e20[j] and j > e:   # close below EMA20 -> out at that close
                exit_px, j_exit, why = c[j], j, "trail"
                break
            if cfg["exit"] == "chandelier":         # after bar j: stop ratchets to highest high - k*ATR (used from j+1)
                stop = max(stop, h[e:j + 1].max() - cfg["trail_atr"] * a[j])
        if exit_px is None:
            exit_px, j_exit, why = c[last], last, "time"
        if why == "stop" and stop > px0 - cfg["stop_atr"] * a[i]:
            why = "trail_stop"
        t_in = t[e]
        t_out = t[j_exit] if at_open else t[j_exit] + bar
        if len(f_t):
            k0 = np.searchsorted(f_t, t_in.to_datetime64(), side="right")
            k1 = np.searchsorted(f_t, t_out.to_datetime64(), side="right")
            fsum = f_cum[k1] - f_cum[k0]
        else:
            fsum = 0.0
        ent_eff = px0 * (1 + C.SLIP)
        ex_eff = exit_px * (1 - C.SLIP)
        gross = exit_px / px0 - 1
        net = ex_eff / ent_eff - 1 - 2 * C.FEE - fsum
        risk = (px0 - (px0 - cfg["stop_atr"] * a[i])) / px0
        out.append(dict(i=int(i), e=int(e), x=int(j_exit), t_sig=t[i], t_in=t_in, t_out=t_out, entry=px0,
                        exit=exit_px, why=why, gross=gross, net=net, funding=fsum, risk=risk,
                        R=net / risk, R_gross=gross / risk, bars=j_exit - e + 1))
    return out
