"""Fair Value Gap (FVG) backtest on Binance USDT futures, 1-hour candles, Nov 2021 - Oct 2026 (data_5y, ~515 coins).

Pattern (known when candle 3 closes, at row i):
  bullish FVG: low[i] > high[i-2]   gap = high[i-2] .. low[i]   -> wait for price to come back down, buy
  bearish FVG: high[i] < low[i-2]   gap = high[i] .. low[i-2]   -> wait for price to come back up, short
Trade:
  entry  = limit order at the gap edge price touches first ("edge") or the gap middle ("mid"), valid 24 candles;
           cancelled if price reaches the target first. Fills only if price trades through the level.
  stop   = other side of the gap ("edge") or beyond candle 1 ("c1")
  target = 1R / 2R / 3R, max hold 48 candles then exit at the close
  trend  = any, or only with the 200-hour EMA (long above it, short below it)
  gap    = minimum gap size 0.5% / 1% / 2%
Conservative fills: if the stop and the target are inside the same candle, the stop counts. The candle that fills
the order can stop it out but cannot reach the target. Stops that gap through exit at the worse open.
Costs: entry 0.05%, target exit 0.05% (limit orders), stop / time exit 0.10% (market order + slippage), plus the
real funding payments.
Placebo: the same orders (same distances from the price) placed at random hours of the same coin; tells whether
the FVG location itself adds anything.
Honest choice: the best settings are picked on Nov 2021 - Dec 2024 only and then checked on 2025 - 2026.

    .venv\\Scripts\\python user_data\\fvg_bt.py      (from freqtrade_bot)  -> fvg_results.json, fvg_bt.txt
"""
import itertools
import os
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
TF = os.environ.get("FVG_TF", "1h")  # set FVG_TF=5m for the 5-minute test (data_5m from dl_5m.py)
D5 = HERE / "data_5y"
DC = HERE / ("data_5m" if TF == "5m" else "data_5y")
SUF = "" if TF == "1h" else "_" + TF
OUT = HERE / f"fvg_results{SUF}.json"
WAIT, HOLD = 24, 48
L = WAIT + HOLD + 1
C_IN, C_TP, C_SL = 0.0005, 0.0005, 0.0010
GAPS = (0.003, 0.005, 0.01) if TF == "5m" else (0.005, 0.01, 0.02)
ENTRY = ("edge", "mid")
STOP = ("edge", "c1")
RR = (1, 2, 3)
TREND = ("any", "with")
PICK_END = pd.Timestamp("2026-07-01" if TF == "5m" else "2025-01-01", tz="UTC")
rng = np.random.default_rng(7)


def coins():
    return sorted(p.name.split("_USDT_USDT")[0] for p in DC.glob(f"*_USDT_USDT-{TF}-futures.feather"))


def load(coin):
    df = pd.read_feather(DC / f"{coin}_USDT_USDT-{TF}-futures.feather")
    try:
        fr = pd.read_feather(D5 / f"{coin}_USDT_USDT-1h-funding_rate.feather").set_index("date")["funding_rate"]
        cum = fr.groupby(level=0).sum().reindex(df["date"], fill_value=0.0).cumsum().to_numpy()
    except FileNotFoundError:
        cum = np.zeros(len(df))
    return df, cum


def sim(o, h, lo, c, cum, rows, E, S, T, long):
    """Vectorised trade simulation for orders placed after row `rows`. Returns (filled, ret, fill_row, exit_row)."""
    idx = rows[:, None] + 1 + np.arange(L)
    O, H, Lo, C = o[idx], h[idx], lo[idx], c[idx]
    if not long:  # mirror shorts so one code path handles both: prices -> negative
        O, H, Lo, C = -O, -Lo, -H, -C
        E, S, T = -E, -S, -T
    e, s, t = E[:, None], S[:, None], T[:, None]
    n = len(rows)
    r = np.arange(n)
    fillm = Lo[:, :WAIT] < e
    has = fillm.any(1)
    fi = fillm.argmax(1)
    pre = H[:, :WAIT] >= t
    ti = np.where(pre.any(1), pre.argmax(1), WAIT + 1)
    ok = has & (ti >= fi)  # same candle: count as filled (conservative)
    F = np.minimum(E, O[r, fi])
    cols = np.arange(L)[None, :]
    after = (cols > fi[:, None]) & (cols <= fi[:, None] + HOLD)
    sm = (Lo <= s) & after
    tm = (H >= t) & after
    js = np.where(sm.any(1), sm.argmax(1), L + 5)
    jt = np.where(tm.any(1), tm.argmax(1), L + 5)
    stop_fill = Lo[r, fi] <= S
    js = np.where(stop_fill, fi, js)
    last = np.minimum(fi + HOLD, L - 1)
    kind = np.where(js <= jt, np.where(js < L + 5, 0, 2), 1)  # 0 stop, 1 target, 2 time
    jx = np.select([kind == 0, kind == 1], [js, jt], last)
    X = np.select([kind == 0, kind == 1], [np.minimum(S, np.where(stop_fill, S, O[r, np.minimum(js, L - 1)])), T],
                  C[r, last])
    # mirrored short prices are negative, but X / F is still real exit / real entry
    ret = X / F - 1 if long else 1 - X / F
    cost = C_IN + np.where(kind == 1, C_TP, C_SL)
    fr_, xr_ = rows + 1 + fi, rows + 1 + jx
    paid = cum[xr_] - cum[fr_]
    fund = -paid if long else paid
    return ok, ret - cost + fund, fr_, xr_


