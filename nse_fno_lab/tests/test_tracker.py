"""Expiry-day tracker: NSE cookie refresh, source fallback, and a simulated day at 60-second polls."""
import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from nse_calendar import IST


def chain(spot, atm_prem=60.0, wing_prem=8.0, spread=0.5, K=25000, W=150):
    """Full strike ladder every 50 points around K (straight-line prices, enough to drive the rules)."""
    out = {}
    for k in range(K - 500, K + 501, 50):
        tv = max(atm_prem - abs(k - spot) * (atm_prem - wing_prem) / W, 0.5)
        ce, pe = max(spot - k, 0) + tv, max(k - spot, 0) + tv
        out[float(k)] = {"CE": {"ltp": ce, "bid": ce - spread / 2, "ask": ce + spread / 2, "oi": 0},
                         "PE": {"ltp": pe, "bid": pe - spread / 2, "ask": pe + spread / 2, "oi": 0}}
    return out

spec = importlib.util.spec_from_file_location("lec", Path(__file__).resolve().parents[1] / "scripts" / "log_expiry_chain.py")
lec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lec)


class Resp:
    def __init__(self, code, js=None):
        self.status_code, self._js = code, js

    def json(self):
        if self._js is None:
            raise ValueError("not json")
        return self._js


def test_nse_client_refreshes_cookies_after_403(monkeypatch):
    sessions = []

    class FakeSession:
        def __init__(self):
            self.calls = 0
            sessions.append(self)

        def get(self, url, params=None, timeout=None):
            self.calls += 1
            return Resp(403) if len(sessions) == 1 else Resp(200, {"ok": 1})

    c = lec.NSEClient()
    monkeypatch.setattr(c, "_new", lambda: setattr(c, "s", FakeSession()))
    monkeypatch.setattr(lec.time, "sleep", lambda s: None)
    assert c.get("https://www.nseindia.com/api/x") == {"ok": 1}
    assert len(sessions) == 2                      # a fresh session (new cookies) after the 403


def test_fetch_any_falls_back(monkeypatch):
    def bad(sym, exp):
        raise RuntimeError("blocked")

    monkeypatch.setitem(lec.SOURCES, "nse", bad)
    monkeypatch.setitem(lec.SOURCES, "dhan", lambda sym, exp: (25000.0, {25000.0: {}}, 13.0))
    spot, ch, vix, src = lec.fetch_any("NIFTY", None, ["nse", "dhan"])
    assert src == "dhan" and spot == 25000.0


def test_simulated_day_stop_between_marks(monkeypatch):
    monkeypatch.setattr(lec, "entry_filters", lambda *a, **k: {"pass": True, "notes": []})
    state = {"locked": [], "flies": [], "done": False}
    sess = lec.Session("NIFTY", 1, state)
    t = datetime(2026, 11, 3, 9, 40, 5, tzinfo=IST)
    rows_all, acted = [], {}
    while t.hour * 60 + t.minute <= 15 * 60 + 25:
        m = t.hour * 60 + t.minute
        spot = 25000.0
        if 11 * 60 + 2 <= m < 11 * 60 + 4:          # a fast spike between the 11:00 and 11:05 marks
            spot = 25400.0                          # beyond every wing: loss at its maximum
        c = chain(spot, atm_prem=max(60 - (m - 585) * 0.12, 5), wing_prem=max(8 - (m - 585) * 0.02, 0.5))
        c.update({k: v for k, v in chain(spot, K=25200).items() if k not in c})
        rows, lines = sess.poll(t, spot, c, 13.0, None, "test")
        rows_all += rows
        for r in rows:
            if r.get("action") in ("exit", "recenter"):
                acted.setdefault(r["fid"], []).append((r["time_s"], r["action"], r.get("reason")))
        if sess.finished(t):
            break
        t += timedelta(seconds=60)
    assert set(state["locked"]) == {"09:45", "10:15"}
    per_lock = {f.entry_time: set() for f in sess.flies}
    for f in sess.flies:
        per_lock[f.entry_time].add(f.W)
    assert set(per_lock) == {"09:45", "10:15"}
    assert all({150, 200} <= ws for ws in per_lock.values())    # fixed widths (+ the rule width when different)
    first = {fid: ev[0] for fid, ev in acted.items()}
    # the spike at 11:02 is caught by the 60-second poll, not at the 11:05 mark
    assert any(ts.startswith("11:02") or ts.startswith("11:03") for ts, _, _ in first.values())
    # re-centres only ever happen on 5-minute closes
    for ev in acted.values():
        for ts, action, _ in ev:
            if action == "recenter":
                assert int(ts[3:5]) % 5 == 0
    assert all(f.status == "closed" for f in sess.flies)
    res = sess.results("2026-11-03", datetime(2026, 11, 3).date())
    assert len(res) == len(sess.flies) and all(r["net_rs_after_charges"] is not None for r in res)


def test_late_start_does_not_open_stale_flies(monkeypatch):
    monkeypatch.setattr(lec, "entry_filters", lambda *a, **k: {"pass": True, "notes": []})
    state = {"locked": [], "flies": [], "done": False}
    sess = lec.Session("NIFTY", 1, state)
    t = datetime(2026, 11, 3, 12, 0, 5, tzinfo=IST)
    rows, lines = sess.poll(t, 25000.0, chain(25000.0), 13.0, None, "test")
    assert not sess.flies and set(state["missed"]) == {"09:45", "10:15"}


def test_nse_v3_bid_ask_fields(monkeypatch):
    """option-chain-v3 sends the best bid / offer as buyPrice1 / sellPrice1 (3 Nov 2026 every fill fell back to the
    last price because only the old names bidprice / askPrice were read)."""
    from datetime import date
    row = {"expiryDates": "09-Nov-2026", "strikePrice": 23800,
           "CE": {"lastPrice": 152.65, "buyPrice1": 152.45, "sellPrice1": 153.0, "openInterest": 1, "underlyingValue": 23810.85},
           "PE": {"lastPrice": 223.6, "buyPrice1": 223.6, "sellPrice1": 225.8, "openInterest": 1}}
    monkeypatch.setattr(lec.NSE, "get", lambda url, params=None, tries=4: {"records": {"data": [row], "underlyingValue": 23810.85}})
    monkeypatch.setattr(lec, "vix_or_last", lambda: 15.0)
    spot, ch, vix = lec.nse_chain("NIFTY", date(2026, 11, 9))
    assert ch[23800.0]["CE"]["bid"] == 152.45 and ch[23800.0]["CE"]["ask"] == 153.0
    assert ch[23800.0]["PE"]["bid"] == 223.6 and ch[23800.0]["PE"]["ask"] == 225.8
