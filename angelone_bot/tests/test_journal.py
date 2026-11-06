"""Settling a trade left open from a day when the bot was not running at the close (journal.py, shared with the
IBKR bot)."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import journal  # noqa: E402

TZ = timezone(timedelta(hours=-4))
FLAT = 15 * 60 + 55


def bars(day, start="09:30", rows=()):
    """rows: (o, h, l, c) per 5-minute bar from `start`."""
    t = datetime.fromisoformat(f"{day}T{start}:00").replace(tzinfo=TZ)
    return [{"t": t + timedelta(minutes=5 * i), "o": o, "h": h, "l": l, "c": c, "v": 1} for i, (o, h, l, c) in enumerate(rows)]


def test_stop_first_and_bars_before_entry_ignored():
    b = bars("2026-10-28", "09:45", [(100, 90, 80, 100),      # 09:45 bar ends at entry: ignored even though it is far below
                                     (100, 101, 99, 100),     # 09:50
                                     (100, 106, 97, 100)])    # 09:55 touches both: counts as the stop
    assert journal.settle_from_bars(b, "2026-10-28T09:50:10-04:00", 98, 105, FLAT) == (98, "stop")


def test_target_then_flat_by_uses_last_close_before_flat():
    b = bars("2026-10-28", "15:45", [(100, 102, 99, 101), (101, 103, 100, 102.5), (102, 110, 101, 109)])
    # entry 15:45:05; bars 15:45 and 15:50 count, the 15:55 bar is after the flat-by time
    assert journal.settle_from_bars(b, "2026-10-28T15:45:05-04:00", 95, 120, FLAT) == (102.5, "flat by")
    assert journal.settle_from_bars(b, "2026-10-28T15:45:05-04:00", 95, 102.8, FLAT) == (102.8, "target")


def test_no_bars_after_entry():
    assert journal.settle_from_bars(bars("2026-10-27", rows=[(1, 1, 1, 1)]), "2026-10-28T10:00:00-04:00", 1, 2, FLAT) is None


def test_stale_shadow_settled_from_its_day_not_todays_price(tmp_path, monkeypatch):
    monkeypatch.setattr(journal.config, "TRADER_DIR", tmp_path, raising=False)
    state = {"shadows": {"LLY": {"key": "LLY", "setup": "orb", "entry": 100.0, "stop": 98.0, "target": 104.0, "qty": 10,
                                 "skipped_because": "x", "opened_at": "2026-10-28T10:00:20-04:00", "day": "2026-10-28",
                                 "last": 100.0}}}
    day = bars("2026-10-28", "10:00", [(100, 101, 99.5, 100.5)] * 70)  # drifts, never touches 98 or 104
    journal.check_shadows(state, lambda k: 111.0,                       # next morning's price gapped far above target
                          lambda s: True, lambda s, px: 2.0, "2026-10-29T09:54:00-04:00",
                          lambda s: (day, FLAT))
    rec = json.loads((tmp_path / "shadow_trades.jsonl").read_text().strip())
    assert rec["outcome"] == "flat by" and rec["exit"] == 100.5 and rec["r"] == 0.25 and rec["pnl"] == 3.0
    assert "settled" in rec and state["shadows"] == {}


def test_todays_shadow_still_uses_live_price(tmp_path, monkeypatch):
    monkeypatch.setattr(journal.config, "TRADER_DIR", tmp_path, raising=False)
    state = {"shadows": {"KO": {"key": "KO", "entry": 100.0, "stop": 98.0, "target": 104.0, "qty": 1,
                                "opened_at": "2026-10-29T10:00:00-04:00", "day": "2026-10-29", "last": 100.0}}}
    called = []
    journal.check_shadows(state, lambda k: 104.5, lambda s: False, lambda s, px: 0.0, "2026-10-29T11:00:00-04:00",
                          lambda s: called.append(1))
    rec = json.loads((tmp_path / "shadow_trades.jsonl").read_text().strip())
    assert rec["outcome"] == "target" and rec["exit"] == 104.5 and "settled" not in rec and not called
