"""Broker data for the Angel One bot: Angel One first when logged in, Yahoo as the fallback. No network: Angel One is
a fake SmartAPI object and Yahoo is replaced by a stub.

    .venv\\Scripts\\python -m unittest discover -s tests -v
"""
import sys
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import angel_client  # noqa: E402
import data  # noqa: E402
from market import IST, now  # noqa: E402
from skills import scanner, timemachine  # noqa: E402


def bar(t, c=100.0):
    return {"t": t, "o": c, "h": c + 1, "l": c - 1, "c": c, "v": 1000.0}


class FakeAngel:
    """Stands in for a logged-in AngelClient at the data layer."""

    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def candles(self, symbol, interval, days, tries=5):
        self.calls.append((symbol, interval, days))
        self.tries = getattr(self, "tries_seen", [])
        self.tries.append(tries)
        self.tries_seen = self.tries
        if self.fail:
            raise angel_client.AngelError("candles RELIANCE failed: {'status': False, 'message': 'Access denied'}")
        return [bar(now() - timedelta(days=1), 2500.0)]

    def ltp(self, symbol):
        self.calls.append((symbol, "ltp", 0))
        if self.fail:
            raise angel_client.AngelError("LTP failed")
        return 2501.5


class DataLayer(unittest.TestCase):
    def setUp(self):
        self.yahoo_calls = []
        self._yb, self._yp = data.yahoo_bars, data.yahoo_last_price
        data.yahoo_bars = lambda s, i="5m", r="5d": self.yahoo_calls.append((s, i, r)) or [bar(now(), 1.0)]
        data.yahoo_last_price = lambda s: self.yahoo_calls.append((s, "price", "")) or 1.0
        for k in ("angel", "yahoo", "fallback"):
            data.STATS[k] = 0

    def tearDown(self):
        data.yahoo_bars, data.yahoo_last_price = self._yb, self._yp
        data.set_broker(None)

    def test_logged_in_uses_angel_one_for_every_kind_of_bar(self):
        old = data.config.LIVE_BARS_SOURCE
        data.config.LIVE_BARS_SOURCE = "angel"
        self.addCleanup(setattr, data.config, "LIVE_BARS_SOURCE", old)
        fake = FakeAngel()
        d = data.Data(fake)
        d.intraday("RELIANCE", 1)
        d.daily("RELIANCE", "1y")
        data.bars("INFY", "1d", "3mo")
        data.bars("TCS", "5m", "60d")
        self.assertEqual(d.price("RELIANCE"), 2501.5)
        self.assertEqual(fake.calls, [("RELIANCE", "FIVE_MINUTE", 1), ("RELIANCE", "ONE_DAY", 366),
                                      ("INFY", "ONE_DAY", 93), ("TCS", "FIVE_MINUTE", 60), ("RELIANCE", "ltp", 0)])
        self.assertEqual(self.yahoo_calls, [])
        self.assertEqual((data.STATS["angel"], data.STATS["yahoo"], data.STATS["fallback"]), (5, 0, 0))
        self.assertEqual(d.source, "Angel One (Yahoo as fallback)")
        self.assertEqual(fake.tries_seen, [1, 5, 5, 5])       # live 5-minute loop: one try; the rest: patient

    def test_not_logged_in_uses_yahoo(self):
        d = data.Data(None)
        d.intraday("RELIANCE", 1)
        d.price("RELIANCE")
        self.assertEqual(self.yahoo_calls, [("RELIANCE", "5m", "1d"), ("RELIANCE", "price", "")])
        self.assertEqual(d.source, "Yahoo (may lag)")

    def test_default_live_bars_come_from_yahoo_the_rest_from_angel_one(self):
        fake = FakeAngel()
        d = data.Data(fake)
        d.intraday("RELIANCE", 1)                 # the per-bar loop: Yahoo (LIVE_BARS_SOURCE default)
        d.daily("RELIANCE", "3mo")                # everything else: Angel One
        d.price("RELIANCE")
        self.assertEqual(self.yahoo_calls, [("RELIANCE", "5m", "1d")])
        self.assertEqual(fake.calls, [("RELIANCE", "ONE_DAY", 93), ("RELIANCE", "ltp", 0)])

    def test_an_angel_one_failure_falls_back_to_yahoo_for_that_request(self):
        old = data.config.LIVE_BARS_SOURCE
        data.config.LIVE_BARS_SOURCE = "angel"
        self.addCleanup(setattr, data.config, "LIVE_BARS_SOURCE", old)
        d = data.Data(FakeAngel(fail=True))
        with self.assertLogs("bot", "WARNING") as cm:
            rows = d.intraday("RELIANCE", 1)
            first_error = data.STATS["last_error"]
            p = d.price("RELIANCE")
        self.assertEqual((rows[0]["c"], p), (1.0, 1.0))                  # the Yahoo stub's values
        self.assertEqual(self.yahoo_calls, [("RELIANCE", "5m", "1d"), ("RELIANCE", "price", "")])
        self.assertEqual(data.STATS["fallback"], 2)
        self.assertIn("RELIANCE 5m", first_error)
        self.assertIn("Access denied", first_error)
        self.assertEqual(data.STATS["last_error"], "RELIANCE ltp: LTP failed")
        self.assertEqual(sum("using Yahoo" in line for line in cm.output), 2)

    def test_intervals_angel_one_does_not_have_go_to_yahoo(self):
        fake = FakeAngel()
        data.set_broker(fake)
        data.bars("RELIANCE", "1wk", "1y")
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.yahoo_calls, [("RELIANCE", "1wk", "1y")])

    def test_rng_days(self):
        self.assertEqual([data.rng_days(r) for r in ("1d", "5d", "60d", "3mo", "6mo", "1y", "2y")],
                         [1, 5, 60, 93, 186, 366, 732])


