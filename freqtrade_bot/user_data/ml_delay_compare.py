"""Like-for-like comparison for ml_delay.py: every model pick (no portfolio), and the portfolio with the
original rule (each trade holds its slot for the full 24 h). Uses the cached 1-minute candles."""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ml_delay as D, ml_pumpdump as M

picks = D.walk_forward_picks()
picks["year"] = pd.to_datetime(picks["date"]).dt.year
rows = []
for _, r in picks.iterrows():
    a = D.minutes(r["pair"], r["entry_time"])
    out = {"hourly": r["ret"]}
    for d in D.DELAYS:
        ret, j = D.replay(a, r["side"], d)
        f = D.funding(r["pair"], r["entry_time"] + pd.Timedelta(minutes=d), r["entry_time"] + pd.Timedelta(minutes=j + 1), r["side"])
        out[f"{d}m"], out[f"{d}m+fund"] = ret, ret + f
    rows.append(out)
R = pd.concat([picks.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
cols = ["hourly"] + [c for d in D.DELAYS for c in (f"{d}m", f"{d}m+fund")]
print(f"\nALL {len(R)} model picks, average net % per trade (no slot limit)")
print(f"{'':14}{'all':>8}{'2025':>8}{'2026':>8}{'won%':>7}")
for c in cols:
    y = R.groupby("year")[c].mean() * 100
    print(f"{c:14}{R[c].mean()*100:>+8.3f}{y.get(2025,0):>+8.3f}{y.get(2026,0):>+8.3f}{(R[c]>0).mean()*100:>7.1f}")
print("\nPORTFOLIO, original rule (slot held 24 h, max 5 open, 100 USDT each)")
for c in cols:
    taken, pnl = M.portfolio(R.assign(ret=R[c]))
    y = taken.groupby("year")["ret"].sum() * 100
    print(f"{c:14} trades {len(taken):>5}  avg {taken['ret'].mean()*100:>+7.3f}%  P&L {pnl:>+8.1f}  2025 {y.get(2025,0):>+7.1f}  2026 {y.get(2026,0):>+7.1f}")
