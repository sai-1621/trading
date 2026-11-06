"""5-minute pump/dump detection test, as pre-registered in PREREG_5m_entry.md.

    ..\\.venv\\Scripts\\python entry_5m.py      -> entry_5m_results.json
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ml_5y as Y  # noqa: E402
import ml_pumpdump as M  # noqa: E402

HERE = Path(__file__).resolve().parent
D5M = HERE / "data_5m"
OUT = HERE / "entry_5m_results.json"
BAR = pd.Timedelta(minutes=5)
WIN, BASE, DAY = 12, 576, 288          # 60 min, 48 h, 24 h in 5-minute bars
MOVE, VOLX, MINQV = 0.05, 3.0, 2_000_000
DELAY = 0.0015
SPLIT = pd.Timestamp("2026-07-01", tz="UTC")
RULES = {"R1 pump -> short": ("pump", "short"), "R2 pump -> long": ("pump", "long"),
         "R3 dump -> long": ("dump", "long"), "R4 dump -> short": ("dump", "short")}


def load(coin):
    d = pd.read_feather(D5M / f"{coin}_USDT_USDT-5m-futures.feather").drop_duplicates("date").set_index("date")
    grid = pd.date_range(d.index.min(), d.index.max(), freq="5min")
    return d.reindex(grid)                 # gaps stay NaN, so no event is detected across them


def detect(d, hourly):
    o, c, v = d["open"].to_numpy(float), d["close"].to_numpy(float), d["volume"].to_numpy(float)
    qv = pd.Series(v * c)
    qv60 = qv.rolling(WIN, min_periods=WIN).sum()
    base = qv.rolling(BASE, min_periods=BASE).mean().shift(WIN) * WIN
    qv24 = qv.rolling(DAY, min_periods=DAY).sum()
    move = pd.Series(c) / pd.Series(o).shift(WIN - 1) - 1
    ok = (qv60 / base >= VOLX) & (qv24 >= MINQV)
    pump = (ok & (move >= MOVE)).to_numpy()
    dump = (ok & (move <= -MOVE)).to_numpy()
    cond = pump | dump
    step = WIN if hourly else 1
    prev = np.r_[np.zeros(step, bool), cond[:-step]]
    fresh = cond & ~prev
    if hourly:   # the candle that closes at a whole hour opens at minute 55
        fresh &= (d.index.minute == 55)
    out, last = [], -10 ** 9
    for i in np.flatnonzero(fresh):
        if i - last >= DAY:
            out.append((i, "pump" if pump[i] else "dump"))
            last = i
    return out


def sim(h, lo, c, entry, i, side):
    """Exit bar index and price return (before costs) for a trade entered at the open of bar i+1."""
    s = slice(i + 1, i + 1 + DAY)
    hh, ll = h[s], lo[s]
    if side == "long":
        sl_hit, tp_hit = ll <= entry * (1 - M.LONG_SL), hh >= entry * (1 + M.LONG_TP)
        sl_r, tp_r = -M.LONG_SL, M.LONG_TP
    else:
        sl_hit, tp_hit = hh >= entry * (1 + M.SHORT_SL), ll <= entry * (1 - M.SHORT_TP)
        sl_r, tp_r = -M.SHORT_SL, M.SHORT_TP
    a = np.argmax(sl_hit) if sl_hit.any() else DAY
    b = np.argmax(tp_hit) if tp_hit.any() else DAY
    if a == DAY and b == DAY:
        last = i + DAY
        r = c[last] / entry - 1
        return last, (r if side == "long" else -r)
    if a <= b:                          # stop first, also when both fall in the same candle
        return i + 1 + a, sl_r
    return i + 1 + b, tp_r


def funding_sum(fr_t, fr_cum, t0, t1):
    if fr_t is None:
        return 0.0
    k0 = np.searchsorted(fr_t, t0, side="right")
    k1 = np.searchsorted(fr_t, t1, side="right")
    return float(fr_cum[k1] - fr_cum[k0])


def events_for(coin):
    d = load(coin)
    try:
        fr = pd.read_feather(Y.path(coin, "funding_rate")).groupby("date")["funding_rate"].sum().sort_index()
        fr_t = fr.index.tz_convert("UTC").tz_localize(None).to_numpy("datetime64[ns]")
        fr_cum = np.r_[0.0, np.cumsum(fr.to_numpy())]
    except FileNotFoundError:
        fr_t = fr_cum = None
    o, h, lo, c = (d[x].to_numpy(float) for x in ("open", "high", "low", "close"))
    times = d.index.tz_convert("UTC").tz_localize(None).to_numpy("datetime64[ns]")
    rows = []
    for hourly in (False, True):
        for i, kind in detect(d, hourly):
            if i + 1 + DAY >= len(c) or np.isnan(o[i + 1]):
                continue
            entry = o[i + 1]
            base = {"coin": coin, "detect": "hourly" if hourly else "5m", "kind": kind,
                    "entry_time": pd.Timestamp(times[i + 1], tz="UTC")}
            for side in ("long", "short"):
                j, r = sim(h, lo, c, entry, i, side)
                if np.isnan(r):
                    continue
                t_out = times[j] + np.timedelta64(5, "m")
                f = funding_sum(fr_t, fr_cum, times[i + 1], t_out)
                fund = -f if side == "long" else f
                rows.append({**base, "side": side, "exit_time": pd.Timestamp(t_out, tz="UTC"),
                             "ret_nodelay": r - M.FEE + fund, "ret": r - M.FEE - DELAY + fund, "funding": fund})
    return rows


def tstat(x):
    x = np.asarray(x, float)
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) if len(x) > 2 and x.std(ddof=1) > 0 else 0.0


def evaluate(df, col="ret"):
    if df.empty:
        return {"trades": 0, "portfolio_trades": 0, "avg_pct": None, "t": 0.0, "win_pct": None, "pnl_usdt": 0.0}
    x = df[col]
    taken, pnl = M.portfolio(df.assign(ret=x).assign(date=df["entry_time"]))
    return {"trades": int(len(df)), "portfolio_trades": int(len(taken)), "avg_pct": round(float(x.mean() * 100), 3),
            "t": round(tstat(x), 2), "win_pct": round(float((x > 0).mean() * 100), 1), "pnl_usdt": round(pnl, 1),
            "avg_funding_pct": round(float(df["funding"].mean() * 100), 3)}


def main():
    t0 = time.time()
    coins = sorted(p.name.split("_USDT_USDT")[0] for p in D5M.glob("*_USDT_USDT-5m-futures.feather"))
    rows = []
    for k, coin in enumerate(coins):
        rows += events_for(coin)
        if k % 20 == 0:
            print(f"  {k}/{len(coins)} coins, {len(rows)} trade rows ({time.time() - t0:.0f}s)", flush=True)
    ev = pd.DataFrame(rows)
    rep = {"prereg": "PREREG_5m_entry.md", "coins": len(coins), "split": str(SPLIT.date()), "rules": {}}
    print(f"\n{'':22} {'detect':7} {'period':8} {'trades':>6} {'pf':>4} {'avg %':>7} {'t':>6} {'won%':>6} {'P&L':>8}  fund%")
    for name, (kind, side) in RULES.items():
        rep["rules"][name] = {}
        for det in ("5m", "hourly"):
            sel = ev[(ev["detect"] == det) & (ev["kind"] == kind) & (ev["side"] == side)]
            per = {"pick": sel[sel["entry_time"] < SPLIT], "holdout": sel[sel["entry_time"] >= SPLIT]}
            res = {p: evaluate(x) for p, x in per.items()}
            res["pick_no_delay"] = evaluate(per["pick"], "ret_nodelay")
            rep["rules"][name][det] = res
            for p in ("pick",) if det == "5m" else ("pick", "holdout"):
                s = res[p]
                print(f"{name:22} {det:7} {p:8} {s['trades']:>6} {s['portfolio_trades']:>4} "
                      f"{(s['avg_pct'] if s['avg_pct'] is not None else 0):>+7.3f} {s['t']:>+6.2f} "
                      f"{(s['win_pct'] or 0):>6} {s['pnl_usdt']:>+8.1f}  {s.get('avg_funding_pct')}")

    # decision on the 5-minute rules only; the holdout is opened only for the chosen rule
    elig = {n: r["5m"]["pick"] for n, r in rep["rules"].items()
            if r["5m"]["pick"]["t"] >= 2.0 and r["5m"]["pick"]["pnl_usdt"] > 0}
    if not elig:
        rep["decision"] = {"eligible": [], "PASS": False, "why": "no 5-minute rule had t >= 2.0 and positive P&L"}
    else:
        best = max(elig, key=lambda n: elig[n]["t"])
        ho = rep["rules"][best]["5m"]["holdout"]
        ok = (ho["avg_pct"] or 0) > 0 and ho["pnl_usdt"] > 0
        rep["decision"] = {"eligible": list(elig), "chosen": best, "holdout": ho, "PASS": bool(ok)}
    print("\ndecision:", rep["decision"])
    OUT.write_text(json.dumps(rep, indent=1, default=str))
    print(f"saved {OUT.name}  done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
