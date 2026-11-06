"""Paper momentum swing strategy (delivery), as pre-registered in research/PREREG_swing_v2_momentum.md.

Rule: buy at the next open after a close above the previous 20 closes; sell at the next open after a close
below the previous 20 closes. Rs 1,00,000 paper account, at most 5 positions of Rs 20,000, highest 20-day
return first. Delivery charges and a 0.05% spread, the same as research/swing_v1.py.

Pure logic, no network: step(state, day, bars) processes one trading day. The bot calls it once per day after
the close; tests replay it over history.
"""
import math

N = 20
MIN_HISTORY = 200   # the backtest only bought stocks with 200+ days of history (its 200-day average had to exist)
SIZE = 20000.0
SLOTS = 5
CAPITAL = 100000.0
SPREAD = 0.0005


def delivery_charges(value, side):
    """Angel One delivery: brokerage min(Rs 20, 0.1%), STT 0.1% both sides, stamp 0.015% on buy, exchange and
    SEBI fees, 18% GST on brokerage and fees, DP Rs 15.93 per sell."""
    brokerage = min(20.0, value * 0.001)
    fees = value * (0.00297 + 0.0001) / 100
    stt = value * 0.001
    stamp = value * 0.00015 if side == "buy" else 0.0
    dp = 15.93 if side == "sell" else 0.0
    return brokerage + fees + stt + stamp + dp + 0.18 * (brokerage + fees)


def _est_cost(close):
    e = close * (1 + SPREAD / 2)
    spend = math.floor(SIZE / e) * e
    return spend + delivery_charges(spend, "buy")


def new_state(start_day):
    return {"start": str(start_day), "cash": CAPITAL, "positions": {}, "pending": [], "closed": [],
            "skipped": [], "equity": [], "last_day": None}


def _bar_on(bars, day):
    return next((b for b in reversed(bars) if b["t"] == day), None)


def _closes_to(bars, day):
    return [b["c"] for b in bars if b["t"] <= day]


def step(state, day, bars_by_sym, nifty_close=None):
    """bars_by_sym: {symbol: daily bars, oldest first, each {"t": date, "o","h","l","c"}} up to at least `day`.
    1) fill yesterday's orders at today's open, 2) value the account at today's close,
    3) queue orders for tomorrow's open from today's close. Returns a list of event strings."""
    ev = []
    sday = str(day)
    # 1) fills at today's open: sells first (they free slots and cash), then buys
    still = []
    for o in sorted(state["pending"], key=lambda o: o["side"] != "sell"):
        b = _bar_on(bars_by_sym.get(o["symbol"], []), day)
        if b is None:                       # no trading in that stock today: try again tomorrow
            still.append(o)
            continue
        sym = o["symbol"]
        if o["side"] == "sell":
            p = state["positions"].pop(sym)
            px = b["o"] * (1 - SPREAD / 2)
            proceeds = p["shares"] * px
            fee = delivery_charges(proceeds, "sell")
            state["cash"] += proceeds - fee
            pnl = proceeds - fee - p["cost"]
            state["closed"].append({"symbol": sym, "entry_day": p["entry_day"], "exit_day": sday,
                                    "entry": round(p["entry"], 2), "exit": round(px, 2), "shares": p["shares"],
                                    "pnl": round(pnl, 2), "ret_pct": round(pnl / p["cost"] * 100, 2)})
            ev.append(f"SELL {sym} {p['shares']} @ {px:.2f} pnl {pnl:+.0f}")
        else:
            e = b["o"] * (1 + SPREAD / 2)
            if sym in state["positions"]:
                continue
            shares = math.floor(SIZE / e)
            spend = shares * e
            fee = delivery_charges(spend, "buy")
            if shares < 1 or len(state["positions"]) >= SLOTS or state["cash"] < spend + fee:
                state["skipped"].append({"day": sday, "symbol": sym, "why": "price above Rs 20,000" if shares < 1
                                         else "no free slot or cash"})
                ev.append(f"SKIP buy {sym}")
                continue
            state["cash"] -= spend + fee
            state["positions"][sym] = {"shares": shares, "entry": e, "entry_day": sday, "cost": spend + fee}
            ev.append(f"BUY {sym} {shares} @ {e:.2f}")
    state["pending"] = still

    # 2) value at today's close
    value = state["cash"]
    for sym, p in state["positions"].items():
        cl = _closes_to(bars_by_sym.get(sym, []), day)
        p["last"] = cl[-1] if cl else p["entry"]
        value += p["shares"] * p["last"]
    state["equity"].append([sday, round(value, 2), nifty_close])

    # 3) tomorrow's orders from today's close
    pend_sell = {o["symbol"] for o in state["pending"] if o["side"] == "sell"}
    pend_buy = {o["symbol"] for o in state["pending"] if o["side"] == "buy"}
    for sym in state["positions"]:
        if sym in pend_sell or _bar_on(bars_by_sym.get(sym, []), day) is None:
            continue
        c = _closes_to(bars_by_sym[sym], day)
        if len(c) > N and c[-1] < min(c[-N - 1:-1]):
            state["pending"].append({"symbol": sym, "side": "sell", "signal_day": sday,
                                     "why": f"close {c[-1]:.2f} below the 20-day low {min(c[-N - 1:-1]):.2f}"})
            ev.append(f"signal SELL {sym}")
    cands = []
    for sym, bars in bars_by_sym.items():
        if sym in state["positions"] or sym in pend_buy or _bar_on(bars, day) is None:
            continue
        c = _closes_to(bars, day)
        if len(c) >= MIN_HISTORY and c[-1] > max(c[-N - 1:-1]):
            cands.append((c[-1] / c[-N - 1] - 1, sym, c[-1]))
    cands.sort(reverse=True)
    free = SLOTS - len(state["positions"]) - len(pend_buy)
    reserved = sum(o.get("est_cost", SIZE) for o in state["pending"] if o["side"] == "buy")
    for ret20, sym, close in cands:
        if free <= 0:
            break
        # as in the backtest: a new buy needs Rs 20,000 of cash left after the buys already queued (at their cost)
        if close > SIZE or state["cash"] - reserved < SIZE:
            continue
        est = _est_cost(close)
        state["pending"].append({"symbol": sym, "side": "buy", "signal_day": sday, "est_cost": round(est, 2),
                                 "why": f"close {close:.2f} above the 20-day high, 20-day return {ret20 * 100:.1f}%"})
        ev.append(f"signal BUY {sym} (20d {ret20 * 100:+.1f}%)")
        free -= 1
        reserved += est
    state["last_day"] = sday
    return ev
