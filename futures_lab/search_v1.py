"""Futures strategy search v1, exactly as pre-registered in futures_lab/PREREG.md.

    ..\\freqtrade_bot\\.venv\\Scripts\\python search_v1.py

Writes results_v1.json. Holdout numbers are computed only for the chosen variant.
"""
import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "freqtrade_bot" / "user_data" / "data_5y"
COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT", "LTC", "BCH", "TRX", "ATOM",
         "ETC", "NEAR", "FIL", "UNI", "AAVE"]
PICK = ("2022-01-01", "2024-12-31")
HOLD = ("2025-01-01", "2026-10-29")
COST = 0.001          # per side, of traded notional
TARGET_VOL, MAX_GROSS, MAX_COIN, VOL_WIN = 0.40, 3.0, 0.75, 30


def load():
    closes, highs, lows, execp, fund = {}, {}, {}, {}, {}
    for c in COINS:
        h = pd.read_feather(DATA / f"{c}_USDT_USDT-1h-futures.feather").set_index("date").sort_index()
        d = h.resample("1D").agg({"high": "max", "low": "min", "close": "last"})
        closes[c], highs[c], lows[c] = d["close"], d["high"], d["low"]
        # fill price for day E: close of the 00:00 candle (= 01:00 UTC)
        ex = h["close"][h.index.hour == 0]
        execp[c] = ex.set_axis(ex.index.normalize())
        f = pd.read_feather(DATA / f"{c}_USDT_USDT-1h-funding_rate.feather").set_index("date")["funding_rate"]
        # funding paid in the holding window (E 01:00, E+1 01:00] -> attributed to day E
        fund[c] = f.groupby((f.index - pd.Timedelta(hours=1, seconds=1)).normalize()).sum()
    C = pd.DataFrame(closes).dropna()
    idx = C.index
    H, L = pd.DataFrame(highs).reindex(idx), pd.DataFrame(lows).reindex(idx)
    X = pd.DataFrame(execp).reindex(idx).ffill()
    F = pd.DataFrame(fund).reindex(idx).fillna(0.0)
    return C, H, L, X, F


def donchian(C, H, L, n, m, long_only):
    hi_n, lo_n = H.shift(1).rolling(n).max(), L.shift(1).rolling(n).min()
    hi_m, lo_m = H.shift(1).rolling(m).max(), L.shift(1).rolling(m).min()
    pos = pd.DataFrame(0.0, index=C.index, columns=C.columns)
    cur = pd.Series(0.0, index=C.columns)
    for i, d in enumerate(C.index):
        c = C.loc[d]
        if i < n:
            pos.loc[d] = 0.0
            continue
        enter_l, enter_s = c > hi_n.loc[d], c < lo_n.loc[d]
        exit_l, exit_s = c < lo_m.loc[d], c > hi_m.loc[d]
        cur = cur.where(~((cur > 0) & exit_l), 0.0)
        cur = cur.where(~((cur < 0) & exit_s), 0.0)
        cur = cur.where(~enter_l, 1.0)
        if not long_only:
            cur = cur.where(~enter_s, -1.0)
        pos.loc[d] = cur
    return pos


def ema_sig(C, f, s, long_only):
    sig = np.sign(C.ewm(span=f, adjust=False).mean() - C.ewm(span=s, adjust=False).mean())
    sig.iloc[:s] = 0.0
    return sig.clip(lower=0) if long_only else sig


def xsec(C, look, k):
    ret = C / C.shift(look) - 1
    pos = pd.DataFrame(np.nan, index=C.index, columns=C.columns)
    for i, d in enumerate(C.index):
        if i < max(look, VOL_WIN) or (i % 7):
            continue
        r = ret.loc[d].dropna().sort_values()
        row = pd.Series(0.0, index=C.columns)
        row[r.index[-k:]] = 1.0
        row[r.index[:k]] = -1.0
        pos.loc[d] = row
    return pos   # NaN rows = keep the previous weights


def size(raw_sign, C, rebalance_mask=None):
    """sign -> inverse-vol raw weights -> book vol target 40% -> caps. Returns weights decided on each day D."""
    R = C.pct_change()
    vol = R.rolling(VOL_WIN).std()
    W = pd.DataFrame(0.0, index=C.index, columns=C.columns)
    last = pd.Series(0.0, index=C.columns)
    for i, d in enumerate(C.index):
        s = raw_sign.loc[d]
        if s.isna().all():                       # cross-sectional: hold between rebalances
            W.loc[d] = last
            continue
        if i < VOL_WIN + 1:
            continue
        raw = (s.fillna(0.0) / vol.loc[d]).replace([np.inf, -np.inf], 0.0).fillna(0.0)
        if raw.abs().sum() == 0:
            last = raw * 0.0
            W.loc[d] = last
            continue
        if (raw > 0).any() and (raw < 0).any() and rebalance_mask is not None:   # neutral book: each side 1
            raw[raw > 0] /= raw[raw > 0].sum()
            raw[raw < 0] /= -raw[raw < 0].sum()
        hist = R.iloc[i - VOL_WIN + 1:i + 1].fillna(0.0) @ raw
        sig = hist.std() * math.sqrt(365)
        w = raw * (TARGET_VOL / sig) if sig > 0 else raw * 0.0
        w = w.clip(-MAX_COIN, MAX_COIN)
        g = w.abs().sum()
        if g > MAX_GROSS:
            w *= MAX_GROSS / g
        last = w
        W.loc[d] = w
    return W


