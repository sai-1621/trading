"""Market mood ("breadth"): every hour, the share of Binance USDT futures coins whose close is above their own
20-day (480-hour) average. Built from the downloaded hourly data; saved to user_data/breadth_1h.feather.

    .venv\\Scripts\\python user_data\\build_breadth.py
"""
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "binance" / "futures"
OUT = HERE / "breadth_1h.feather"
WINDOW = 480

above, valid = None, None
files = sorted(DATA.glob("*-1h-futures.feather"))
for f in files:
    df = pd.read_feather(f, columns=["date", "close"]).set_index("date")["close"]
    sma = df.rolling(WINDOW).mean()
    a = (df > sma).astype(float).where(sma.notna())
    v = sma.notna().astype(float)
    above = a.fillna(0) if above is None else above.add(a.fillna(0), fill_value=0)
    valid = v if valid is None else valid.add(v, fill_value=0)

out = pd.DataFrame({"breadth": above / valid, "coins": valid}).dropna()
out = out[out["coins"] >= 30].reset_index()
out.to_feather(OUT)
print(f"{len(files)} coins -> {OUT.name}: {len(out)} hours, {out['date'].min()} .. {out['date'].max()}")
by_q = out.set_index("date")["breadth"].resample("QE").mean().round(2)
print("average breadth by quarter:\n", by_q.to_string())
