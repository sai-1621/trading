"""Funding filter test for PumpML, as pre-registered in PREREG_funding_filter.md.

Reuses ml_5y.py unchanged (events, walk-forward, realistic costs) and adds, for every event, the last funding
rate settled at or before the entry time, per hour. Then compares the baseline with rule F1 (skip shorts when
that rate is below -0.075% per hour).

    ..\\.venv\\Scripts\\python funding_filter.py      -> funding_filter_results.json
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
from ml_features import FEATURES  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "funding_filter_results.json"
F1_LIMIT = -0.00075          # per hour, fixed in the pre-registration
INFO_LIMITS = [-0.000375, -0.0015]


def _naive_utc(s):
    s = pd.to_datetime(s, utc=True)
    return s.dt.tz_localize(None).to_numpy(dtype="datetime64[ns]")


def entry_funding(ev):
    """Last settled funding rate at or before entry_time, divided by the hours since the settlement before it."""
    out = np.full(len(ev), np.nan)
    for pair, idx in ev.groupby("pair").groups.items():
        try:
            fr = pd.read_feather(Y.path(pair.split("/")[0], "funding_rate"))
        except FileNotFoundError:
            continue
        fr = fr.groupby("date", as_index=False)["funding_rate"].sum().sort_values("date")
        if len(fr) < 2:
            continue
        t = _naive_utc(fr["date"])
        gap_h = np.r_[8.0, np.diff(t).astype("timedelta64[m]").astype(float) / 60.0]
        per_h = fr["funding_rate"].to_numpy() / np.clip(gap_h, 1.0, 8.0)
        e = _naive_utc(ev.loc[idx, "entry_time"])
        k = np.searchsorted(t, e, side="right") - 1
        ok = k >= 0
        vals = np.full(len(idx), np.nan)
        vals[ok] = per_h[k[ok]]
        out[ev.index.get_indexer(idx)] = vals
    return out


def walk_forward(ev):
    first = ev["exit_time"].iloc[min(1000, len(ev) - 1)]
    start = (first + pd.offsets.MonthBegin(1)).normalize()
    months = pd.date_range(start, ev["date"].max(), freq="MS")
    picks = []
    for m in months:
        end = m + pd.offsets.MonthBegin(1)
        train = ev[ev["exit_time"] < m]
        test = ev[(ev["date"] >= m) & (ev["date"] < end)]
        if len(train) < 1000 or test.empty:
            continue
        t = test.copy()
        t["pred_long"] = M.model().fit(train[FEATURES], train["long_ret"]).predict(test[FEATURES])
        t["pred_short"] = M.model().fit(train[FEATURES], train["short_ret"]).predict(test[FEATURES])
        picks.append(t)
        print(f"  {m:%Y-%m}: trained on {len(train)}, {len(test)} events", flush=True)
    return pd.concat(picks), months


def realistic(df):
    long_all = df["long_ret"] + df["long_fund"] - Y.DELAY_COST
    short_all = df["short_ret"] + df["short_fund"] - Y.DELAY_COST
    return np.where(df["side"] == "long", long_all, short_all)


def blocked(df, limit, longs_too=False):
    f = df["fund_h"]
    b = (df["side"] == "short") & (f < limit)
    if longs_too:
        b |= (df["side"] == "long") & (f > -limit)
    return b.fillna(False).to_numpy()


def tstat(x):
    x = np.asarray(x, float)
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) if len(x) > 2 and x.std(ddof=1) > 0 else None


def main():
    t0 = time.time()
    print("building events (same as ml_5y.py) ...", flush=True)
    ev = Y.build_events()
    ev["fund_h"] = entry_funding(ev)
    print(f"{len(ev)} events; funding known for {ev['fund_h'].notna().mean():.1%} ({time.time() - t0:.0f}s)", flush=True)
    p, months = walk_forward(ev)
    chosen = M.choose(p, M.MIN_PRED)
    chosen = chosen.assign(ret=realistic(chosen)).reset_index(drop=True)

    rep = {"prereg": "PREREG_funding_filter.md", "events": int(len(ev)),
           "test_months": [str(months[0].date()), str(months[-1].date())],
           "model_picks": int(len(chosen)), "shorts_in_picks": int((chosen["side"] == "short").sum()), "runs": {}}

    base_taken, base_pnl = M.portfolio(chosen)
    rep["runs"]["baseline (model, realistic costs)"] = Y.stats(base_taken, base_pnl)

    def run(name, mask):
        kept = chosen[~mask]
        taken, pnl = M.portfolio(kept)
        s = Y.stats(taken, pnl)
        rem = chosen.loc[mask, "ret"]
        s["blocked_picks"] = int(mask.sum())
        s["blocked_avg_net_pct"] = round(float(rem.mean() * 100), 3) if len(rem) else None
        s["blocked_t"] = round(tstat(rem), 2) if tstat(rem) is not None else None
        s["blocked_win_pct"] = round(float((rem > 0).mean() * 100), 1) if len(rem) else None
        rep["runs"][name] = s
        return s, taken, pnl

    f1, f1_taken, f1_pnl = run("F1: skip shorts below -0.075%/h", blocked(chosen, F1_LIMIT))
    run("F2 (info): F1 + skip longs above +0.075%/h", blocked(chosen, F1_LIMIT, longs_too=True))
    for lim in INFO_LIMITS:
        run(f"info: skip shorts below {lim * 100:.4f}%/h", blocked(chosen, lim))

    # decision, exactly as pre-registered
    by_b = rep["runs"]["baseline (model, realistic costs)"]["by_year"]
    by_f = f1["by_year"]
    years = sorted(set(by_b) | set(by_f))
    better = [y for y in years if by_f.get(y, {}).get("pnl_usdt", 0) >= by_b.get(y, {}).get("pnl_usdt", 0)]
    c1 = f1["blocked_t"] is not None and f1["blocked_avg_net_pct"] < 0 and f1["blocked_t"] <= -2.0
    c2 = f1_pnl > base_pnl
    c3 = len(better) > len(years) / 2
    rep["decision"] = {"1_blocked_lose_t_le_-2": bool(c1), "2_pnl_higher": bool(c2),
                       "3_majority_years": f"{len(better)}/{len(years)} ({bool(c3)})",
                       "PASS": bool(c1 and c2 and c3)}

    print(f"\nunseen months {months[0]:%Y-%m} .. {months[-1]:%Y-%m}; {len(chosen)} model picks")
    print(f"{'':44} {'trades':>6} {'won%':>6} {'avg %':>7} {'P&L':>8} {'months+':>8} | blocked: n, avg %, t")
    for name, s in rep["runs"].items():
        blk = f"{s.get('blocked_picks', '')} {s.get('blocked_avg_net_pct', '')} {s.get('blocked_t', '')}"
        yrs = "  ".join(f"{y}:{v['pnl_usdt']:+.0f}" for y, v in s["by_year"].items())
        print(f"{name:44} {s['trades']:>6} {s['win_pct']:>6} {s['avg_net_pct']:>+7.3f} {s['pnl_usdt']:>+8.1f} "
              f"{s['months_positive']:>8} | {blk}\n{'':44} {yrs}")
    print("\ndecision:", rep["decision"])
    OUT.write_text(json.dumps(rep, indent=1, default=str))
    print(f"saved {OUT.name}  done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
