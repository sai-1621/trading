"""The bot's skills/ follow the trader-* skill rules. No network.

    .venv\\Scripts\\python -m unittest tests.test_skill_sync -v
"""
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import market  # noqa: E402
from market import IST  # noqa: E402
from skills import budget, entry, risk, rule, timemachine  # noqa: E402

DAY = datetime(2026, 11, 9, tzinfo=IST)  # a Monday


def session(closes_after, vols_after, n=72):
    """3 range bars (high 101, volume 1000 each), then bars with the given closes and volumes, then flat bars."""
    t0 = DAY.replace(hour=9, minute=15)
    bars = [{"t": t0 + timedelta(minutes=5 * i), "o": 100, "h": 101, "l": 99, "c": 100, "v": 1000} for i in range(3)]
    for c, v in zip(closes_after, vols_after):
        bars.append({"t": t0 + timedelta(minutes=5 * len(bars)), "o": c, "h": c + 0.2, "l": c - 0.2, "c": c, "v": v})
    while len(bars) < n:
        bars.append({"t": t0 + timedelta(minutes=5 * len(bars)), "o": 100.5, "h": 100.7, "l": 100.3, "c": 100.5, "v": 900})
    return bars


class EntryVolume(unittest.TestCase):
    """trader-entry: a close above on thin volume is skipped; the next close above with enough volume triggers."""

    def setUp(self):
        self.bars = session([101.5, 101.8], [400, 2000])  # 9:30 bar thin, 9:35 bar strong

    def test_thin_close_waits(self):
        out = entry.check("X", self.bars, None, DAY.replace(hour=9, minute=35, second=5))
        self.assertEqual(out["status"], "waiting")
        self.assertEqual(out["note"], "closed above, volume too low")

    def test_next_strong_close_triggers(self):
        out = entry.check("X", self.bars, None, DAY.replace(hour=9, minute=40, second=5))
        self.assertEqual(out["status"], "triggered")
        self.assertEqual(out["price"], 101.8)

    def test_later_close_after_a_trigger_is_expired(self):
        bars =session([101.5, 101.8, 102.0], [400, 2000, 2000])
        out = entry.check("X", bars, None, DAY.replace(hour=9, minute=45, second=5))
        self.assertEqual(out["status"], "expired")


class RuleVolume(unittest.TestCase):
    def test_backtest_rule_matches_live(self):
        bars = session([101.5, 101.8], [400, 2000])
        res = rule.simulate_day(bars, rule.live_params(trend_filter=False), 0.0005)
        self.assertIsNotNone(res)
        self.assertEqual(res["entry_bar"], 4)

    def test_preregistered_rule_unchanged(self):
        bars = session([101.5, 101.8], [400, 2000])
        self.assertIsNone(rule.simulate_day(bars, rule.live_params(trend_filter=False, skip_thin=False), 0.0005))


def plan(entry_=200.0, stop=198.0, target=204.0):
    return {"symbol": "X", "entry": entry_, "stop": stop, "target": target, "setup": "orb"}


def state():
    return {"entries_today": [], "orders_today": 0, "positions": {}, "risk_log": []}


class Budget(unittest.TestCase):
    """trader-budget's refusals."""

    def test_sizes_a_good_plan(self):
        self.assertTrue(budget.size(plan(), 100000)["ok"])

    def test_refuses_low_reward_risk(self):
        out = budget.size(plan(target=201.0), 100000)
        self.assertFalse(out["ok"])
        self.assertIn("reward:risk", out["reason"])

    def test_refuses_stop_not_below_entry(self):
        out = budget.size(plan(stop=200.0), 100000)
        self.assertFalse(out["ok"])
        self.assertIn("not below", out["reason"])


class RiskFreshPrice(unittest.TestCase):
    """trader-risk measures from the higher of the plan's entry and a fresh price."""

    def setUp(self):
        self.order = budget.size(plan(), 100000)
        self.open = mock.patch.object(market, "now", return_value=DAY.replace(hour=10, minute=0))
        self.open.start()
        self.addCleanup(self.open.stop)

    def test_pass_at_plan_price(self):
        v = risk.check_entry(self.order, plan(), state(), 100000, fresh_price=200.0)
        self.assertEqual(v["verdict"], "PASS", v["reasons"])

    def test_block_after_price_ran_up(self):
        v = risk.check_entry(self.order, plan(), state(), 100000, fresh_price=201.5)
        self.assertEqual(v["verdict"], "BLOCK")
        self.assertEqual(v["checked_entry"], 201.5)
        self.assertTrue(any("loss at stop" in x for x in v["reasons"]))
        self.assertTrue(any("reward:risk" in x for x in v["reasons"]))

    def test_lower_price_never_shrinks_risk(self):
        v = risk.check_entry(self.order, plan(), state(), 100000, fresh_price=199.0)
        self.assertEqual(v["checked_entry"], 200.0)

    def test_min_order(self):
        small = {**self.order, "qty": 1, "value": 200.0}
        v = risk.check_entry(small, plan(), state(), 100000, fresh_price=200.0)
        self.assertTrue(any("under the minimum" in x for x in v["reasons"]))


class TimeMachineNotes(unittest.TestCase):
    def test_many_neither_note(self):
        flat = {}
        for d in range(25):
            day = DAY - timedelta(days=d + 1)
            t0 = day.replace(hour=9, minute=15)
            flat[day.date()] = [{"t": t0 + timedelta(minutes=5 * i), "o": 100, "h": 100.01, "l": 99.99, "c": 100,
                                 "v": 1000} for i in range(timemachine.needed_bars())]
        p = {"entry": 100.0, "stop": 99.0, "target": 102.0, "target_r": 2.0, "entry_bar_index": 4}
        out = timemachine.replay(flat, p, 0.05, replays=200)
        self.assertEqual(out["neither_pct"], 100.0)
        self.assertTrue(any("hit neither" in n for n in out["notes"]))


if __name__ == "__main__":
    unittest.main()
