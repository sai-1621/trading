r"""Live calibration logger for strategy 2 (expiry-day iron fly). Places no orders.

Full-day paper tracker (start any time before 09:40 on expiry day and leave it running until it prints results):
    .venv\Scripts\python scripts\log_expiry_chain.py --symbol NIFTY --loop                 (NSE feed, 60 s checks)
    .venv\Scripts\python scripts\log_expiry_chain.py --symbol NIFTY --loop --broker dhan   (Dhan first, NSE fallback)
  locks real-price iron flies at 09:45 and 10:15 (rule width + fixed widths), checks stops / profit-take / VIX every
  --interval seconds (default 60) at executable bid/ask, re-centres only on 5-minute closes, writes a summary row
  every 5 minutes (and a row whenever a fly acts) to logs/paper_fly_marks.csv, and one result row per fly to
  logs/paper_fly_results.csv (see paper_fly.py). Safe to restart: state is kept in logs/paper_fly_<SYMBOL>_<date>.json.

Single snapshot, run on expiry day at 09:45 and 10:15 IST (NIFTY: Tuesday, SENSEX: Thursday):
    .venv\Scripts\python scripts\log_expiry_chain.py --symbol NIFTY
    .venv\Scripts\python scripts\log_expiry_chain.py --symbol SENSEX --source dhan
    .venv\Scripts\python scripts\log_expiry_chain.py --symbol NIFTY --force      (test on a non-expiry day)

It fetches spot, India VIX and today's option chain, then for the ATM straddle and the wings (+/-150 and +/-200
for NIFTY, +/-500 and +/-700 for SENSEX, plus the strategy's own width) computes:
  atm_ratio        real ATM straddle / Black-Scholes straddle at IV = India VIX (trading time)
  credit_ratio     real iron-fly credit / BSM credit at flat VIX   (mid prices and executable bid/ask)
  credit_vs_smile  real credit / BSM credit with the 1.35x wing smile the backtest uses
  wing_skew        implied vol of the wings / implied vol of the ATM straddle (the real smile multiplier)
and appends one row per wing width to logs/expiry_chain_log.csv.
The backtest needs atm_ratio (and credit_vs_smile) to stay above the break-even in STRATEGIES.md.

Sources: nse (public website feed, NIFTY/BANKNIFTY only, no keys; can be blocked or change without notice;
cookies are refreshed with back-off on 401/403/429), dhan (DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN), angel (ANGEL_* keys),
kite (KITE_API_KEY / KITE_ACCESS_TOKEN). If the chosen source fails, every other source with keys is tried in turn.
"""
import argparse
import csv
import math
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
import greeks  # noqa: E402
from nse_calendar import IST, is_expiry_day, nearest_expiry, years_to_expiry  # noqa: E402

