"""Forward shadow test of the 1.5% bar, strongest first (PREREG_forward_1p5.md).

Reads every event the live PumpML bot scored (pumpml_events.jsonl, written by the strategy), picks trades under two
rules, and replays finished trades on 1-minute Binance futures candles: market entry at minute 2, 0.15% cost per
side, the bot's exits, real funding. 100 USDT per trade, at most 5 open, per rule.

    ..\\.venv\\Scripts\\python forward_shadow.py      -> forward_shadow.json
Read-only towards the bot; it never places or changes trades.
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ml_delay as D  # noqa: E402
from limit_entry_test import START, TAKER, hold  # noqa: E402

HERE = Path(__file__).resolve().parent
EVENTS = HERE / "pumpml_events.jsonl"
OUT = HERE / "forward_shadow.json"
RULES = {"B1.5 ranked (shadow)": (0.015, True), "B0.5 as they come (current rule)": (0.005, False)}
NEED_MIN = D.MINUTES + 5             # minutes after entry before the 1-minute file is complete


def load_events():
    if not EVENTS.exists():
        return pd.DataFrame()
    rows = [json.loads(x) for x in EVENTS.read_text(encoding="utf-8").splitlines() if x.strip()]
    ev = pd.DataFrame(rows).drop_duplicates(["pair", "candle"])
    ev["entry_time"] = pd.to_datetime(ev["entry_time"], utc=True)
    # drop events logged late (e.g. the last candle replayed on a bot restart): the bot would not have traded them
    late = pd.to_datetime(ev["logged_at"], utc=True) > ev["entry_time"] + pd.Timedelta(minutes=15)
    return ev[~late].sort_values("entry_time").reset_index(drop=True)


def choose(ev, bar):
    pl, ps = ev["pred_long"], ev["pred_short"]
    long_ok, short_ok = (pl >= bar) & (pl >= ps), (ps >= bar) & (ps > pl)
    a = ev[long_ok].assign(side="long", pred=pl[long_ok])
    b = ev[short_ok].assign(side="short", pred=ps[short_ok])
    return pd.concat([a, b])


def funding(pair, t0, t1, side):
    """Funding received (+) or paid (-) over (t0, t1], from Binance."""
    sym = pair.split("/")[0] + "USDT"
    r = requests.get("https://fapi.binance.com/fapi/v1/fundingRate", timeout=20,
                     params={"symbol": sym, "startTime": int(t0.timestamp() * 1000) + 1,
                             "endTime": int(t1.timestamp() * 1000), "limit": 1000})
    time.sleep(0.25)
    rate = sum(float(x["fundingRate"]) for x in r.json()) if r.status_code == 200 else 0.0
    return -rate if side == "long" else rate


_settled = {}


def settle(t, now):
    """(ret, exit_time) for a finished trade, None while it is still open, 'missing' without 1-minute data."""
    key = (t["pair"], str(t["entry_time"]), t["side"])
    if key in _settled:
        return _settled[key]
    if now < t["entry_time"] + pd.Timedelta(minutes=NEED_MIN):
        return None
    a = D.minutes(t["pair"], t["entry_time"])
    time.sleep(0.25)
    if a is None:
        _settled[key] = "missing"
        return "missing"
    ret, x = hold(a, t["side"], START, a[START, 1], TAKER)
    t_in = t["entry_time"] + pd.Timedelta(minutes=START)
    t_out = t["entry_time"] + pd.Timedelta(minutes=x + 1)
    _settled[key] = (ret + funding(t["pair"], t_in, t_out, t["side"]), t_out)
    return _settled[key]


def run_rule(ev, bar, ranked, now):
    c = choose(ev, bar)
    if c.empty:
        return {"signals": 0, "taken": 0}, []
    keys = ["entry_time", "pred"] if ranked else ["entry_time"]
    c = c.sort_values(keys, ascending=[True, False][:len(keys)], kind="mergesort")
    open_until, taken = [], []
    for _, t in c.iterrows():
        open_until = [x for x in open_until if x > t["entry_time"]]
        if len(open_until) >= 5:
            continue
        s = settle(t, now)
        if s == "missing":
            continue
        exit_t = s[1] if s else t["entry_time"] + pd.Timedelta(hours=24)
        open_until.append(exit_t)
        taken.append({"pair": t["pair"], "side": t["side"], "pred_pct": round(t["pred"] * 100, 2),
                      "entry_time": str(t["entry_time"]), "status": "closed" if s else "open",
                      "ret_pct": round(s[0] * 100, 3) if s else None, "exit_time": str(s[1]) if s else None})
    closed = [x["ret_pct"] / 100 for x in taken if x["status"] == "closed"]
    x = np.array(closed)
    sm = {"signals": int(len(c)), "taken": len(taken), "closed": len(closed),
          "open": sum(t["status"] == "open" for t in taken)}
    if len(x):
        sm |= {"win_pct": round(float((x > 0).mean() * 100), 1), "avg_pct": round(float(x.mean() * 100), 3),
               "pnl_usdt": round(float(x.sum() * 100), 2),
               "t": round(float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))), 2) if len(x) > 2 and x.std(ddof=1) > 0 else 0.0}
    return sm, taken


def main():
    now = pd.Timestamp(datetime.now(timezone.utc))
    ev = load_events()
    rep = {"prereg": "PREREG_forward_1p5.md", "updated": now.isoformat(), "events_logged": int(len(ev)),
           "first_event": str(ev["entry_time"].min()) if len(ev) else None, "rules": {}}
    for name, (bar, ranked) in RULES.items():
        sm, taken = run_rule(ev, bar, ranked, now) if len(ev) else ({"signals": 0, "taken": 0}, [])
        rep["rules"][name] = {"summary": sm, "trades": taken}
        print(f"{name:34} {json.dumps(sm)}")
    print(f"{len(ev)} events logged since {rep['first_event']}")
    OUT.write_text(json.dumps(rep, indent=1))
    print(f"saved {OUT.name}")


if __name__ == "__main__":
    main()
