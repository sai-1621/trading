"""Swing v2 momentum holdout check, as pre-registered in research/PREREG_swing_v2_momentum.md."""
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import swing_v1 as s1  # noqa: E402

CHOSEN = {"family": "M", "n": 20, "exit": "low20", "stock": "none", "market": "none"}


def main():
    stocks, nmap, nifty = s1.load()
    rows = s1.per_trade(stocks, nmap, CHOSEN, s1.HOLD)
    st = s1.stats(rows)
    pf = s1.portfolio(stocks, nmap, nifty, CHOSEN, s1.HOLD)
    passed = ((st.get("avg_ret_pct") or 0) > 0 and (st.get("t") or 0) >= 2.0 and pf["final_rupees"] > s1.CAPITAL
              and pf["total_return_pct"] > pf["nifty_buy_hold_pct"] and pf["max_drawdown_pct"] >= -25)
    all_m = []
    for c in itertools.product(*s1.M_GRID.values()):
        v = dict(zip(s1.M_GRID, c))
        all_m.append({**v, **s1.stats(s1.per_trade(stocks, nmap, v, s1.HOLD))})
    rep = {"chosen": CHOSEN, "holdout_trades": st, "portfolio_holdout": pf, "passed": passed, "all_24_holdout": all_m}
    Path(__file__).with_name("swing_v2_momentum_results.json").write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps({"holdout_trades": st, "portfolio": {k: v for k, v in pf.items() if k != "curve_monthly"},
                      "passed": passed}, indent=1))
    print("monthly curve:", pf["curve_monthly"])
    for x in sorted(all_m, key=lambda x: x.get("t") or -99, reverse=True):
        print(f"  n{x['n']} {x['exit']:<5} {x['stock']:<5} {x['market']:<8} n={x['trades']} win={x.get('win_pct')}%"
              f" avg={x.get('avg_ret_pct')}% t={x.get('t')}")


if __name__ == "__main__":
    main()
