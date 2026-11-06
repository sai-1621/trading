"""Run one iteration of the loop.

    .venv\\Scripts\\python run.py --iter 1              research walk-forward OOS (2023-01 .. 2025-12)
    .venv\\Scripts\\python run.py --iter N --holdout    the 2026 holdout: ONCE, only for the final frozen iteration

Writes results/iter<N>[_holdout].json and results/iter<N>[_holdout]_trades.csv."""
import argparse
import json
import time

import numpy as np
import pandas as pd

import config as C
import data
import features as F
import model
import portfolio as P
import primary


def build_trades(cfg):
    bars, fund = data.daily_panel() if cfg.get("tf") == "1d" else data.load_panel()
    frames = {k: F.coin_frame(b, fund[k], cfg) for k, b in bars.items()}
    ti = data.time_index(bars)
    if cfg.get("top_n"):                       # point-in-time liquidity rank among all coins on that bar
        dv = pd.DataFrame({k: f["dvol30"].where(f["liquid"]) for k, f in frames.items()}).reindex(ti)
        rk = dv.rank(axis=1, ascending=False)
        for k, f in frames.items():
            f["liquid"] = f["liquid"] & (rk[k].reindex(f.index) <= cfg["top_n"]).values
    if cfg.get("regime") == "btc_ema200":      # fixed gate: BTC close above its 200-bar EMA on the signal bar
        b = frames["BTC"]
        g = (b["close"] > b["ema200"]).reindex(ti).fillna(False)
        for k, f in frames.items():
            f["gate"] = g.reindex(f.index).fillna(False).values
    mkt, ranks = F.market_frame(frames, ti)
    rows = []
    sig_count = pd.Series(0.0, index=ti)
    per_coin = {}
    for k, d in frames.items():
        idx = primary.signals(d, cfg)
        per_coin[k] = idx
        sig_count.loc[d.index[idx]] += 1
    mkt["m_nsig"] = (sig_count / mkt["n_liquid"].replace(0, np.nan)).astype(float)
    for k, d in frames.items():
        tr = primary.simulate(d, fund[k], per_coin[k], cfg)
        if not tr:
            continue
        t = pd.DataFrame(tr)
        t["coin"] = k
        fcols = [c for c in d.columns if c.startswith("f_")]
        t = t.join(d[fcols].reset_index(drop=True), on="i")
        t = t.join(mkt, on="t_sig")
        for name, w in ranks.items():
            t[name] = w[k].reindex(pd.DatetimeIndex(t["t_sig"])).values
        rows.append(t)
    trades = pd.concat(rows, ignore_index=True)
    trades["f_rs42"] = trades["f_r42"] - trades["m_btc_r42"]
    if cfg["label"] == "net_positive":
        trades["y"] = (trades["net"] > 0).astype(int)
    elif cfg["label"] == "beats_R":
        trades["y"] = (trades["R"] > cfg.get("label_R", 0.5)).astype(int)
    else:
        raise ValueError(cfg["label"])
    return trades, frames, ti


def placebo(test: pd.DataFrame, frames, ti, start, end, n=200, seed=1):
    """Random filter keeping the same number of trades per fold, then the same slot portfolio."""
    rng = np.random.default_rng(seed)
    res = []
    for _ in range(n):
        t = test.copy()
        t["keep"] = False
        for _f, g in t.groupby("fold"):
            k = int(g["keep_meta"].sum())
            if k:
                t.loc[rng.choice(g.index, k, replace=False), "keep"] = True
        t["p"] = rng.random(len(t))
        pt = P.take_slots(t[t["keep"]])
        m = P.metrics(pt)
        res.append((m.get("pf", np.nan), m.get("avg_net_pct", np.nan)))
    a = np.array(res)
    return {"pf_mean": round(np.nanmean(a[:, 0]), 3), "pf_95pct": round(np.nanpercentile(a[:, 0], 95), 3),
            "avg_net_mean": round(np.nanmean(a[:, 1]), 3), "avg_net_95pct": round(np.nanpercentile(a[:, 1], 95), 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iter", type=int, required=True)
    ap.add_argument("--holdout", action="store_true")
    ap.add_argument("--placebo", type=int, default=200)
    a = ap.parse_args()
    cfg = C.ITERATIONS[a.iter]
    model.LGB_OVERRIDE.update(cfg.get("lgb", {}))
    t0 = time.time()
    trades, frames, ti = build_trades(cfg)
    feats = {"base": F.FEATURES_BASE, "v2": F.FEATURES_V2, "v3": F.FEATURES_V3}[cfg["features"]]
    start, end = (C.HOLDOUT_START, ti[-1] + pd.Timedelta(cfg.get("tf", "4h"))) if a.holdout else (C.OOS_START, C.HOLDOUT_START)
    print(f"iteration {a.iter} ({cfg['name']}): {len(trades)} primary trades in total, {time.time() - t0:.0f}s", flush=True)

    test, folds, imp = model.walk_forward(trades, feats, start, end, th_mode=cfg.get("threshold", "max_total"))
    test["keep_meta"] = test["keep"]
    meta = test[test["keep"]]
    prim_port = P.take_slots(test.assign(p=0.0))
    meta_port = P.take_slots(meta)
    eq_meta = P.equity_curve(meta_port, frames, ti)
    eq_prim = P.equity_curve(prim_port, frames, ti)
    rep = {
        "iteration": a.iter, "config": cfg, "period": [str(start.date()), str(end.date())],
        "costs": {"fee_per_side": C.FEE, "slippage_per_side": C.SLIP, "funding": "real Binance rates"},
        "primary_all_signals": P.metrics(test),
        "primary_portfolio": P.metrics(prim_port, eq_prim, start, end),
        "meta_all_kept": P.metrics(meta),
        "meta_portfolio": P.metrics(meta_port, eq_meta, start, end),
        "benchmark": P.benchmark(frames, start, end),
        "placebo_same_count": placebo(test, frames, ti, start, end, n=a.placebo) if a.placebo else None,
        "folds": folds.to_dict("records"),
        "importance_top12": {k: int(v) for k, v in imp.head(12).items()},
        "by_year_meta_portfolio": {str(y): P.metrics(g) for y, g in meta_port.groupby(meta_port["t_in"].dt.year)},
        "exit_mix_meta": meta_port["why"].value_counts().to_dict(),
    }
    rep["pass"] = P.passes(rep["meta_portfolio"])
    rep["passed_all"] = all(rep["pass"].values())
    C.RESULTS.mkdir(exist_ok=True)
    tag = f"iter{a.iter}" + ("_holdout" if a.holdout else "")
    (C.RESULTS / f"{tag}.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    test.drop(columns=[c for c in test.columns if c.startswith(("f_", "m_"))]).to_csv(C.RESULTS / f"{tag}_trades.csv", index=False)
    print(json.dumps({k: rep[k] for k in ("primary_all_signals", "primary_portfolio", "meta_all_kept", "meta_portfolio",
                                          "benchmark", "placebo_same_count", "pass", "passed_all", "exit_mix_meta",
                                          "importance_top12", "by_year_meta_portfolio")}, indent=1, default=str))
    print(pd.DataFrame(rep["folds"]).to_string(index=False))
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
