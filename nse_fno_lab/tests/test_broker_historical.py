"""Broker downloader: chunking, response parsing, merge (no network: a fake HTTP session)."""
from datetime import datetime

import pandas as pd

from data import broker_historical as BH


def test_chunks_cover_range_without_gaps():
    a, b = datetime(2023, 11, 1), datetime(2026, 11, 1)
    ch = BH.chunks(a, b, 85)
    assert ch[0][0] == a and ch[-1][1] == b
    assert all(x[1] == y[0] for x, y in zip(ch, ch[1:]))
    assert all((y - x).days <= 85 for x, y in ch)


def test_parse_dhan_and_kite_to_ist():
    ts = int(pd.Timestamp("2026-10-30 09:15", tz="Asia/Kolkata").timestamp())
    d = BH.parse_dhan({"open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5], "volume": [0], "timestamp": [ts]})
    assert d.index[0].hour == 9 and d.index[0].minute == 15
    k = BH.parse_kite({"data": {"candles": [["2026-10-30T09:15:00+0530", 1, 2, 0.5, 1.5, 0]]}})
    assert k.index[0] == d.index[0] and float(k["close"].iloc[0]) == 1.5


class FakeResp:
    def __init__(self, js, code=200):
        self._js, self.status_code = js, code

    def json(self):
        return self._js

    def raise_for_status(self):
        pass


class FakeSession:
    """Answers Dhan chart calls with one 09:15 bar per requested chunk; first call fails with 429."""
    def __init__(self):
        self.calls = 0

    def post(self, url, json, headers, timeout):
        self.calls += 1
        if self.calls == 1:
            return FakeResp({}, 429)
        t = pd.Timestamp(json["fromDate"]).normalize() + pd.Timedelta(hours=9, minutes=15)
        t = t.tz_localize("Asia/Kolkata")
        return FakeResp({"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [0],
                         "timestamp": [int(t.timestamp())]})


def test_download_retries_and_merges(monkeypatch, tmp_path):
    monkeypatch.setenv("DHAN_CLIENT_ID", "x")
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "y")
    monkeypatch.setattr(BH, "HIST", tmp_path)
    monkeypatch.setattr(BH.time, "sleep", lambda s: None)
    d = BH.Downloader("dhan", session=FakeSession(), pause=0)
    df = d.download("NIFTY", years=1, chunk_days=90)
    assert len(df) >= 4 and df.index.is_monotonic_increasing
    again = d.download("NIFTY", years=1, chunk_days=90)          # rerun merges, no duplicates
    assert len(again) == len(df) and not again.index.duplicated().any()
