"""Step 1 of the stock long/short + ML test: collect every opening-range breakout and breakdown.

    ..\\angelone_bot\\.venv\\Scripts\\python stocks_candidates.py --market nse
    ..\\ibkr_us_bot\\.venv\\Scripts\\python  stocks_candidates.py --market us

Hourly bars (~2.7 years, Yahoo), 100 stocks from the bot's own scanner. For each stock and full session:
  long : first hourly close ABOVE the first hour's high  -> buy;  stop = middle of the first hour, target 2R
  short: first hourly close BELOW the first hour's low   -> short; stop = middle of the first hour, target 2R
  both : entry window as the bots (NSE trigger by 14:30, US by 15:30), out at the last bar of the day.
Entries pay half the assumed spread; charges come from the bot's own cost model (lab.Stocks.cost_r).
Each candidate also gets features known at the moment of entry (no later data). Saved to
results/candidates_<market>.json for stocks_ml.py.
"""
import argparse
import json
import math
import sys
import time
from collections import defaultdict
from datetime import datetime

from filters import ENTRY_TO, FULL_BARS, INDEX, hourly, index_regime, strength
from lab import HERE, Stocks

TARGET_R = 2.0


def simulate(bs, i, side, sf):
    """Trade triggered by bar i's close. Returns (r, entry, risk, exit) or None."""
    hi, lo = bs[0]["h"], bs[0]["l"]
    mid = (hi + lo) / 2
    c = bs[i]["c"]
    if side == "long":
        e = c * (1 + sf / 2)
        stop, risk = mid, c * (1 + sf / 2) - mid
    else:
        e = c * (1 - sf / 2)
        stop, risk = mid, mid - c * (1 - sf / 2)
    if risk <= 0 or risk < 2 * sf * e:
        return None
    tgt = e + TARGET_R * risk if side == "long" else e - TARGET_R * risk
    px = bs[-1]["c"] * ((1 - sf / 2) if side == "long" else (1 + sf / 2))
    for b in bs[i + 1:]:
        if side == "long":
            if b["o"] <= stop:
                px = b["o"]; break
            if b["l"] <= stop:
                px = stop; break
            if b["h"] >= tgt:
                px = tgt; break
        else:
            if b["o"] >= stop:
                px = b["o"]; break
            if b["h"] >= stop:
                px = stop; break
            if b["l"] <= tgt:
                px = tgt; break
    r = (px - e) / risk if side == "long" else (e - px) / risk
    return r, e, risk, px


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True, choices=list(INDEX))
    ap.add_argument("--stocks", type=int, default=100)
    a = ap.parse_args()
    t0 = time.time()
    mk = Stocks(a.market, a.stocks, False)
    import data
    idx_d1 = data.yahoo_bars(INDEX[a.market], "1d", "5y")
    regime, idx_close = index_regime(idx_d1)
    idx_days = sorted(idx_close)
    idx_ret = {d: idx_close[d] / idx_close[idx_days[k - 1]] - 1 for k, d in enumerate(idx_days) if k}
    syms = sorted({s["sym"] for s in mk.sessions})
    today = mk.market.now().date()
    sf = mk.config.ASSUMED_SPREAD_PCT / 100
    out = []
    for n_sym, sym in enumerate(syms, 1):
        sys.stdout.write(f"\r  {n_sym}/{len(syms)} {sym:<14} candidates {len(out)}")
        sys.stdout.flush()
        try:
            h1 = hourly(mk, data, sym, False)
        except Exception as ex:
            print(f"\n  {sym} skipped: {ex}")
            continue
        days = defaultdict(list)
        for b in h1:
            if None not in (b.get("o"), b.get("h"), b.get("l"), b.get("c")):
                days[b["t"].date()].append(b)
        sess = []
        for d in sorted(days):
            bs = sorted(days[d], key=lambda b: b["t"])
            if d < today and len(bs) == FULL_BARS:
                sess.append((d, bs))
        closes = [(d, bs[-1]["c"]) for d, bs in sess]
        st = strength(closes, idx_close)
        first_vol = [(bs[0].get("v") or 0) for _, bs in sess]
        for k, (d, bs) in enumerate(sess):
            g, reg = st.get(d), regime.get(d)
            if g is None or reg is None or k < 21:
                continue
            prev_c = sess[k - 1][1][-1]["c"]
            dc = [x for _, x in closes[k - 21:k]]
            rets = [dc[j] / dc[j - 1] - 1 for j in range(1, len(dc))]
            vola20 = (sum(r * r for r in rets) / len(rets)) ** 0.5
            fv_avg = sum(first_vol[k - 20:k]) / 20
            o0, h0, l0, c0 = bs[0]["o"], bs[0]["h"], bs[0]["l"], bs[0]["c"]
            last_entry = max(j for j, b in enumerate(bs) if b["t"].hour * 60 + b["t"].minute + 60 <= ENTRY_TO[a.market])
            prev_idx_days = [x for x in idx_days if x < d]
            ix1 = idx_ret.get(prev_idx_days[-1]) if prev_idx_days else None
            ix5 = (idx_close[prev_idx_days[-1]] / idx_close[prev_idx_days[-6]] - 1) if len(prev_idx_days) > 6 else None
            base = {"sym": sym, "day": d.isoformat(),
                    "gap": o0 / prev_c - 1, "or_pct": (h0 - l0) / o0, "first_ret": c0 / o0 - 1,
                    "first_closepos": (c0 - l0) / (h0 - l0) if h0 > l0 else 0.5,
                    "first_vol_x": (bs[0].get("v") or 0) / fv_avg if fv_avg else None,
                    "prev_ret": prev_c / sess[k - 2][1][-1]["c"] - 1, "ret5": prev_c / closes[k - 6][1] - 1,
                    "vola20": vola20, "rs21": g["rs21"], "rs63": g["rs63"], "trend20": int(g["trend"]),
                    "idx_up50": int(reg["up50"]), "idx_up200": int(reg["up200"]), "idx_ret1": ix1, "idx_ret5": ix5,
                    "log_price": math.log10(o0)}
            for side in ("long", "short"):
                for i in range(1, last_entry + 1):
                    c = bs[i]["c"]
                    if (side == "long" and c > h0) or (side == "short" and c < l0):
                        res = simulate(bs, i, side, sf)
                        if res is None:
                            break
                        r, e, risk, px = res
                        r_net = r - mk.cost_r({"sf": sf}, e, risk, px)
                        out.append({**base, "side": side, "trigger_bar": i, "risk_pct": risk / e,
                                    "trigger_move": c / bs[i]["o"] - 1, "r": round(r, 4), "r_net": round(r_net, 4)})
                        break
    print()
    f = HERE / "results" / f"candidates_{a.market}.json"
    f.write_text(json.dumps({"market": a.market, "run_at": datetime.now().isoformat(timespec="seconds"),
                             "stocks": len(syms), "target_r": TARGET_R, "candidates": out}))
    n_l = sum(c["side"] == "long" for c in out)
    print(f"  {len(out)} candidates ({n_l} long, {len(out) - n_l} short) -> {f.name} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