class FakeSmartAPI:
    """getCandleData like SmartAPI: returns one bar per requested day, records the requests."""

    def __init__(self, fail_first=0):
        self.requests, self.fail_first, self.times = [], fail_first, []

    def getCandleData(self, params):
        self.requests.append(dict(params))
        self.times.append(time.time())
        if self.fail_first:
            self.fail_first -= 1
            return {"status": False, "message": "Access denied because of exceeding access rate", "data": None}
        a = datetime.strptime(params["fromdate"], "%Y-%m-%d %H:%M")
        b = datetime.strptime(params["todate"], "%Y-%m-%d %H:%M")
        rows, d = [], a
        while d.date() <= b.date():
            rows.append([d.strftime("%Y-%m-%dT09:15:00+05:30"), 10, 11, 9, 10.5, 1000])
            d += timedelta(days=1)
        return {"status": True, "data": rows}


def client(api):
    c = object.__new__(angel_client.AngelClient)      # no login: a fake API stands in for the session
    c.api, c.login_day = api, now().date()
    c.tokens = {"RELIANCE": {"token": "2885", "tradingsymbol": "RELIANCE-EQ", "tick": 0.05}}
    return c


class AngelCandles(unittest.TestCase):
    def setUp(self):
        self._gap = angel_client.HIST_GAP
        angel_client.HIST_GAP = 0.0

    def tearDown(self):
        angel_client.HIST_GAP = self._gap

    def test_long_ranges_are_split_and_joined_without_duplicates(self):
        api = FakeSmartAPI()
        rows = client(api).candles("RELIANCE", "FIVE_MINUTE", 250)      # 253 days > 100 per request
        self.assertEqual(len(api.requests), 3)
        self.assertTrue(all(r["symboltoken"] == "2885" and r["interval"] == "FIVE_MINUTE" for r in api.requests))
        ts = [r["t"] for r in rows]
        self.assertEqual(ts, sorted(set(ts)))                            # oldest first, no day twice at the seams
        self.assertEqual(rows[0]["t"].tzinfo.utcoffset(None), IST.utcoffset(None))

    def test_daily_fits_one_request(self):
        api = FakeSmartAPI()
        client(api).candles("RELIANCE", "ONE_DAY", 366)
        self.assertEqual(len(api.requests), 1)

    def test_nifty_index_token(self):
        api = FakeSmartAPI()
        client(api).candles("^NSEI", "ONE_DAY", 30)
        self.assertEqual(api.requests[0]["symboltoken"], "99926000")

    def test_unknown_symbol_is_an_error(self):
        with self.assertRaises(angel_client.AngelError):
            client(FakeSmartAPI()).candles("NOTASTOCK", "ONE_DAY", 30)

    def test_rate_limit_retry_then_success(self):
        api = FakeSmartAPI(fail_first=1)
        orig = time.sleep
        angel_client.time.sleep = lambda s: None
        try:
            rows = client(api).candles("RELIANCE", "ONE_DAY", 10)
        finally:
            angel_client.time.sleep = orig
        self.assertEqual(len(api.requests), 2)
        self.assertGreater(len(rows), 0)

    def test_requests_are_spaced_for_the_rate_limit(self):
        angel_client.HIST_GAP = 0.2
        angel_client._last_hist[0] = 0.0
        api = FakeSmartAPI()
        client(api).candles("RELIANCE", "FIVE_MINUTE", 250)              # 3 requests
        gaps = [b - a for a, b in zip(api.times, api.times[1:])]
        self.assertTrue(all(g >= 0.19 for g in gaps), gaps)


