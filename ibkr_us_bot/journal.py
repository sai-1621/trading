"""Trade journal (shared by the three bots).

signals.jsonl        one record per triggered signal: where it triggered, the plan (entry, stop, target),
                     the Time Machine's odds, the size, the risk verdict, and the decision (BOUGHT or SKIPPED + why)
shadow_trades.jsonl  every signal the bot turned DOWN after it had a valid plan is followed on paper anyway,
                     to its stop, target or flat-by time, so you can see what would have happened.
                     Shadow trades never touch the paper balance or the go-live record (paper_trades.jsonl).
"""
import json
from datetime import datetime, timedelta

import config

BAR = timedelta(minutes=5)


def _append(name, rec):
    with open(config.TRADER_DIR / name, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def signal(rec):
    _append("signals.jsonl", rec)


def open_shadow(state, key, plan, qty, reason, opened_at, day):
    sh = state.setdefault("shadows", {})
    if key in sh or qty <= 0:
        return
    sh[key] = {"key": key, "setup": plan.get("setup"), "entry": plan["entry"], "stop": plan["stop"],
               "target": plan["target"], "qty": qty, "skipped_because": reason, "opened_at": opened_at,
               "day": day, "last": plan["entry"]}


def settle_from_bars(bars, opened_at, stop, target, flat_min):
    """How a trade opened at `opened_at` ended on its own day, from that day's 5-minute bars (market time zone):
    the first bar touching the stop or the target (a bar touching both counts as the stop, as in the backtest),
    otherwise the last close before the flat-by time (minutes after midnight). Returns (exit_price, outcome), or
    None when there are no bars after the entry. Used when the bot was not running at the close."""
    t0 = datetime.fromisoformat(opened_at)
    use = sorted((b for b in bars if b["t"].date() == t0.date() and b["t"] + BAR > t0
                  and b["t"].hour * 60 + b["t"].minute + 5 <= flat_min), key=lambda b: b["t"])
    if not use:
        return None
    for b in use:
        if b["l"] <= stop:
            return stop, "stop"
        if b["h"] >= target:
            return target, "target"
    return use[-1]["c"], "flat by"


def check_shadows(state, price_of, is_flat, charges, now_iso, day_bars=None):
    """price_of(key) -> the price a sell would get now; is_flat(shadow) -> past the flat-by time;
    charges(shadow, exit_price) -> round-trip charges in money;
    day_bars(shadow) -> (5-minute bars covering the shadow's day, flat-by minute). A shadow from an earlier day
    (the bot was not running at that day's close) is settled from that day's bars, not at today's price."""
    today = now_iso[:10]
    for key, s in list(state.get("shadows", {}).items()):
        extra = {}
        if day_bars and s.get("day") and s["day"] < today:
            try:
                got = settle_from_bars(*_bars_args(day_bars, s))
            except Exception:
                continue  # no bars right now: try again on the next loop
            if got:
                px, outcome = got
                extra = {"settled": "from that day's 5-minute bars (the bot was not running at the close)"}
            else:
                px, outcome = None, None
        if not extra:
            try:
                px = price_of(key)
            except Exception:
                continue
            if px is None:
                continue
            outcome = "stop" if px <= s["stop"] else "target" if px >= s["target"] else "flat by" if is_flat(s) else None
            if outcome and s.get("day") and s["day"] < today:
                outcome, extra = "flat by", {"settled": "late, at the next price seen (no bars for that day)"}
        s["last"] = px
        if not outcome:
            continue
        risk = s["entry"] - s["stop"]
        pnl = s["qty"] * (px - s["entry"]) - charges(s, px)
        _append("shadow_trades.jsonl", {**s, "exit": px, "outcome": outcome, "pnl": round(pnl, 2),
                                        "r": round((px - s["entry"]) / risk, 2) if risk else 0, "closed_at": now_iso,
                                        **extra})
        del state["shadows"][key]


def _bars_args(day_bars, s):
    bars, flat_min = day_bars(s)
    return bars, s["opened_at"], s["stop"], s["target"], flat_min
