"""Paper momentum swing bot: the daily step (swing_core) and the bot's catch-up cycle. No network.

    .venv\\Scripts\\python -m unittest discover -s tests -v
"""
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import swing_core as sc  # noqa: E402

D0 = date(2025, 1, 1)


def series(closes, start=D0, opens=None):
    """Daily bars on consecutive days (weekends ignored: the core only follows the given dates)."""
    out = []
    for i, c in enumerate(closes):
        o = opens[i] if opens else c
        out.append({"t": start + timedelta(days=i), "o": o, "h": max(o, c), "l": min(o, c), "c": c})
    return out


def flat_then(n_flat, tail, level=100.0):
    return [level] * n_flat + tail


def run(bars_by_sym, days, st=None):
    st = st or sc.new_state(days[0])
    evs = []
    for d in days:
        evs += sc.step(st, d, {k: [b for b in v if b["t"] <= d] for k, v in bars_by_sym.items()})
    return st, evs


class SignalsAndFills(unittest.TestCase):
    def test_breakout_buys_at_next_open_and_breakdown_sells_at_next_open(self):
        closes = flat_then(200, [101, 102, 103] + [102] * 20 + [90, 95])
        opens = list(closes)
        opens[201] = 101.5                       # the open after the first breakout close (index 200)
        bars = {"AAA": series(closes, opens=opens)}
        days = [b["t"] for b in bars["AAA"]][199:]
        st, ev = run(bars, days)
        self.assertIn("signal BUY AAA (20d +1.0%)", ev)
        buy = next(e for e in ev if e.startswith("BUY AAA"))
        self.assertIn("@ 101.53", buy)          # 101.5 + half the 0.05% spread
        self.assertEqual(len(st["closed"]), 1)
        tr = st["closed"][0]
        self.assertEqual(tr["exit"], round(95 * (1 - sc.SPREAD / 2), 2))   # next open after the close of 90
        self.assertLess(tr["pnl"], 0)
        self.assertEqual(st["positions"], {})

    def test_no_buy_without_200_days_of_history(self):
        bars = {"NEW": series(flat_then(50, [110]))}
        st, ev = run(bars, [b["t"] for b in bars["NEW"]])
        self.assertFalse([e for e in ev if "BUY" in e])

    def test_at_most_five_positions_strongest_first(self):
        bars = {}
        for k, jump in enumerate([101, 102, 103, 104, 105, 106, 107]):
            bars[f"S{k}"] = series(flat_then(200, [jump, jump]))
        days = [b["t"] for b in bars["S0"]][199:]
        st, ev = run(bars, days)
        self.assertEqual(len(st["positions"]), 5)
        self.assertEqual(set(st["positions"]), {"S2", "S3", "S4", "S5", "S6"})

    def test_cash_reserved_at_cost_not_at_20000_like_the_backtest(self):
        # Rs 38,639 of cash: the first buy costs ~Rs 17,050 (5 shares at ~3,400), leaving more than Rs 20,000,
        # so the backtest queued a second buy (it did on 1 Sep 2025: TVSMOTOR then MARUTI)
        st = sc.new_state(D0)
        st["cash"] = 38639.0
        bars = {"A": series(flat_then(200, [3400.0], level=3000.0)), "B": series(flat_then(200, [3300.0], level=3000.0))}
        d = bars["A"][-1]["t"]
        sc.step(st, d, bars)
        self.assertEqual({o["symbol"] for o in st["pending"]}, {"A", "B"})

    def test_price_above_20000_is_never_bought(self):
        bars = {"MRF": series(flat_then(200, [150000.0], level=140000.0))}
        st, ev = run(bars, [b["t"] for b in bars["MRF"]][199:])
        self.assertFalse(st["pending"])

    def test_order_waits_when_the_stock_has_no_bar(self):
        bars = {"AAA": series(flat_then(200, [105]))}
        d_sig = bars["AAA"][-1]["t"]
        st = sc.new_state(d_sig)
        sc.step(st, d_sig, bars)
        self.assertEqual(st["pending"][0]["symbol"], "AAA")
        sc.step(st, d_sig + timedelta(days=1), bars)          # no bar that day (suspended)
        self.assertEqual(len(st["pending"]), 1)
        self.assertFalse(st["positions"])

    def test_charges_match_the_research(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
        import swing_v1
        for v in (500.0, 19999.0, 25000.0):
            for side in ("buy", "sell"):
                self.assertAlmostEqual(sc.delivery_charges(v, side), swing_v1.delivery_charges(v, side))


class Replay(unittest.TestCase):
    def test_matches_the_backtest_portfolio_on_the_holdout(self):
        cache = Path(__file__).resolve().parents[1] / "cache"
        if not list(cache.glob("angel_d1_*_2000.json")):
            self.skipTest("research daily cache not present")
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
        import swing_v1 as s1
        stocks, nmap, nifty = s1.load()
        bars = {sym: [{"t": s["t"][i], "o": s["o"][i], "h": s["h"][i], "l": s["l"][i], "c": s["c"][i]}
                      for i in range(len(s["t"]))] for sym, s in stocks.items()}
        days = [d for d in nifty["t"] if s1.HOLD[0] <= d <= s1.HOLD[1]]
        st, _ = run(bars, days)
        v = {"family": "M", "n": 20, "exit": "low20", "stock": "none", "market": "none"}
        bt = s1.portfolio(stocks, nmap, nifty, v, s1.HOLD)["final_rupees"]
        self.assertAlmostEqual(st["equity"][-1][1] / bt, 1.0, delta=0.01)


class BotCycle(unittest.TestCase):
    def test_first_run_starts_on_the_last_finished_day_then_catches_up(self):
        import market
        import swing_bot
        from market import IST
        closes = flat_then(240, [])
        nifty = series(closes, start=date(2025, 6, 1))
        stock = series(closes, start=date(2025, 6, 1))
        last = nifty[-1]["t"]
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(swing_bot, "STATE", Path(tmp) / "s.json"), \
                mock.patch.object(swing_bot, "TRADES", Path(tmp) / "t.jsonl"), \
                mock.patch.object(swing_bot, "UNIVERSE", Path(tmp) / "u.json"), \
                mock.patch.object(swing_bot, "connect", return_value="test"), \
                mock.patch.object(market, "is_trading_day", return_value=True):
            (Path(tmp) / "u.json").write_text(json.dumps(["AAA"]))
            # 17:00 on the last day: that day is final
            at = datetime.combine(last, datetime.min.time(), IST).replace(hour=17)
            with mock.patch.object(market, "now", return_value=at), \
                    mock.patch.object(swing_bot, "daily_bars", side_effect=lambda s: nifty if s == "^NSEI" else stock):
                swing_bot.cycle()
            st = json.loads((Path(tmp) / "s.json").read_text())
            self.assertEqual(st["start"], str(last))
            self.assertEqual(st["last_day"], str(last))
            self.assertEqual(st["mode"], "paper")
            # two more days, bot was off for one of them: both are processed in order
            more_n = series(closes + [100, 100], start=date(2025, 6, 1))
            at2 = datetime.combine(more_n[-1]["t"], datetime.min.time(), IST).replace(hour=17)
            with mock.patch.object(market, "now", return_value=at2), \
                    mock.patch.object(swing_bot, "daily_bars", side_effect=lambda s: more_n):
                swing_bot.cycle()
            st = json.loads((Path(tmp) / "s.json").read_text())
            self.assertEqual(st["last_day"], str(more_n[-1]["t"]))
            self.assertEqual(len(st["equity"]), 3)

    def test_todays_bar_is_not_used_before_16_00(self):
        import market
        import swing_bot
        from market import IST
        nifty = series(flat_then(240, []), start=date(2025, 6, 1))
        last = nifty[-1]["t"]
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(swing_bot, "STATE", Path(tmp) / "s.json"), \
                mock.patch.object(swing_bot, "TRADES", Path(tmp) / "t.jsonl"), \
                mock.patch.object(swing_bot, "UNIVERSE", Path(tmp) / "u.json"), \
                mock.patch.object(swing_bot, "connect", return_value="test"), \
                mock.patch.object(market, "is_trading_day", return_value=True), \
                mock.patch.object(market, "now",
                                  return_value=datetime.combine(last, datetime.min.time(), IST).replace(hour=13)), \
                mock.patch.object(swing_bot, "daily_bars", side_effect=lambda s: nifty):
            (Path(tmp) / "u.json").write_text(json.dumps(["AAA"]))
            swing_bot.cycle()
            st = json.loads((Path(tmp) / "s.json").read_text())
            self.assertEqual(st["last_day"], str(last - timedelta(days=1)))


if __name__ == "__main__":
    unittest.main()
