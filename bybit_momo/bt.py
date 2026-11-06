"""Backtest of strategy.py on the downloaded Bybit 5-minute perp candles (data/*.npz).

Per signal (one open position per coin at a time):
  taker model : buy at the next candle's open + 0.10% slippage, 0.055% taker fee
  chase model : limit at signal close + 0.2%, valid 2 candles (no fill if price runs away), 0.02% maker fee
  exits       : strategy.exit_event; stop exits 0.10% slippage, other exits 0.05%, 0.055% taker fee each.
                The entry candle can stop the trade out (worst case). Funding is not included (holds are hours).
R = (exit after costs - entry after costs) / (entry - stop). Placebo: for every real signal, a random bar of the same
coin while it was in the turnover band, same stop distance in %, same exits.
Portfolio: 1,000 USDT, risk 10 USDT per trade (1%), at most 5 open, position <= 250 USDT and <= 10% of the coin's
average 5-minute turnover (liquidity cap).

    .venv\\Scripts\\python bt.py
"""
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

import strategy as S

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results"
FEE_T, FEE_M = 0.00055, 0.0002
SLIP_IN, SLIP_STOP, SLIP_EXIT = 0.0010, 0.0010, 0.0005
CHASE, CHASE_BARS = 0.002, 2
rng = np.random.default_rng(11)


def load(f):
    z = np.load(f)
    return {k: z[k] for k in ("ts", "open", "high", "low", "close", "volume", "turnover")}


def simulate(d, x, i, stop, model):
    """Returns (trade dict or None, index of the last bar used)."""
    n = len(d["close"])
    if i + 1 >= n:
        return None, i
    if model == "taker":
        k = i + 1
        fill = d["open"][k] * (1 + SLIP_IN)
        fee_in = FEE_T
    else:
        lim = d["close"][i] * (1 + CHASE)
        k = None
        for kk in range(i + 1, min(i + 1 + CHASE_BARS, n)):
            if d["low"][kk] <= lim:
                k, fill = kk, min(d["open"][kk], lim)
                break
        if k is None:
            return {"missed": True}, i + CHASE_BARS
        fee_in = FEE_M
    if fill <= stop:
        return {"skipped": "opened below stop"}, k
    cost_in = fill * (1 + fee_in)
    risk = fill - stop
    pos = {"stop": stop, "left": 1.0, "partial": False}
    got, reasons, j = 0.0, [], k
    while j < n and pos["left"] > 1e-9:
        for frac, px, why in S.exit_event(d, x, j, pos):
            slip = SLIP_STOP if why == "stop" else SLIP_EXIT
            got += frac * px * (1 - slip) * (1 - FEE_T)
            reasons.append(why)
            if why == "stop" or why.startswith("close below"):
                pos["left"] = 0.0
        if pos["left"] <= 1e-9:
            break
        j += 1
    if pos["left"] > 1e-9:                      # data ended: close at the last price
        got += pos["left"] * d["close"][-1] * (1 - SLIP_EXIT) * (1 - FEE_T)
        reasons.append("end of data")
        j = n - 1
    return {"fill": fill, "stop": stop, "R": (got - cost_in) / risk, "ret_pct": (got / cost_in - 1) * 100,
            "stop_pct": risk / fill * 100, "bars": j - k + 1, "exit": " + ".join(reasons), "k": k, "j": j}, j


def run():
    OUT.mkdir(exist_ok=True)
    files = sorted(DATA.glob("*.npz"))
    rows, plac, stalks = [], [], 0
    t0 = time.time()
    for n_f, f in enumerate(files, 1):
        d = load(f)
        if len(d["close"]) < 500:
            continue
        x = S.indicators(d)
        log = []
        sigs = S.scan(d, x, stalk_log=log)
        stalks += len(log)
        sym = f.stem
        band = np.where([S.in_universe(x, i) for i in range(len(d["close"]))])[0] if sigs else []
        for model in ("taker", "chase"):
            busy_until = -1
            for s in sigs:
                if s.i <= busy_until:
                    continue
                tr, last = simulate(d, x, s.i, s.stop, model)
                if tr is None:
                    continue
                busy_until = last
                base = {"symbol": sym, "leg": s.leg, "model": model, "ts": int(d["ts"][s.i]),
                        "avg_turnover_5m": float(np.mean(d["turnover"][max(0, s.i - 50):s.i])) if s.i else 0.0}
                if tr.get("missed") or tr.get("skipped"):
                    rows.append({**base, "status": "missed" if tr.get("missed") else "skipped"})
                    continue
                rows.append({**base, "status": "filled", **{k: v for k, v in tr.items() if k not in ("k", "j")},
                             "exit_ts": int(d["ts"][tr["j"]]), **{f"i_{k}": v for k, v in s.info.items()}})
                if model == "taker" and len(band):
                    r = int(rng.choice(band))
                    stop = d["close"][r] * (1 - tr["stop_pct"] / 100)
                    pt, _ = simulate(d, x, r, stop, "taker")
                    if pt and "R" in pt:
                        plac.append({"symbol": sym, "leg": s.leg, "R": pt["R"], "ret_pct": pt["ret_pct"]})
        if n_f % 50 == 0:
            print(f"  {n_f}/{len(files)} coins, {len(rows)} rows ({time.time() - t0:.0f}s)", flush=True)
    return pd.DataFrame(rows), pd.DataFrame(plac), stalks


