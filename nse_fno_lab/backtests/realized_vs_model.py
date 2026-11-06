r"""How big are expiry-day moves after entry, compared with the straddle the model charges?

For every expiry day in the hourly data: model ATM straddle at 10:15 (Black-Scholes, IV = yesterday's India VIX,
trading time) vs the realised move from 10:15 to 15:15. A fair straddle is worth about the average absolute
move. ratio < 1 means VIX-priced options overstate intraday movement (good for a seller IF the real market
prices options like the model). The real expiry-day premium is the unknown: check it live in paper mode.

    .venv\Scripts\python backtests\realized_vs_model.py
"""
import numpy as np
import pandas as pd

from common import ROOT  # noqa: F401
import config as C
import greeks
from bt_iron_fly import DATA, expiry_days
from data_yahoo import bars, vix_daily
from nse_calendar import years_to_expiry

for sym in ("NIFTY", "SENSEX"):
    df = bars(DATA[sym], "1h")
    vix = vix_daily()
    days = sorted(set(df.index.date))
    rows = []
    for d in sorted(expiry_days(sym, days)):
        day = df[df.index.date == d]
        prev = [v for k, v in vix.items() if k < d]
        if len(day) < 6 or not prev:
            continue
        t_in = day.index[0] + pd.Timedelta(hours=1)          # 10:15
        s_in = day["close"].iloc[0]
        s_out = day["open"].iloc[-1]                          # 15:15
        K = round(s_in / C.STRIKE_STEP[sym]) * C.STRIKE_STEP[sym]
        T = years_to_expiry(t_in.to_pydatetime(), d)
        iv = prev[-1] / 100
        straddle = float(greeks.price(s_in, K, T, C.RISK_FREE, iv, 1) + greeks.price(s_in, K, T, C.RISK_FREE, iv, -1))
        payoff = abs(s_out - K)
        rows.append({"date": d, "straddle": straddle, "payoff_at_1515": payoff,
                     "range_after": day["high"].iloc[1:].max() - day["low"].iloc[1:].min()})
    r = pd.DataFrame(rows)
    print(f"{sym}: {len(r)} expiry days  model straddle avg {r.straddle.mean():.1f} pts  "
          f"straddle value at 15:15 avg {r.payoff_at_1515.mean():.1f} pts  "
          f"ratio {r.payoff_at_1515.mean() / r.straddle.mean():.2f}  "
          f"(median ratio per day {np.median(r.payoff_at_1515 / r.straddle):.2f}, "
          f"days payoff > straddle {(r.payoff_at_1515 > r.straddle).mean() * 100:.0f}%)")