class RateLimit(unittest.TestCase):
    def test_the_rate_limit_exception_is_retried(self):
        api = FakeSmartAPI()
        orig, calls = api.getCandleData, []

        def flaky(p):
            calls.append(p)
            if len(calls) <= 2:   # what SmartAPI raises when Angel One answers with plain text
                raise Exception("Couldn't parse the JSON response received from the server: "
                                "b'Access denied because of exceeding access rate'")
            return orig(p)
        api.getCandleData = flaky
        angel_client.HIST_GAP = 0.0
        sleeps = []
        old = angel_client.time.sleep
        angel_client.time.sleep = lambda s: sleeps.append(s)
        try:
            rows = client(api).candles("RELIANCE", "ONE_DAY", 5)
        finally:
            angel_client.time.sleep = old
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [1.5, 3.0])
        self.assertGreater(len(rows), 0)

    def test_gives_up_after_five_tries(self):
        api = FakeSmartAPI()
        api.getCandleData = lambda p: (_ for _ in ()).throw(Exception("b'Access denied because of exceeding access rate'"))
        angel_client.HIST_GAP = 0.0
        old = angel_client.time.sleep
        angel_client.time.sleep = lambda s: None
        try:
            with self.assertRaises(angel_client.AngelError):
                client(api).candles("RELIANCE", "ONE_DAY", 5)
        finally:
            angel_client.time.sleep = old


class SessionRecovery(unittest.TestCase):
    def test_an_expired_session_logs_in_again_and_retries(self):
        api = FakeSmartAPI()
        expired = {"success": False, "message": "Invalid Token", "errorCode": "AG8001", "data": ""}   # real reply shape
        calls = []
        orig = api.getCandleData
        api.getCandleData = lambda p: calls.append(p) or (expired if len(calls) == 1 else orig(p))
        c = client(api)
        logins = []
        c.login = lambda: logins.append(1)          # the fake api stays in place after the "login"
        angel_client.HIST_GAP = 0.0
        rows = c.candles("RELIANCE", "ONE_DAY", 5)
        self.assertEqual(len(logins), 1)
        self.assertGreater(len(rows), 0)

    def test_other_errors_do_not_log_in_again(self):
        api = FakeSmartAPI()
        api.getCandleData = lambda p: {"success": False, "message": "Invalid API Key", "errorCode": "AG8004", "data": ""}
        c = client(api)
        logins = []
        c.login = lambda: logins.append(1)
        angel_client.HIST_GAP = 0.0
        orig = angel_client.time.sleep
        angel_client.time.sleep = lambda s: None
        try:
            with self.assertRaises(angel_client.AngelError):
                c.candles("RELIANCE", "ONE_DAY", 5)
        finally:
            angel_client.time.sleep = orig
        self.assertEqual(logins, [])


class SkillsUseTheBroker(unittest.TestCase):
    def setUp(self):
        self._bars, self._uni = data.bars, data.universe
        self.calls = []

    def tearDown(self):
        data.bars, data.universe = self._bars, self._uni

    def test_scanner_daily_closes_go_through_data_bars(self):
        days = [now() - timedelta(days=40 - i) for i in range(40)]
        data.universe = lambda name=None: {"RELIANCE": "Reliance Industries"}
        data.bars = lambda s, i="5m", r="5d": self.calls.append((s, i, r)) or [bar(t, 100 + k) for k, t in enumerate(days)]
        orig_watch = scanner.config.WATCH
        scanner.config.WATCH = ["RELIANCE"]
        try:
            res = scanner.scan(write=False)
        finally:
            scanner.config.WATCH = orig_watch
        self.assertEqual(self.calls, [("RELIANCE", "1d", "3mo")])
        self.assertEqual(res["hits"][0]["symbol"], "RELIANCE")

    def test_time_machine_history_goes_through_data_bars(self):
        def fake(s, i="5m", r="5d"):
            self.calls.append((s, i, r))
            return []
        data.bars = fake
        out = timemachine.run({"symbol": "RELIANCE", "entry": 100, "stop": 99, "target": 101, "target_r": 1},
                              now().date(), 0.05)
        self.assertEqual(self.calls[0], ("RELIANCE", "5m", "60d"))
        self.assertFalse(out["ok"])                                       # no history -> refuses, never guesses


if __name__ == "__main__":
    unittest.main()
