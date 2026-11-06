"""Slot portfolio (mark-to-market on 4h closes) and the performance metrics."""
import numpy as np
import pandas as pd

import config as C


def take_slots(cand: pd.DataFrame, slots=C.SLOTS) -> pd.DataFrame:
    """Walk candidates in entry order (higher p first on ties); take one if a slot is free and the coin is not held."""
    cand = cand.sort_values(["t_in", "p"], ascending=[True, False])
    open_ = []                       # (t_out, coin)
    taken = []
    for r in cand.itertuples():
        open_ = [(to, co) for to, co in open_ if to > r.t_in]
        if len(open_) >= slots or any(co == r.coin for _, co in open_):
            continue
        open_.append((r.t_out, r.coin))
        taken.append(r.Index)
    return cand.loc[taken]


def equity_curve(tr: pd.DataFrame, frames: dict, ti: pd.DatetimeIndex) -> pd.Series:
    """Mark-to-market PnL (USDT) on every 4h close, each trade CAPITAL/SLOTS notional."""
    notional = C.CAPITAL / C.SLOTS
    unreal = np.zeros(len(ti))
    delta = np.zeros(len(ti) + 1)
    pos = pd.Series(np.arange(len(ti)), index=ti)
    for r in tr.itertuples():
        d = frames[r.coin]
        closes = d["close"].values[r.e:r.x]                     # closes while still open (exit bar excluded)
        if len(closes):
            k = pos.reindex(d.index[r.e:r.x]).values.astype(int)
            ent_eff = r.entry * (1 + C.SLIP)
            unreal[k] += notional * (closes / ent_eff - 1 - C.FEE)
        kx = int(pos[d.index[r.x]])
        delta[kx] += notional * r.net
    return pd.Series(unreal + np.cumsum(delta)[:-1], index=ti)


def metrics(tr: pd.DataFrame, eq: pd.Series | None = None, start=None, end=None) -> dict:
    if tr.empty:
        return {"trades": 0}
    r = tr["net"].values
    w, lo = r[r > 0], r[r <= 0]
    pf = w.sum() / -lo.sum() if lo.sum() < 0 else np.inf
    payoff = (len(w) / len(r) * w.mean()) / (len(lo) / len(r) * -lo.mean()) if len(lo) and len(w) else np.nan
    out = {"trades": len(r), "win_pct": round(len(w) / len(r) * 100, 1),
           "avg_net_pct": round(r.mean() * 100, 3), "avg_gross_pct": round(tr["gross"].mean() * 100, 3),
           "avg_R": round(tr["R"].mean(), 3), "avg_win_pct": round(w.mean() * 100, 2) if len(w) else 0,
           "avg_loss_pct": round(lo.mean() * 100, 2) if len(lo) else 0, "pf": round(pf, 3),
           "payoff_ratio": round(payoff, 3),
           "t_trade": round(r.mean() / r.std(ddof=1) * np.sqrt(len(r)), 2) if len(r) > 2 else np.nan}
    if eq is not None:
        e = eq[(eq.index >= start) & (eq.index < end)]
        e = e - e.iloc[0]
        dd = (e + C.CAPITAL) / (e + C.CAPITAL).cummax() - 1
        wk = e.resample("W").last().diff().dropna() / C.CAPITAL
        out.update({"net_return_pct": round(e.iloc[-1] / C.CAPITAL * 100, 1),
                    "max_dd_pct": round(dd.min() * 100, 1),
                    "t_weekly": round(wk.mean() / wk.std(ddof=1) * np.sqrt(len(wk)), 2) if wk.std() > 0 else np.nan})
    return out


def benchmark(frames, start, end) -> dict:
    b = frames["BTC"]["close"]
    b = b[(b.index >= start) & (b.index < end)]
    dd = b / b.cummax() - 1
    return {"btc_bh_return_pct": round((b.iloc[-1] / b.iloc[0] - 1) * 100, 1), "btc_max_dd_pct": round(dd.min() * 100, 1)}


def passes(m: dict) -> dict:
    p = C.PASS
    return {"pf": m.get("pf", 0) >= p["pf"], "t_trade": m.get("t_trade", 0) >= p["t_trade"],
            "t_weekly": (m.get("t_weekly") or 0) >= p["t_weekly"], "payoff_ratio": m.get("payoff_ratio", 0) >= p["payoff_ratio"]}