def stats(g: pd.DataFrame) -> dict:
    if g.empty:
        return {"trades": 0}
    R = g["R"]
    t = R.mean() / (R.std(ddof=1) / np.sqrt(len(R))) if len(R) > 2 else float("nan")
    return {"trades": len(g), "win_pct": round((R > 0).mean() * 100, 1), "avg_R": round(R.mean(), 3),
            "median_R": round(R.median(), 3), "avg_ret_pct": round(g["ret_pct"].mean(), 3),
            "profit_factor": round(R[R > 0].sum() / -R[R < 0].sum(), 2) if (R < 0).any() else None,
            "t": round(float(t), 2), "best_R": round(R.max(), 1), "share_R_ge_3": round((R >= 3).mean() * 100, 1),
            "avg_stop_pct": round(g["stop_pct"].mean(), 2), "avg_hold_min": round(g["bars"].mean() * 5, 0)}


def portfolio(g: pd.DataFrame, cash=1000.0, risk=10.0, max_open=5, max_pos=250.0):
    g = g.sort_values("ts")
    open_until, pnl, taken = [], 0.0, 0
    for _, r in g.iterrows():
        open_until = [e for e in open_until if e > r["ts"]]
        if len(open_until) >= max_open:
            continue
        notional = min(risk / (r["stop_pct"] / 100), max_pos, 0.10 * r["avg_turnover_5m"])
        if notional < 5:
            continue
        open_until.append(r["exit_ts"])
        pnl += notional * r["ret_pct"] / 100
        taken += 1
    return {"trades": taken, "pnl_usdt": round(pnl, 1), "return_pct": round(pnl / cash * 100, 1)}


if __name__ == "__main__":
    df, pl, stalks = run()
    df.to_csv(OUT / "trades.csv", index=False)
    rep = {"coins": len(list(DATA.glob('*.npz'))), "stalk_episodes": stalks}
    print(f"\n{rep['coins']} coins, {stalks} stalk episodes (leg-2 queue entries)\n")
    for model in ("taker", "chase"):
        m = df[df["model"] == model]
        filled = m[m["status"] == "filled"]
        print(f"== entry model: {model}  (signals {len(m)}, filled {len(filled)}, missed {int((m.status == 'missed').sum())})")
        for leg in ("ignition", "flag", None):
            g = filled if leg is None else filled[filled["leg"] == leg]
            s = stats(g)
            s["portfolio"] = portfolio(g) if not g.empty else {}
            if not g.empty:
                half = pd.to_datetime(g["ts"], unit="ms") < pd.to_datetime(g["ts"], unit="ms").min() + \
                    (pd.to_datetime(g["ts"], unit="ms").max() - pd.to_datetime(g["ts"], unit="ms").min()) / 2
                s["first_half_avg_R"] = round(g.loc[half, "R"].mean(), 3)
                s["second_half_avg_R"] = round(g.loc[~half, "R"].mean(), 3)
            rep[f"{model} {leg or 'both'}"] = s
            print(f"   {leg or 'both legs':<9} {s}")
    for leg in ("ignition", "flag", None):
        g = pl if leg is None else pl[pl["leg"] == leg]
        if not g.empty:
            rep[f"placebo {leg or 'both'}"] = {"trades": len(g), "avg_R": round(g.R.mean(), 3), "win_pct": round((g.R > 0).mean() * 100, 1)}
            print(f"   placebo {leg or 'both'}: {rep[f'placebo {leg or chr(98) + chr(111) + chr(116) + chr(104)}']}")
    (OUT / "summary.json").write_text(json.dumps(rep, indent=1, default=str))
