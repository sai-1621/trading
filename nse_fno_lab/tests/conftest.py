import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nse_calendar import IST  # noqa: E402


def make_bars(days=12, start="2026-10-05", seed=1, base=25000.0, vol=True, drift=0.0):
    """Random-walk 5-minute bars for `days` weekdays, 09:15-15:25 IST."""
    rng = np.random.default_rng(seed)
    rows, px = [], base
    d = pd.Timestamp(start)
    n = 0
    while n < days:
        if d.weekday() < 5:
            for i in range(75):
                ts = pd.Timestamp(d.year, d.month, d.day, 9, 15, tz=IST) + pd.Timedelta(minutes=5 * i)
                o = px
                c = o * (1 + drift + rng.normal(0, 0.0012))
                h, l = max(o, c) * (1 + abs(rng.normal(0, 0.0005))), min(o, c) * (1 - abs(rng.normal(0, 0.0005)))
                rows.append((ts, o, h, l, c, float(rng.integers(1000, 5000)) if vol else 0.0))
                px = c
            px *= 1 + rng.normal(0, 0.003)   # overnight gap
            n += 1
        d += pd.Timedelta(days=1)
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).set_index("ts")


@pytest.fixture
def bars():
    return make_bars()
