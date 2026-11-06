"""One-look holdout check of the near-miss variant (PREREG.md addendum): T don55 long_only."""
import json
from pathlib import Path

import search_v1 as S

C, H, L, X, F = S.load()
W = S.size(S.donchian(C, H, L, 55, 20, True), C)
bt, held = S.backtest(W, X, F)
h = S.metrics(bt, S.HOLD)
n, w = S.trade_wins(held, X, S.HOLD)
h.update({"trades": n, "win_pct": round(w / n * 100, 1) if n else None})
passed = h["total_return_pct"] > 0 and (h["sharpe"] or 0) >= 0.5 and h["max_dd_pct"] >= -40 and h["worst_day_pct"] > -30
yearly = {str(y): round(((1 + g).prod() - 1) * 100, 1) for y, g in bt["net"].groupby(bt.index.year)}
eq = (1 + bt["net"].loc[S.HOLD[0]:S.HOLD[1]]).cumprod()
quarterly = {str(k.date()): round((v - 1) * 100, 1) for k, v in eq.resample("QE").last().items()}
rep = {"variant": "T don55 long_only (near miss)", "holdout": h, "btc_buy_hold_holdout_pct": S.buy_hold(X, S.HOLD),
       "yearly_pct": yearly, "holdout_equity_by_quarter_pct": quarterly, "passed": bool(passed)}
Path(__file__).with_name("results_nearmiss_v1.json").write_text(json.dumps(rep, indent=1, default=float))
print(json.dumps(rep, indent=1, default=float))
