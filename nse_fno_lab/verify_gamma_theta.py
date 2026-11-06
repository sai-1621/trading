"""Prints the gamma-vs-theta filter step by step for a few NIFTY situations (spot 25,000).

    .venv\\Scripts\\python verify_gamma_theta.py

ATR yardstick: at India VIX v, a normal 5-minute NIFTY move is about spot * v/100 / sqrt(252 * 75) points
(about 25 points at VIX 14, about 40 at VIX 22). A breakout bar runs well above that; chop runs well below.
"""
import pandas as pd

import config as C
from engines.opt_buy import StrategyEngine
from nse_calendar import IST

SPOT = 25000
CASES = [  # name, bar time, VIX, ATR(5m) points, min DTE days
    ("VIX 14 breakout (Thu)", "2026-11-05 10:30", 14, 40, None),
    ("VIX 14 normal bar (Thu)", "2026-11-05 10:30", 14, 25, None),
    ("VIX 22 chop (Mon)", "2026-11-16 10:00", 22, 20, None),
    ("VIX 22 chop (Tue)", "2026-11-17 10:00", 22, 20, None),
    ("VIX 22 breakout (Tue)", "2026-11-17 10:00", 22, 60, None),
    ("VIX 22 chop, 0-DTE (Tue)", "2026-11-17 10:00", 22, 20, 0),
]


def rows():
    out = []
    for name, t, vix, atr, dte in CASES:
        cfg = dict(C.S1)
        if dte is not None:
            cfg["min_dte_days"] = dte
        e = StrategyEngine(cfg)
        ts = pd.Timestamp(t, tz=IST)
        opt = e._pick_option(None, ts, SPOT, 1, vix)
        gt = e.gamma_theta_ratio(opt, atr)
        er = e.edge_ratio(opt, atr)
        out.append({"case": name, "expiry": opt["expiry"].isoformat(), "premium": round(opt["premium"], 1),
                    "delta": round(opt["delta"], 3), "gamma": round(opt["gamma"], 6),
                    "theta_day": round(opt["theta"], 2), "theta_5m": round(e.intraday_theta(opt["theta"], 5), 3),
                    "theta_30m": round(er["theta_hold"], 2), "atr5": atr,
                    "gamma_pnl_5m": round(0.5 * opt["gamma"] * atr ** 2, 3), "gamma/theta": round(gt, 2),
                    "pass": gt >= cfg["gamma_theta_min"], "edge_ratio": round(er["ratio"], 1)})
    return out


if __name__ == "__main__":
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    print(f"filter: 0.5*gamma*ATR5^2 / (|daily theta|*5/375) >= {C.S1['gamma_theta_min']}   (spot {SPOT})\n")
    print(pd.DataFrame(rows()).to_string(index=False))
