"""Forward head-to-head: old frozen model vs the live (retrained) model (PREREG_h2h.md).

Uses the events the live bot logs with both models' predictions, and forward_shadow.py's 1-minute replay.

    ..\\.venv\\Scripts\\python forward_h2h.py           -> forward_h2h.json
    ..\\.venv\\Scripts\\python forward_h2h.py --count   only count events and would-be trades (no replay)
Read-only towards the bot.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import forward_shadow as FS  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "forward_h2h.json"
BAR = 0.005
MODELS = {"live": ("pred_long", "pred_short"), "old": ("pred_long_old", "pred_short_old")}


def side(ev, cols, bar):
    pl, ps = ev[cols[0]], ev[cols[1]]
    return np.where((pl >= bar) & (pl >= ps), "long", np.where((ps >= bar) & (ps > pl), "short", ""))


def tstat(x):
    x = np.asarray(x, float)
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) if len(x) > 2 and x.std(ddof=1) > 0 else 0.0


def main():
    now = pd.Timestamp(datetime.now(timezone.utc))
    ev = FS.load_events()
    if len(ev) and "pred_long_old" in ev:
        ev = ev[ev["pred_long_old"].notna()].reset_index(drop=True)
    else:
        ev = ev.iloc[0:0]
    rep = {"prereg": "PREREG_h2h.md", "updated": now.isoformat(), "events": int(len(ev)),
           "first_event": str(ev["entry_time"].min()) if len(ev) else None}
    if len(ev):
        for m, cols in MODELS.items():
            ev["side_" + m] = side(ev, cols, BAR)
        either = ev[(ev["side_live"] != "") | (ev["side_old"] != "")]
        rep["either_trades"] = int(len(either))
        rep["trades_at_0.5"] = {m: int((ev["side_" + m] != "").sum()) for m in MODELS}
        rep["same_side_pct"] = round(float((either["side_live"] == either["side_old"]).mean() * 100), 1) if len(either) else None
    print(json.dumps({k: rep.get(k) for k in ("events", "first_event", "either_trades", "trades_at_0.5", "same_side_pct")}))
    if "--count" in sys.argv or not len(ev):
        return
    # primary: paired per event
    rows, diffs = [], []
    for _, e in either.iterrows():
        res = {}
        for m in MODELS:
            sd = e["side_" + m]
            if not sd:
                res[m] = 0.0
                continue
            s = FS.settle({"pair": e["pair"], "entry_time": e["entry_time"], "side": sd}, now)
            res[m] = s if s in (None, "missing") else s[0]
        if any(v is None or v == "missing" for v in res.values()):
            continue                                   # still open, or no 1-minute data
        diffs.append(res["old"] - res["live"])
        rows.append({"pair": e["pair"], "entry_time": str(e["entry_time"]), "live": e["side_live"], "old": e["side_old"],
                     "ret_live_pct": round(res["live"] * 100, 3), "ret_old_pct": round(res["old"] * 100, 3)})
    d = np.array(diffs)
    rep["paired"] = {"events_settled": len(d), "mean_diff_pct": round(float(d.mean() * 100), 3) if len(d) else None,
                     "t": round(tstat(d), 2), "sum_live_usdt": round(sum(r["ret_live_pct"] for r in rows), 2),
                     "sum_old_usdt": round(sum(r["ret_old_pct"] for r in rows), 2)}
    # secondary: portfolio rules per model
    rep["portfolio"] = {}
    for m, cols in MODELS.items():
        e2 = ev.rename(columns={cols[0]: "PL", cols[1]: "PS"}).drop(columns=["pred_long", "pred_short"], errors="ignore")
        e2 = e2.rename(columns={"PL": "pred_long", "PS": "pred_short"})
        for name, bar, ranked in (("0.5% first come", 0.005, False), ("1.5% ranked", 0.015, True)):
            sm, _ = FS.run_rule(e2, bar, ranked, now)
            rep["portfolio"][f"{m} {name}"] = sm
    rep["trades"] = rows
    print(json.dumps(rep["paired"]))
    for k, v in rep["portfolio"].items():
        print(f"{k:24} {json.dumps(v)}")
    OUT.write_text(json.dumps(rep, indent=1))
    print(f"saved {OUT.name}")


if __name__ == "__main__":
    main()