def backtest(W, X, F):
    """Weights decided at day D's close are filled at D+1 01:00 and held to D+2 01:00."""
    held = W.shift(1).fillna(0.0)                     # position over the window starting day E
    r = X.shift(-1) / X - 1                           # E 01:00 -> E+1 01:00
    gross = (held * r).sum(axis=1)
    funding = (held * F).sum(axis=1)
    turnover = (held - held.shift(1).fillna(0.0)).abs().sum(axis=1)
    net = gross - funding - turnover * COST
    out = pd.DataFrame({"net": net, "gross": gross, "funding": funding, "cost": turnover * COST,
                        "lev": held.abs().sum(axis=1)})
    out.index = out.index + pd.Timedelta(days=1)      # the return is realised at the end of the window
    return out.iloc[:-1], held


def trade_wins(held, X, period):
    """Per coin, each run of the same position sign is one trade (trend) / holding period."""
    r = X.shift(-1) / X - 1
    contrib = held * r
    wins = n = 0
    for c in held.columns:
        s = np.sign(held[c])
        seg = (s != s.shift()).cumsum()
        for _, g in contrib[c][(s != 0)].groupby(seg[s != 0]):
            d = g.index[0] + pd.Timedelta(days=1)
            if period[0] <= str(d.date()) <= period[1]:
                n += 1
                wins += g.sum() > 0
    return n, wins


def metrics(bt, period):
    x = bt.loc[period[0]:period[1]]
    r = x["net"]
    eq = (1 + r).cumprod()
    years = len(r) / 365
    sd = r.std()
    return {"days": len(r), "total_return_pct": round((eq.iloc[-1] - 1) * 100, 1),
            "cagr_pct": round((eq.iloc[-1] ** (1 / years) - 1) * 100, 1),
            "sharpe": round(r.mean() / sd * math.sqrt(365), 2) if sd > 0 else None,
            "t": round(r.mean() / sd * math.sqrt(len(r)), 2) if sd > 0 else None,
            "max_dd_pct": round(((eq / eq.cummax()) - 1).min() * 100, 1), "worst_day_pct": round(r.min() * 100, 1),
            "avg_leverage": round(x["lev"].mean(), 2), "funding_pct": round(-x["funding"].sum() * 100, 1),
            "costs_pct": round(-x["cost"].sum() * 100, 1),
            "positive_months_pct": round(((1 + r).resample("ME").prod() > 1).mean() * 100, 0)}


def buy_hold(X, period):
    p = X["BTC"].loc[period[0]:period[1]]
    return round((p.iloc[-1] / p.iloc[0] - 1) * 100, 1)


def main():
    C, H, L, X, F = load()
    print(f"{len(C.columns)} coins, {C.index[0].date()} to {C.index[-1].date()}")
    variants = []
    for sig in ("don20", "don55", "ema10_50", "ema20_100"):
        for side in ("long_short", "long_only"):
            variants.append({"family": "T", "signal": sig, "side": side})
    for look in (7, 28):
        for k in (3, 5):
            variants.append({"family": "X", "look": look, "k": k})
    res = []
    for v in variants:
        lo = v.get("side") == "long_only"
        if v["family"] == "T":
            n = {"don20": (20, 10), "don55": (55, 20)}.get(v["signal"])
            raw = donchian(C, H, L, *n, lo) if n else ema_sig(C, *{"ema10_50": (10, 50), "ema20_100": (20, 100)}[v["signal"]], lo)
            W = size(raw, C)
        else:
            W = size(xsec(C, v["look"], v["k"]), C, rebalance_mask=True)
        bt, held = backtest(W, X, F)
        m = metrics(bt, PICK)
        n_tr, w_tr = trade_wins(held, X, PICK)
        res.append({"v": v, "pick": {**m, "trades": n_tr, "win_pct": round(w_tr / n_tr * 100, 1) if n_tr else None},
                    "_bt": bt, "_held": held})
        print(f"{json.dumps(v):<60} pick: ret {m['total_return_pct']:>7}% sharpe {m['sharpe']} dd {m['max_dd_pct']}%"
              f" lev {m['avg_leverage']} win {res[-1]['pick']['win_pct']}% trades {n_tr}")
    elig = [r for r in res if (r["pick"]["sharpe"] or 0) >= 1.0 and r["pick"]["max_dd_pct"] >= -40]
    elig.sort(key=lambda r: r["pick"]["sharpe"], reverse=True)
    rep = {"run_at": datetime.now().isoformat(), "pick": PICK, "holdout": HOLD, "btc_buy_hold_pick_pct": buy_hold(X, PICK),
           "variants": [{"v": r["v"], "pick": r["pick"]} for r in res], "eligible": len(elig)}
    if elig:
        ch = elig[0]
        h = metrics(ch["_bt"], HOLD)
        n_tr, w_tr = trade_wins(ch["_held"], X, HOLD)
        h.update({"trades": n_tr, "win_pct": round(w_tr / n_tr * 100, 1) if n_tr else None})
        passed = h["total_return_pct"] > 0 and (h["sharpe"] or 0) >= 0.5 and h["max_dd_pct"] >= -40 and h["worst_day_pct"] > -30
        yearly = {str(y): round(((1 + g).prod() - 1) * 100, 1) for y, g in ch["_bt"]["net"].groupby(ch["_bt"].index.year)}
        rep.update({"chosen": ch["v"], "chosen_pick": ch["pick"], "holdout": h, "btc_buy_hold_holdout_pct": buy_hold(X, HOLD),
                    "yearly_pct": yearly, "passed": passed})
    else:
        rep.update({"passed": False, "note": "nothing met the choice rule; holdout not looked at"})
    (HERE / "results_v1.json").write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps({k: v for k, v in rep.items() if k != "variants"}, indent=1, default=str))


if __name__ == "__main__":
    main()