def levels(o, h, lo, c, i, long, entry, stop, k):
    if long:
        top, bot = lo[i], h[i - 2]
        E = top if entry == "edge" else (top + bot) / 2
        S = bot * 0.999 if stop == "edge" else lo[i - 2] * 0.999
        T = E + k * (E - S)
    else:
        bot, top = h[i], lo[i - 2]
        E = bot if entry == "edge" else (top + bot) / 2
        S = top * 1.001 if stop == "edge" else h[i - 2] * 1.001
        T = E - k * (S - E)
    return E, S, T


def main(keep=()):
    t0 = time.time()
    agg = {}  # (kind, side, gap, entry, stop, k, trend, period) -> [n, sum, wins]
    kept = {cfg: [] for cfg in keep}
    cl = coins()
    for ci, coin in enumerate(cl):
        df, cum = load(coin)
        n = len(df)
        if n < 500:
            continue
        o, h, lo, c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
        ema = pd.Series(c).ewm(span=200, adjust=False).mean().to_numpy()
        dates = df["date"].dt.tz_convert(None).to_numpy()
        i = np.arange(2, n - L - 1)
        i = i[i >= 200]
        for side, long in (("long", True), ("short", False)):
            gap = (lo[i] / h[i - 2] - 1) if long else (lo[i - 2] / h[i] - 1)
            ev = i[gap >= GAPS[0]]
            g = gap[gap >= GAPS[0]]
            if len(ev) == 0:
                continue
            trend_ok = (c[ev] > ema[ev]) if long else (c[ev] < ema[ev])
            per = np.where(dates[ev] < PICK_END.to_datetime64(), "pick", "check")
            rnd = rng.integers(200, n - L - 1, len(ev))
            for entry, stop, k in itertools.product(ENTRY, STOP, RR):
                E, S, T = levels(o, h, lo, c, ev, long, entry, stop, k)
                good = np.isfinite(E) & (E > 0) & (S > 0) & (T > 0) & (np.abs(E - S) / E > 0.001)
                for kind, rows, e_, s_, t_ in (("fvg", ev, E, S, T),
                                               ("placebo", rnd, c[rnd] * E / c[ev], c[rnd] * S / c[ev],
                                                c[rnd] * T / c[ev])):
                    filled, ret, fr_, xr_ = sim(o, h, lo, c, cum, rows, e_, s_, t_, long)
                    m0 = filled & good
                    for gmin, trend in itertools.product(GAPS, TREND):
                        m = m0 & (g >= gmin) & (trend_ok if trend == "with" else True)
                        for p in ("pick", "check"):
                            mm = m & (per == p)
                            if not mm.any():
                                continue
                            a = agg.setdefault((kind, side, gmin, entry, stop, k, trend, p), [0, 0.0, 0])
                            rr = ret[mm]
                            a[0] += len(rr)
                            a[1] += float(rr.sum())
                            a[2] += int((rr > 0).sum())
                        cfg = (kind, side, gmin, entry, stop, k, trend)
                        if cfg in kept:
                            mm = np.where(m)[0]
                            kept[cfg] += [(coin, dates[fr_[j]], dates[xr_[j]], ret[j]) for j in mm]
        if ci % 50 == 0:
            print(f"  {ci}/{len(cl)} coins ({time.time() - t0:.0f}s)", flush=True)
    return agg, kept


def portfolio(tr, stake=100.0, max_open=5):
    tr = sorted(tr, key=lambda x: x[1])
    open_until, pnl, taken = [], 0.0, []
    for t in tr:
        open_until = [x for x in open_until if x > t[1]]
        if len(open_until) >= max_open:
            continue
        open_until.append(t[2])
        pnl += stake * t[3]
        taken.append(t)
    return taken, pnl


def row(agg, cfg, p):
    a = agg.get(cfg + (p,), [0, 0.0, 0])
    return {"trades": a[0], "avg_pct": round(a[1] / a[0] * 100, 3) if a[0] else None,
            "win_pct": round(a[2] / a[0] * 100, 1) if a[0] else None}


