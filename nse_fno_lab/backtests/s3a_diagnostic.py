r"""Strategy 3A final diagnostic (decided 1 Nov 2026 before running):
variant C (no time cap, EMA20 exit, NIFTY > 50-day SMA) with and without a 52-week-high proximity filter
(close >= 0.85 x 252-day high). Governance rule: keep 3A only if the filtered version reaches t >= 2.0;
otherwise strategy 3 is frozen and shelved.

    .venv\Scripts\python backtests\s3a_diagnostic.py
"""
import json

import pandas as pd

from common import RESULTS
from bt_rs_variants import START, VARIANTS, daily_frames, placebo, run_3a
from data_yahoo import bars

BASE = VARIANTS["C  no cap, EMA20 exit, regime"]
RUNS = {"C  (no filter)": BASE, "C + close >= 0.85 x 52-week high": {**BASE, "near_52w": 0.85}}

if __name__ == "__main__":
    fr = daily_frames()
    out, rows = {}, []
    for name, cfg in RUNS.items():
        tr, s = run_3a(fr, cfg)
        pl = placebo(fr, cfg)
        out[name] = {**s, "placebo": pl}
        rows.append({"variant": name, "trades": s.get("trades"), "win %": s.get("win_pct"), "PF": s.get("profit_factor"),
                     "avg R": s.get("avg_R"), "cost/R": s.get("cost_per_R"), "total Rs": s.get("total_pnl"),
                     "t": s.get("t_stat_per_trade"), "%/yr standalone": s.get("simple_return_pct_per_year"),
                     "%/yr + LIQUIDBEES 6%": s.get("cash_plus_pct_per_year"), "capital used %": s.get("avg_capital_used_pct"),
                     "max DD": s.get("max_drawdown"), "placebo avg R": pl.get("avg_R")})
        if not tr.empty:
            tr.to_csv(RESULTS / f"s3a_diag_{'52w' if 'near' in str(cfg) or cfg.get('near_52w') else 'base'}_trades.csv", index=False)
    t = pd.DataFrame(rows)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 20)
    print(t.to_string(index=False))
    for name in RUNS:
        bh = out[name].get("by_half", {})
        print(f"  {name:<36} " + "   ".join(f"{k}: {v['trades']} / {v['pnl']:+,} / {v['avg_R']:+.3f}R" for k, v in bh.items()))
    n = bars("^NSEI", "1d")["close"]
    n = n[n.index >= pd.Timestamp(START)]
    yrs = (n.index[-1] - n.index[0]).days / 365.25
    print(f"\nNIFTY buy-and-hold: {((n.iloc[-1] / n.iloc[0]) ** (1 / yrs) - 1) * 100:.1f}% a year (price only; "
          f"about +1.2-1.5%/yr more with dividends, e.g. via NIFTYBEES)")
    tf = out["C + close >= 0.85 x 52-week high"].get("t_stat_per_trade") or 0
    verdict = "KEEP 3A (t >= 2.0)" if tf >= 2.0 else "FREEZE strategy 3 (t < 2.0)"
    print(f"\nGovernance rule: filtered t = {tf} -> {verdict}")
    out["verdict"] = verdict
    (RESULTS / "s3a_diagnostic.json").write_text(json.dumps(out, indent=1, default=str))
