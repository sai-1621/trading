"""Strategy 2: which real ATM-premium ratio (real / VIX-priced) does the iron fly need to break even?

Runs the hourly 3-year backtest over a grid of ATM IV multipliers, with flat wings (wing IV = ATM IV) and with the
1.35x wing smile, and interpolates where total P&L crosses zero. This is the number scripts/log_expiry_chain.py
measures live.

    .venv\\Scripts\\python backtests\\s2_breakeven.py
"""
import json

import numpy as np
import pandas as pd

from common import RESULTS
from bt_iron_fly import run

GRID = [round(x, 2) for x in np.arange(0.56, 1.041, 0.04)]


def crossing(xs, ys):
    for (x0, y0), (x1, y1) in zip(zip(xs, ys), zip(xs[1:], ys[1:])):
        if y0 <= 0 < y1 or y0 >= 0 > y1:
            return round(x0 + (0 - y0) * (x1 - x0) / (y1 - y0), 3)
    return None


if __name__ == "__main__":
    rows = []
    for sym in ("NIFTY", "SENSEX"):
        for wing in (1.0, 1.35):
            for m in GRID:
                _, s = run(sym, "1h", iv_mult=m, wing_iv_mult=wing)
                rows.append({"index": sym, "wing_iv": wing, "atm_ratio": m, "trades": s.get("trades", 0),
                             "win_pct": s.get("win_pct"), "profit_factor": s.get("profit_factor"),
                             "total": s.get("total_pnl") or 0.0, "avg": s.get("avg_pnl"),
                             "max_dd": s.get("max_drawdown")})
                print(rows[-1], flush=True)
    t = pd.DataFrame(rows)
    t.to_csv(RESULTS / "s2_breakeven_grid.csv", index=False)
    out = {}
    print("\nBreak-even real ATM premium ratio (real straddle / VIX-priced straddle), hourly data, ~150 expiries:")
    for (sym, wing), g in t.groupby(["index", "wing_iv"]):
        g = g.sort_values("atm_ratio")
        be = crossing(list(g.atm_ratio), list(g.total))
        be_avg = crossing(list(g.atm_ratio), list(g.avg.fillna(0)))
        out[f"{sym} wing {wing}"] = {"break_even_total": be, "break_even_avg_trade": be_avg}
        print(f"  {sym:<7} wing IV {wing:.2f}x ATM: total P&L crosses 0 at {be}x, average trade at {be_avg}x")
    (RESULTS / "s2_breakeven.json").write_text(json.dumps(out, indent=1))