LOG = C.BASE_DIR / "logs" / "expiry_chain_log.csv"
WINGS = {"NIFTY": [150, 200], "SENSEX": [500, 700], "BANKNIFTY": [300, 500]}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36", "Accept": "application/json, text/plain, */*",
      "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/option-chain"}


# ------------------------------------------------------------------ sources -> (spot, {strike: {"CE": q, "PE": q}}, vix)
# q = {"ltp", "bid", "ask", "oi"}
class NSEClient:
    """One reused session for nseindia.com. On 401/403/429, a non-JSON reply or a network error it backs off,
    refreshes the cookies (reloads the option-chain page) and retries."""

    def __init__(self):
        self.s = None

    def _new(self):
        self.s = requests.Session()
        self.s.headers.update(UA)
        self.s.get("https://www.nseindia.com/option-chain", timeout=15)

    def get(self, url, params=None, tries=4):
        last = None
        for k in range(tries):
            try:
                if self.s is None:
                    self._new()
                r = self.s.get(url, params=params, timeout=20)
                if r.status_code in (401, 403, 429) or r.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {r.status_code}")
                return r.json()
            except (requests.RequestException, ValueError) as e:   # ValueError: not JSON (blocked page)
                last = e
                self.s = None                                          # new cookies next try
                time.sleep([3, 10, 25, 45][min(k, 3)])
        raise RuntimeError(f"NSE {url.rsplit('/', 1)[-1]}: {last}")


NSE = NSEClient()
_LAST_VIX = {"v": None}


def nse_indices(s=None) -> dict:
    """NSE allIndices rows by name (open, high, low, last, previousClose)."""
    return {r.get("index", "").upper(): r for r in NSE.get("https://www.nseindia.com/api/allIndices").get("data", [])}


def nse_vix(s=None) -> float:
    row = nse_indices().get("INDIA VIX")
    if not row:
        raise RuntimeError("India VIX not found in NSE allIndices")
    _LAST_VIX["v"] = float(row["last"])
    return _LAST_VIX["v"]


def vix_or_last() -> float | None:
    """India VIX from NSE; if NSE is blocked, the last value seen (VIX rules are skipped when unknown)."""
    try:
        return nse_vix()
    except Exception:  # noqa: BLE001
        return _LAST_VIX["v"]


def nse_chain(symbol: str, expiry: date):
    if symbol == "SENSEX":
        raise RuntimeError("SENSEX trades on BSE: NSE feed not available")
    exp_txt = expiry.strftime("%d-%b-%Y")
    try:  # newer NSE endpoint (needs the expiry)
        js = NSE.get("https://www.nseindia.com/api/option-chain-v3",
                     params={"type": "Indices", "symbol": symbol, "expiry": exp_txt}, tries=2)
        rows, spot = js["records"]["data"], js["records"].get("underlyingValue")
    except Exception:  # noqa: BLE001 - fall back to the older endpoint
        js = NSE.get("https://www.nseindia.com/api/option-chain-indices", params={"symbol": symbol})
        rows, spot = js["records"]["data"], js["records"].get("underlyingValue")
    chain = {}
    for r in rows:
        if r.get("expiryDate") and r["expiryDate"].upper() != exp_txt.upper():
            continue
        k = float(r["strikePrice"])
        for side in ("CE", "PE"):
            q = r.get(side)
            if q:
                # option-chain-v3 calls the best bid / offer buyPrice1 / sellPrice1; the older endpoint bidprice / askPrice
                chain.setdefault(k, {})[side] = {
                    "ltp": float(q.get("lastPrice") or 0),
                    "bid": float(q.get("buyPrice1") or q.get("bidprice") or 0),
                    "ask": float(q.get("sellPrice1") or q.get("askPrice") or 0),
                    "oi": float(q.get("openInterest") or 0)}
                spot = spot or q.get("underlyingValue")
    if not chain:
        raise RuntimeError(f"NSE returned no strikes for {exp_txt}")
    return float(spot), chain, vix_or_last()


DHAN_IDS = {"NIFTY": 13, "BANKNIFTY": 25, "SENSEX": 51}


def dhan_chain(symbol: str, expiry: date):
    h = {"access-token": os.getenv("DHAN_ACCESS_TOKEN", ""), "client-id": os.getenv("DHAN_CLIENT_ID", ""),
         "Content-Type": "application/json"}
    if not all(h.values()):
        raise RuntimeError("DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN not set")
    body = {"UnderlyingScrip": DHAN_IDS[symbol], "UnderlyingSeg": "IDX_I", "Expiry": expiry.isoformat()}
    r = requests.post("https://api.dhan.co/v2/optionchain", json=body, headers=h, timeout=20)
    r.raise_for_status()
    d = r.json().get("data") or {}
    chain = {}
    for k, v in (d.get("oc") or {}).items():
        for side, key in (("CE", "ce"), ("PE", "pe")):
            q = v.get(key)
            if q:
                chain.setdefault(float(k), {})[side] = {"ltp": float(q.get("last_price") or 0),
                                                        "bid": float(q.get("top_bid_price") or 0),
                                                        "ask": float(q.get("top_ask_price") or 0),
                                                        "oi": float(q.get("oi") or 0)}
    if not chain:
        raise RuntimeError("Dhan returned no strikes")
    return float(d.get("last_price")), chain, vix_or_last()


_ANGEL = {"b": None}


def angel_chain(symbol: str, expiry: date):
    from broker import AngelBroker
    if _ANGEL["b"] is None:
        _ANGEL["b"] = AngelBroker()          # one login for the day
    b = _ANGEL["b"]
    spot = b.index_ltp(symbol)
    rows = b.option_chain(symbol, expiry, spot, strikes_each_side=20)
    chain = {}
    for r in rows:
        chain.setdefault(r["strike"], {})["CE" if r["cp"] > 0 else "PE"] = {
            "ltp": r.get("ltp", 0.0), "bid": r.get("bid", 0.0), "ask": r.get("ask", 0.0), "oi": r.get("oi", 0.0)}
    try:
        vix = b.index_ltp("INDIAVIX")
        _LAST_VIX["v"] = vix
    except Exception:  # noqa: BLE001
        vix = vix_or_last()
    return spot, chain, vix


_KITE = {"day": None, "rows": []}
KITE_SPOT = {"NIFTY": "NSE:NIFTY 50", "BANKNIFTY": "NSE:NIFTY BANK", "SENSEX": "BSE:SENSEX"}


def kite_chain(symbol: str, expiry: date):
    key, tok = os.getenv("KITE_API_KEY", ""), os.getenv("KITE_ACCESS_TOKEN", "")
    if not (key and tok):
        raise RuntimeError("KITE_API_KEY / KITE_ACCESS_TOKEN not set")
    h = {"X-Kite-Version": "3", "Authorization": f"token {key}:{tok}"}
    seg = "BFO" if symbol == "SENSEX" else "NFO"
    if _KITE["day"] != date.today():            # instrument list once a day
        import io
        import pandas as pd
        txt = requests.get(f"https://api.kite.trade/instruments/{seg}", headers=h, timeout=60).text
        df = pd.read_csv(io.StringIO(txt))
        _KITE["rows"] = df[(df["name"] == symbol) & (df["instrument_type"].isin(["CE", "PE"]))].to_dict("records")
        _KITE["day"] = date.today()
    q = requests.get("https://api.kite.trade/quote", params={"i": [KITE_SPOT[symbol], "NSE:INDIA VIX"]},
                     headers=h, timeout=20).json()["data"]
    spot = float(q[KITE_SPOT[symbol]]["last_price"])
    vix = float(q["NSE:INDIA VIX"]["last_price"]) if "NSE:INDIA VIX" in q else vix_or_last()
    step = C.STRIKE_STEP[symbol]
    want = [r for r in _KITE["rows"] if str(r["expiry"])[:10] == expiry.isoformat() and abs(r["strike"] - spot) <= 20 * step]
    names = [f"{seg}:{r['tradingsymbol']}" for r in want]
    chain = {}
    for i in range(0, len(names), 200):
        data = requests.get("https://api.kite.trade/quote", params={"i": names[i:i + 200]}, headers=h, timeout=20).json()["data"]
        for r in want[i:i + 200]:
            d = data.get(f"{seg}:{r['tradingsymbol']}")
            if not d:
                continue
            dep = d.get("depth") or {}
            bid = (dep.get("buy") or [{}])[0].get("price") or 0
            ask = (dep.get("sell") or [{}])[0].get("price") or 0
            chain.setdefault(float(r["strike"]), {})[r["instrument_type"]] = {
                "ltp": float(d.get("last_price") or 0), "bid": float(bid), "ask": float(ask), "oi": float(d.get("oi") or 0)}
    if not chain:
        raise RuntimeError("Kite returned no strikes")
    return spot, chain, vix


SOURCES = {"nse": nse_chain, "dhan": dhan_chain, "angel": angel_chain, "kite": kite_chain}


def available_sources(symbol: str, primary: str) -> list[str]:
    """Primary first, then every other source that has keys in .env (NSE needs none, NIFTY/BANKNIFTY only)."""
    have = {"nse": symbol != "SENSEX",
            "dhan": bool(os.getenv("DHAN_CLIENT_ID") and os.getenv("DHAN_ACCESS_TOKEN")),
            "angel": bool(C.ANGEL_API_KEY and C.ANGEL_CLIENT_CODE and C.ANGEL_PIN and C.ANGEL_TOTP_SECRET),
            "kite": bool(os.getenv("KITE_API_KEY") and os.getenv("KITE_ACCESS_TOKEN"))}
    return [primary] + [k for k in ("dhan", "angel", "kite", "nse") if k != primary and have[k]]


def fetch_any(symbol: str, expiry: date, order: list[str]):
    """Try each source in turn; returns (spot, chain, vix, source_used)."""
    errs = []
    for name in order:
        try:
            spot, chain, vix = SOURCES[name](symbol, expiry)
            return spot, chain, vix, name
        except Exception as e:  # noqa: BLE001
            errs.append(f"{name}: {e}")
    raise RuntimeError(" | ".join(errs))


# ------------------------------------------------------------------ maths
def mid(q: dict) -> float:
    return (q["bid"] + q["ask"]) / 2 if q.get("bid", 0) > 0 and q.get("ask", 0) > 0 else q["ltp"]


def sell_px(q):   # what a seller gets now
    return q["bid"] if q.get("bid", 0) > 0 else q["ltp"]


def buy_px(q):    # what a buyer pays now
    return q["ask"] if q.get("ask", 0) > 0 else q["ltp"]


def analyse(symbol, spot, chain, vix, now, expiry, wing_mult=1.35):
    step = C.STRIKE_STEP[symbol]
    K = round(spot / step) * step
    if K not in chain or "CE" not in chain[K] or "PE" not in chain[K]:
        raise RuntimeError(f"ATM strike {K} missing from the chain")
    T = years_to_expiry(now, expiry)
    r = C.RISK_FREE
    iv = vix / 100
    ce, pe = chain[K]["CE"], chain[K]["PE"]
    straddle_mid = mid(ce) + mid(pe)
    straddle_bid = sell_px(ce) + sell_px(pe)
    bs = lambda k, cp, sig: float(greeks.price(spot, k, T, r, sig, cp))  # noqa: E731
    straddle_bs = bs(K, 1, iv) + bs(K, -1, iv)
    # implied ATM IV from the straddle (solve the call and put separately, average)
    ivc = float(greeks.implied_vol(mid(ce), spot, K, T, r, 1))
    ivp = float(greeks.implied_vol(mid(pe), spot, K, T, r, -1))
    atm_iv = (ivc + ivp) / 2 if not (math.isnan(ivc) or math.isnan(ivp)) else float("nan")
    rule_w = max(math.ceil(straddle_mid / step), 1) * step
    rows = []
    for W in sorted(set(WINGS.get(symbol, [3 * step, 4 * step]) + [rule_w])):
        if K + W not in chain or K - W not in chain or "CE" not in chain[K + W] or "PE" not in chain[K - W]:
            continue
        wc, wp = chain[K + W]["CE"], chain[K - W]["PE"]
        credit_mid = straddle_mid - mid(wc) - mid(wp)
        credit_exec = straddle_bid - buy_px(wc) - buy_px(wp)
        bs_flat = straddle_bs - bs(K + W, 1, iv) - bs(K - W, -1, iv)
        bs_smile = straddle_bs - bs(K + W, 1, iv * wing_mult) - bs(K - W, -1, iv * wing_mult)
        ivwc = float(greeks.implied_vol(mid(wc), spot, K + W, T, r, 1))
        ivwp = float(greeks.implied_vol(mid(wp), spot, K - W, T, r, -1))
        wing_iv = (ivwc + ivwp) / 2
        rows.append({
            "logged_at": now.strftime("%Y-%m-%d %H:%M:%S"), "symbol": symbol, "expiry": expiry.isoformat(),
            "expiry_day": is_expiry_day(symbol, now.date()), "spot": round(spot, 2), "india_vix": vix, "atm": K,
            "wing_width": W, "rule_width": W == rule_w, "minutes_left": round(T * 375 * 252, 1),
            "straddle_mid": round(straddle_mid, 2), "straddle_bid": round(straddle_bid, 2),
            "straddle_bsm_vix": round(straddle_bs, 2), "atm_ratio": round(straddle_mid / straddle_bs, 4),
            "atm_iv": round(atm_iv * 100, 2), "atm_iv_vs_vix": round(atm_iv * 100 / vix, 4),
            "wing_ce": round(mid(wc), 2), "wing_pe": round(mid(wp), 2),
            "credit_mid": round(credit_mid, 2), "credit_exec": round(credit_exec, 2),
            "credit_bsm_flat": round(bs_flat, 2), "credit_bsm_smile": round(bs_smile, 2),
            "credit_ratio_mid": round(credit_mid / bs_flat, 4), "credit_ratio_exec": round(credit_exec / bs_flat, 4),
            "credit_vs_smile_exec": round(credit_exec / bs_smile, 4),
            "wing_iv": round(wing_iv * 100, 2), "wing_skew": round(wing_iv / atm_iv, 4) if atm_iv == atm_iv else None,
            "atm_oi_ce": chain[K]["CE"].get("oi"), "atm_oi_pe": chain[K]["PE"].get("oi"),
        })
    return rows


def append(rows):
    LOG.parent.mkdir(exist_ok=True)
    new = not LOG.exists()
    with LOG.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        if new:
            w.writeheader()
        w.writerows(rows)


# ------------------------------------------------------------------ --loop: full-day real-price paper flies
MARKS = C.BASE_DIR / "logs" / "paper_fly_marks.csv"
RESULTS = C.BASE_DIR / "logs" / "paper_fly_results.csv"
LOCK_TIMES = ("09:45", "10:15")


def _csv_append(path: Path, rows: list[dict]):
    if not rows:
        return
    path.parent.mkdir(exist_ok=True)
    new = not path.exists()
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


def entry_filters(symbol, spot, vix, straddle, now, idx: dict | None) -> dict:
    """The strategy-2 regime filters, evaluated on live data where available ('unknown' otherwise)."""
    import pandas as pd
    notes, ok = [], True
    c = C.S2
    if vix > c["vix_max"]:
        ok = False; notes.append(f"VIX {vix:.2f} > {c['vix_max']}")
    name = {"NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK"}.get(symbol)
    row = (idx or {}).get(name or "")
    vrow = (idx or {}).get("INDIA VIX")
    if vrow and vrow.get("previousClose"):
        pv = float(vrow["previousClose"])
        if vix > pv * (1 + c["vix_jump_max_pct"] / 100):
            ok = False; notes.append(f"VIX {vix:.2f} more than {c['vix_jump_max_pct']}% above yesterday {pv:.2f}")
    else:
        notes.append("VIX jump: unknown")
    exp_move = spot * vix / 100 / math.sqrt(252)
    if row:
        gap = (float(row["open"]) / float(row["previousClose"]) - 1) * 100
        if abs(gap) > c["gap_max_pct"]:
            ok = False; notes.append(f"gap {gap:+.2f}%")
        elapsed = now.hour * 60 + now.minute - (9 * 60 + 15)
        rng = float(row["high"]) - float(row["low"])
        lim = c["or_max_expected_move"] * exp_move * math.sqrt(max(elapsed, 1) / 30)
        if rng > lim:
            ok = False; notes.append(f"open-to-entry range {rng:.0f} > {lim:.0f}")
    else:
        notes.append("gap / range: unknown")
    try:
        from data_yahoo import bars
        d = bars({"NIFTY": "^NSEI", "SENSEX": "^BSESN", "BANKNIFTY": "^NSEBANK"}[symbol], "1d")
        d = d[d.index < pd.Timestamp(now.date())]
        ref = float((d["close"] - d["open"]).abs().tail(10).mean())
        remaining = (15 * 60 + 30 - (now.hour * 60 + now.minute)) / 375
        need = c["min_premium_vs_realized"] * ref * math.sqrt(max(remaining, 0.05))
        if straddle < need:
            ok = False; notes.append(f"straddle {straddle:.1f} < {need:.1f} (recent moves)")
    except Exception as e:  # noqa: BLE001
        notes.append(f"premium filter: unknown ({e.__class__.__name__})")
    if now.date().isoformat() in c["event_days"]:
        ok = False; notes.append("event day")
    return {"pass": ok, "notes": notes}


class Session:
    """One expiry day of paper flies. poll() is called every --interval seconds; it is separate from the clock so
    tests can drive a whole day with made-up chains."""

    def __init__(self, symbol, lots, state, force=False):
        from paper_fly import PaperFly
        self.PaperFly = PaperFly
        self.symbol, self.lots, self.state = symbol, lots, state
        self.step = C.STRIKE_STEP[symbol]
        self.flies = [PaperFly.from_json(d) for d in state.get("flies", [])]
        self.force = force                       # test mode: allow locking outside market hours

    def rule_width(self, chain, K):
        st = mid(chain[K]["CE"]) + mid(chain[K]["PE"])
        return max(math.ceil(st / self.step), 1) * self.step

    def poll(self, now, spot, chain, vix, idx=None, source=""):
        """Returns (rows to log, lines to print). Rows: every fly at 5-minute marks, plus any fly that acts now."""
        m = now.hour * 60 + now.minute
        bar_close = m % 5 == 0 and not self.state.get("last_mark") == now.strftime("%H:%M")
        out_rows, lines = [], []
        for lt in LOCK_TIMES:
            h, mm = map(int, lt.split(":"))
            if lt in self.state["locked"] or m < h * 60 + mm or (m >= 15 * 60 and not self.force):
                continue
            if m > h * 60 + mm + 10 and not self.force:          # started late: never open a "09:45" fly at noon
                self.state["locked"].append(lt)
                self.state.setdefault("missed", []).append(lt)
                lines.append(f"{now:%H:%M:%S} MISSED {lt} entry (started too late; window is 10 minutes)")
                continue
            K = round(spot / self.step) * self.step
            if K not in chain or "CE" not in chain[K] or "PE" not in chain[K]:
                continue
            straddle = mid(chain[K]["CE"]) + mid(chain[K]["PE"])
            rw = self.rule_width(chain, K)
            filt = entry_filters(self.symbol, spot, vix or 0.0, straddle, now, idx)
            widths = sorted(set(WINGS.get(self.symbol, [3 * self.step]) + [rw]))
            for W in widths:
                f = self.PaperFly.open(f"{lt.replace(':', '')}_W{int(W)}{'R' if W == rw else ''}", self.symbol, chain,
                                       spot, K, W, now, vix or 0.0, self.lots, filt, rule_width=W == rw)
                if f:
                    f.flags.append(f"source {source}")
                    self.flies.append(f)
            self.state["locked"].append(lt)
            lines.append(f"{now:%H:%M:%S} LOCKED {lt}: K={K} widths {widths} "
                         f"filters {'PASS' if filt['pass'] else 'FAIL: ' + '; '.join(filt['notes'])}")
        for f in self.flies:
            if f.status != "open":
                if bar_close and f.shadow_close is None and m >= 15 * 60 + 15:
                    f.mark(now, spot, chain, vix, self.step, self.rule_width, bar_close=True)
                continue
            if f.entry_time == now.strftime("%H:%M"):
                continue                                   # opened this minute: first check next poll
            r = f.mark(now, spot, chain, vix, self.step, self.rule_width, bar_close=bar_close)
            acted = r.get("action") not in (None, "hold")
            if bar_close or acted:
                out_rows.append({"date": now.date().isoformat(), "time_s": now.strftime("%H:%M:%S"), **r,
                                 "vix": vix, "source": source})
            if acted:
                lines.append(f"{now:%H:%M:%S} {f.fid} {r['action'].upper()}: {r.get('reason', '')} "
                             f"(MTM Rs {r.get('mtm_rs_after_charges')})")
        if bar_close:
            self.state["last_mark"] = now.strftime("%H:%M")
            live = [f for f in self.flies if f.status == "open"]
            lines.append(f"{now:%H:%M} spot {spot:.2f} VIX {vix if vix is None else round(vix, 2)} [{source}] | " +
                         "  ".join(f"{r['fid']}: {r.get('mtm_rs_after_charges', '-')}" for r in out_rows
                                   if r.get("status") == "open") + (f"  ({len(live)} open)" if live else ""))
        self.state["flies"] = [f.to_json() for f in self.flies]
        return out_rows, lines

    def finished(self, now) -> bool:
        m = now.hour * 60 + now.minute
        live = any(f.status == "open" for f in self.flies)
        return (len(self.state["locked"]) == len(LOCK_TIMES) or m >= 15 * 60) and not live and \
            (m >= 15 * 60 + 15 or all(f.shadow_close is not None for f in self.flies) or not self.flies)

    def results(self, date_s, expiry):
        res = []
        for f in self.flies:
            T = years_to_expiry(datetime.strptime(f"{date_s} {f.entry_time}", "%Y-%m-%d %H:%M").replace(tzinfo=IST), expiry)
            iv = (f.vix_open or 0) / 100
            K0, W0 = f.shadow_strikes["sc"], f.shadow_strikes["lc"] - f.shadow_strikes["sc"]
            flat = None
            if iv > 0:
                bs = lambda k, cp: float(greeks.price(f.spot_in, k, T, C.RISK_FREE, iv, cp))  # noqa: E731
                flat = round(bs(K0, 1) + bs(K0, -1) - bs(K0 + W0, 1) - bs(K0 - W0, -1), 2)
            res.append(f.result_row(date_s, flat))
        return res


def run_loop(a):
    import json
    now = datetime.now(IST)
    exp_today = is_expiry_day(a.symbol, now.date())
    if not exp_today and not a.force:
        print(f"{now:%A %d %b}: not a {a.symbol} expiry day. Use --force to test on the next expiry.")
        return
    expiry = now.date() if exp_today else nearest_expiry(a.symbol, now.date())
    pre = "test_" if a.force else ""             # --force test runs never mix with real records
    marks_f, results_f = MARKS.with_name(pre + MARKS.name), RESULTS.with_name(pre + RESULTS.name)
    state_f = C.BASE_DIR / "logs" / f"{pre}paper_fly_{a.symbol}_{now.date():%Y%m%d}.json"
    state = json.loads(state_f.read_text()) if state_f.exists() else {"locked": [], "flies": [], "done": False}
    if state.get("done"):
        print(f"today's paper flies are already finished: {results_f}")
        return
    sess = Session(a.symbol, a.lots, state, force=a.force)
    order = available_sources(a.symbol, a.source)
    start, end = 9 * 60 + 40, 15 * 60 + 25
    print(f"paper iron flies {a.symbol}, expiry {expiry}, {a.lots} lot(s), checks every {a.interval}s, summary every "
          f"5 minutes, 09:40-15:25 IST, sources {order} (no orders). State: {state_f}", flush=True)
    fails = 0
    while True:
        now = datetime.now(IST)
        m = now.hour * 60 + now.minute
        if not a.force:
            if m < start:
                time.sleep(min(60, (start - m) * 60 - now.second + 5))
                continue
            if m > end:
                break
        try:
            spot, chain, vix, src = fetch_any(a.symbol, expiry, order)
            fails = 0
        except Exception as e:  # noqa: BLE001 - keep recording
            fails += 1
            print(f"{now:%H:%M:%S} all sources failed ({fails}): {e}", flush=True)
            time.sleep(min(a.interval, 30))
            continue
        idx = None
        if any(lt not in state["locked"] for lt in LOCK_TIMES):
            try:
                idx = nse_indices()
            except Exception:  # noqa: BLE001
                pass
        now = datetime.now(IST)
        rows, lines = sess.poll(now, spot, chain, vix, idx, src)
        _csv_append(marks_f, rows)
        for ln in lines:
            print(ln, flush=True)
        state_f.parent.mkdir(exist_ok=True)
        state_f.write_text(json.dumps(state, default=str))
        if a.force:
            # test mode outside market hours: lock, mark once more, then square off
            if all(f.status != "open" for f in sess.flies) and sess.flies:
                break
            time.sleep(15)
            continue
        if sess.finished(now):
            break
        n2 = datetime.now(IST)
        time.sleep(max(a.interval - n2.second % a.interval if a.interval <= 60 else a.interval, 1))
    res = sess.results(now.date().isoformat(), expiry)
    _csv_append(results_f, res)
    state["done"] = True
    state_f.write_text(json.dumps(state, default=str))
    print("\nResults (real prices, charges included):")
    for r in res:
        print(f"  {r['fid']:<12} credit {r['credit_exec_first']:>7} (ratio {r['credit_ratio']})  exit {r['exit_time']} "
              f"{r['exit_reason']:<45} net Rs {r['net_rs_after_charges']}  hold-to-15:15 gross Rs {r['hold_to_1515_gross_rs']}  "
              f"filters {'pass' if r['filters_pass'] else 'fail'}")
    print(f"appended to {results_f}")


def main():
    ap = argparse.ArgumentParser(description="log real expiry-day premiums vs the VIX model (no orders)")
    ap.add_argument("--symbol", default="NIFTY", choices=["NIFTY", "SENSEX", "BANKNIFTY"])
    ap.add_argument("--source", "--broker", dest="source", default="nse", choices=["nse", "dhan", "angel", "kite"],
                    help="primary quote source; others with keys in .env are used automatically if it fails")
    ap.add_argument("--force", action="store_true", help="run on a non-expiry day (uses the next expiry)")
    ap.add_argument("--repeat-min", type=float, default=0, help="log again every N minutes until 15:15")
    ap.add_argument("--loop", action="store_true", help="full-day real-price paper flies, 09:40-15:25 every 5 minutes")
    ap.add_argument("--lots", type=int, default=1)
    ap.add_argument("--interval", type=int, default=60, help="seconds between stop/mark checks in --loop (default 60)")
    a = ap.parse_args()
    if a.loop:
        return run_loop(a)
    while True:
        now = datetime.now(IST)
        exp_today = is_expiry_day(a.symbol, now.date())
        if not exp_today and not a.force:
            print(f"{now:%A %d %b}: not a {a.symbol} expiry day. Use --force to log the next expiry anyway.")
            return
        expiry = now.date() if exp_today else nearest_expiry(a.symbol, now.date())
        spot, chain, vix, _src = fetch_any(a.symbol, expiry, available_sources(a.symbol, a.source))
        rows = analyse(a.symbol, spot, chain, vix, now, expiry, C.S2.get("wing_iv_mult", 1.35))
        append(rows)
        print(f"\n{a.symbol} {now:%H:%M} IST  spot {spot:.2f}  India VIX {vix:.2f}  expiry {expiry}  "
              f"ATM {rows[0]['atm']}  straddle {rows[0]['straddle_mid']} (VIX model {rows[0]['straddle_bsm_vix']})")
        print(f"  atm_ratio {rows[0]['atm_ratio']}  ATM IV {rows[0]['atm_iv']}%  ({rows[0]['atm_iv_vs_vix']}x VIX)")
        for r in rows:
            tag = " <- strategy width" if r["rule_width"] else ""
            print(f"  wings +/-{r['wing_width']:<4} credit mid {r['credit_mid']:>8} exec {r['credit_exec']:>8} | "
                  f"BSM flat {r['credit_bsm_flat']:>8} smile {r['credit_bsm_smile']:>8} | "
                  f"ratio exec/flat {r['credit_ratio_exec']}  exec/smile {r['credit_vs_smile_exec']}  "
                  f"wing skew {r['wing_skew']}{tag}")
        print(f"  appended {len(rows)} rows to {LOG}")
        if not a.repeat_min or now.hour * 60 + now.minute >= 15 * 60 + 15:
            return
        time.sleep(a.repeat_min * 60)


if __name__ == "__main__":
    main()
