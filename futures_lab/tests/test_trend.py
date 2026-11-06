r"""Paper crypto trend bot: the daily step replays the research backtest; the bot's catch-up works. No network.

    ..\freqtrade_bot\.venv\Scripts\python -m unittest discover -s tests -v
"""
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import search_v1 as S  # noqa: E402
import trend_core as T  # noqa: E402


class Replay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.C, cls.H, cls.L, cls.X, cls.F = S.load()
        cls.W = T.target_weights(cls.C, cls.H, cls.L)

    def test_matches_the_research_backtest(self):
        bt, _ = S.backtest(self.W, self.X, self.F)
        days = [d for d in self.X.index if d >= pd.Timestamp("2022-01-02", tz="UTC")][:-1]
        st = T.new_state(days[0].date())
        prev = None
        for e in days:
            fund = {c: float(self.F.at[prev, c]) for c in self.C.columns} if prev is not None else {}
            T.step(st, e.date(), {c: float(self.X.at[e, c]) for c in self.C.columns},
                   T.weights_on(self.W, e - pd.Timedelta(days=1)), fund)
            prev = e
        # research: the return of the window starting day E is booked on E+1
        ref = (1 + bt["net"].loc[days[1]:days[-1]]).prod()
        self.assertAlmostEqual(st["equity"] / T.CAPITAL / ref, 1.0, delta=0.01)
        self.assertGreater(len(st["closed"]), 30)

    def test_never_short_and_within_leverage_caps(self):
        w = self.W.loc["2022-01-01":]
        self.assertGreaterEqual(w.min().min(), 0.0)
        self.assertLessEqual(w.max().max(), 0.75 + 1e-9)
        self.assertLessEqual(w.abs().sum(axis=1).max(), 3.0 + 1e-9)

    def test_flat_when_btc_below_its_200_day_average(self):
        btc = self.C["BTC"]
        below = btc < btc.rolling(200).mean()
        days = below[below].index
        days = [d for d in days if d >= pd.Timestamp("2022-08-01", tz="UTC")]
        self.assertTrue(days)
        self.assertEqual(float(self.W.loc[days].abs().sum().sum()), 0.0)


class Step(unittest.TestCase):
    def test_pnl_funding_and_costs(self):
        st = T.new_state(date(2026, 1, 1))
        T.step(st, date(2026, 1, 1), {"BTC": 100.0}, {"BTC": 0.5}, {})
        self.assertAlmostEqual(st["equity"], 1000 * (1 - 0.5 * 0.001))
        e0 = st["equity"]
        # BTC +10%, funding 0.03% paid by the long, then fully sold
        T.step(st, date(2026, 1, 2), {"BTC": 110.0}, {}, {"BTC": 0.0003})
        self.assertAlmostEqual(st["equity"], e0 * (1 + 0.5 * 0.10 - 0.5 * 0.0003 - 0.5 * 0.001))
        self.assertEqual(len(st["closed"]), 1)
        self.assertGreater(st["closed"][0]["pnl_pct"], 0)
        self.assertEqual(st["weights"], {})


class Bot(unittest.TestCase):
    def test_catches_up_missed_days_in_order(self):
        import trend_bot as B
        idx = pd.date_range("2025-01-01", periods=300, freq="D", tz="UTC")
        C = pd.DataFrame({c: [100.0 + i * 0.1 for i in range(300)] for c in T.COINS}, index=idx)
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(B, "STATE", Path(tmp) / "s.json"), \
                mock.patch.object(B, "daily_bars", return_value=(C, C * 1.01, C * 0.99)), \
                mock.patch.object(B, "fill_prices", side_effect=lambda d: {c: 100.0 for c in T.COINS}), \
                mock.patch.object(B, "funding_between", return_value={}):
            B.cycle(datetime(2025, 10, 20, 2, 0, tzinfo=timezone.utc))
            st = json.loads((Path(tmp) / "s.json").read_text())
            self.assertEqual(st["last_exec_day"], "2025-10-20")
            self.assertEqual(st["mode"], "paper")
            B.cycle(datetime(2025, 10, 23, 0, 30, tzinfo=timezone.utc))      # before 01:05: up to the 22nd
            st = json.loads((Path(tmp) / "s.json").read_text())
            self.assertEqual(st["last_exec_day"], "2025-10-22")
            self.assertEqual([h[0] for h in st["history"]], ["2025-10-20", "2025-10-21", "2025-10-22"])


if __name__ == "__main__":
    unittest.main()