if __name__ == "__main__":
    t0 = time.time()
    agg, _ = main()
    cfgs = sorted({k[1:7] for k in agg if k[0] == "fvg"})
    table = []
    for cfg in cfgs:
        f_p, f_c = row(agg, ("fvg",) + cfg, "pick"), row(agg, ("fvg",) + cfg, "check")
        p_p, p_c = row(agg, ("placebo",) + cfg, "pick"), row(agg, ("placebo",) + cfg, "check")
        table.append({"side": cfg[0], "gap": cfg[1], "entry": cfg[2], "stop": cfg[3], "rr": cfg[4], "trend": cfg[5],
                      "pick": f_p, "check": f_c, "placebo_pick": p_p, "placebo_check": p_c})
    textbook = [("long", 0.005, "mid", "c1", 2, "with"), ("short", 0.005, "mid", "c1", 2, "with")]
    best = {}
    for side in ("long", "short"):
        cand = [t for t in table if t["side"] == side and t["pick"]["trades"] >= 500]
        best[side] = max(cand, key=lambda t: t["pick"]["avg_pct"])
    chosen = [tuple(best[s][x] for x in ("side", "gap", "entry", "stop", "rr", "trend")) for s in ("long", "short")]
    print("second pass for the portfolio ...", flush=True)
    keep = [("fvg",) + c for c in chosen + textbook] + [("placebo",) + c for c in chosen]
    _, kept = main(keep=tuple(keep))
    ports = {}
    for cfg, tr in kept.items():
        out = {}
        for name, lo_, hi_ in (("pick", None, PICK_END), ("check", PICK_END, None), ("all", None, None)):
            sub = [t for t in tr if (lo_ is None or t[1] >= lo_.to_datetime64()) and
                   (hi_ is None or t[1] < hi_.to_datetime64())]
            taken, pnl = portfolio(sub)
            yrs = {}
            for t in taken:
                y = pd.Timestamp(t[1]).year
                yrs[y] = yrs.get(y, 0.0) + 100 * t[3]
            out[name] = {"trades": len(taken), "pnl_usdt": round(pnl, 1),
                         "win_pct": round(float(np.mean([t[3] > 0 for t in taken]) * 100), 1) if taken else None,
                         "avg_pct": round(float(np.mean([t[3] for t in taken]) * 100), 3) if taken else None,
                         "by_year": {int(k): round(v, 1) for k, v in sorted(yrs.items())}}
        ports[" ".join(map(str, cfg))] = out
    rep = {"configs": table, "best_on_pick": {s: best[s] for s in best}, "textbook": [list(t) for t in textbook],
           "portfolio": ports, "costs": {"entry": C_IN, "tp_exit": C_TP, "sl_time_exit": C_SL}}
    OUT.write_text(json.dumps(rep, indent=1, default=str))
    lines = []
    for side in ("long", "short"):
        lines.append(f"\n{side.upper()}  top 8 settings by pick-period average (then check period, placebo)")
        cand = sorted([t for t in table if t["side"] == side and t["pick"]["trades"] >= 500],
                      key=lambda t: -t["pick"]["avg_pct"])[:8]
        for t in cand + [t for t in table if t["side"] == side and (t["side"], t["gap"], t["entry"], t["stop"], t["rr"], t["trend"])
                         in textbook]:
            lines.append(f"  gap>={t['gap']*100:.1f}% {t['entry']:>4} stop={t['stop']:<4} {t['rr']}R {t['trend']:<4} | "
                         f"pick n={t['pick']['trades']:>6} won {t['pick']['win_pct']}% avg {t['pick']['avg_pct']:+.3f}% | "
                         f"check n={t['check']['trades']:>6} won {t['check']['win_pct']}% avg {t['check']['avg_pct']:+.3f}% | "
                         f"placebo check avg {t['placebo_check']['avg_pct']}")
    lines.append("\nPORTFOLIO (100 USDT a trade, max 5 open)")
    for k, v in ports.items():
        lines.append(f"  {k:<40} " + "  ".join(f"{p}: n={v[p]['trades']} won {v[p]['win_pct']}% "
                                              f"avg {v[p]['avg_pct']}% P&L {v[p]['pnl_usdt']:+}" for p in v)
                     + f"  years {v['all']['by_year']}")
    allf = [t for t in table]
    for kind in ("pick", "check"):
        av = [t[kind]["avg_pct"] for t in allf if t[kind]["trades"] >= 500]
        lines.append(f"{kind}: {sum(a > 0 for a in av)}/{len(av)} settings positive")
    txt = "\n".join(lines)
    print(txt)
    (HERE / f"fvg_bt{SUF}.txt").write_text(txt)
    print(f"done in {time.time() - t0:.0f}s")
