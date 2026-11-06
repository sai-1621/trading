"""Shared backtest helpers: import path, bar ticks, summaries."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RESULTS = ROOT / "backtests" / "results"
RESULTS.mkdir(parents=True, exist_ok=True)


def ticks(bar: pd.Series, ts: pd.Timestamp, long: bool | None, minutes: int = 5):
    """Price path inside a completed bar, worst case first: (time, price, is_bar_close).
    long=True: low before high; long=False: high before low; None (neutral): both extremes then close."""
    mid = ts + pd.Timedelta(minutes=minutes / 2)
    end = ts + pd.Timedelta(minutes=minutes)
    if long is True:
        seq = [bar["low"], bar["high"]]
    elif long is False:
        seq = [bar["high"], bar["low"]]
    else:
        seq = [bar["high"], bar["low"]]
    return [(mid, float(p), False) for p in seq] + [(end, float(bar["close"]), True)]


def summary(tr: pd.DataFrame, capital: float, days: int) -> dict:
    if tr.empty:
        return {"trades": 0, "sessions": days}
    pnl = tr["pnl"]
    eq = pnl.cumsum()
    by_day = tr.groupby("date")["pnl"].sum()
    return {"sessions": days, "trades": int(len(tr)), "win_pct": round(float((pnl > 0).mean() * 100), 1),
            "avg_pnl": round(float(pnl.mean()), 1), "total_pnl": round(float(pnl.sum()), 0),
            "return_pct": round(float(pnl.sum() / capital * 100), 2),
            "avg_win": round(float(pnl[pnl > 0].mean()), 0) if (pnl > 0).any() else 0,
            "avg_loss": round(float(pnl[pnl <= 0].mean()), 0) if (pnl <= 0).any() else 0,
            "max_drawdown": round(float((eq.cummax() - eq).max()), 0),
            "profit_factor": round(float(pnl[pnl > 0].sum() / -pnl[pnl < 0].sum()), 2) if (pnl < 0).any() else None,
            "charges": round(float(tr["charges"].sum()), 0),
            "pnl_before_charges": round(float((pnl + tr["charges"]).sum()), 0),
            "days_green": f"{int((by_day > 0).sum())}/{len(by_day)}",
            "exits": tr["reason"].value_counts().to_dict()}


def print_summary(title: str, s: dict):
    print(f"\n== {title}")
    for k, v in s.items():
        print(f"  {k:<20} {v}")


def t_stat(x: pd.Series) -> float:
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) if len(x) > 2 and x.std() > 0 else float("nan")
