"""Virtual paper wallet for the "1.5% bar, strongest first" rule, run inside the main PumpML bot.

Until 7 Nov 2026 this rule ran as a second freqtrade bot (PumpML15.py). That bot downloaded the same 522 coins and ran
the same model again, which cost about 1.5-1.8 GB of memory. Now PumpML hands its predictions to this book, and the
book keeps its own 1,000 USDT wallet in pumpml15_virtual.json. Nothing here places orders, even paper ones.

Rules (same as PumpML15):
  * enter when the better side's prediction is at least 1.5%, at most 5 open, 100 USDT each, strongest first;
  * exits as PumpML: long TP +8% / SL -8%, short TP +6% / SL -10% (price moves), out after 24 h;
  * protections: a side pauses 12 h after 3 stop-outs on it within 24 h; everything pauses 24 h after an 8% drawdown
    over the last 7 days (at least 5 trades); no new trade in a coin for 2 h after leaving it.
Prices: the bot polls each open coin's last price every 15 s (take profit, stop, time limit fill at that price, like
the freqtrade bot). Once an hour the closed 1-hour candles are also checked for stops that a quick wick hit between
polls; those fill at the stop price, as a stop order held on the exchange would. Fees 0.05% a side, funding added
from Binance's funding history when a trade closes.
"""
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

FEE = 0.0005
LONG_TP, LONG_SL = 0.08, 0.08
SHORT_TP, SHORT_SL = 0.06, 0.10
MAX_HOURS = 24
LATE_MIN = 15          # ignore a signal first seen more than 15 min after its candle closed (e.g. after a restart)


def _iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse(s):
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def funding_paid(pair, is_short, notional, t0, t1):
    """Funding received (+) or paid (-) between t0 and t1 from Binance's public history. 0 if it cannot be read."""
    try:
        sym = pair.split(":")[0].replace("/", "")
        url = (f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}"
               f"&startTime={int(t0.timestamp() * 1000)}&endTime={int(t1.timestamp() * 1000)}&limit=100")
        with urllib.request.urlopen(url, timeout=10) as r:
            rows = json.loads(r.read())
        rate = sum(float(x["fundingRate"]) for x in rows)
        return notional * rate * (1 if is_short else -1)
    except Exception:  # noqa: BLE001
        return 0.0


class VirtualBook:
    def __init__(self, path, min_pred=0.015, stake=100.0, max_open=5, wallet=1000.0, funding=funding_paid):
        self.path, self.min_pred, self.stake, self.max_open = path, min_pred, stake, max_open
        self.funding = funding
        self.s = {"wallet_start": wallet, "started": None, "next_id": 1, "trades": [], "locks": {}, "log": [],
                  "done_candles": []}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                self.s.update(json.load(fh))
        self.signals = {}          # candle iso -> {pair: (pred_long, pred_short, close)}
        self.last_poll = None
        self.last_candle_check = None

    # ---------- state ----------
    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.s, fh, indent=1)
        os.replace(tmp, self.path)

    def note(self, now, text):
        self.s["log"] = (self.s["log"] + [f"{_iso(now)} {text}"])[-200:]

    def open_trades(self):
        return [t for t in self.s["trades"] if t["is_open"]]

    def realized(self):
        return sum(t["close_profit_abs"] or 0 for t in self.s["trades"] if not t["is_open"])

    # ---------- signals from PumpML.populate_indicators ----------
    def offer(self, pair, candle, pred_long, pred_short, close):
        best = max(pred_long, pred_short)
        if best == best and best >= self.min_pred:
            self.signals.setdefault(_iso(candle), {})[pair] = (float(pred_long), float(pred_short), float(close))

    # ---------- protections ----------
    def _locked(self, now, pair, side):
        lk = self.s["locks"]
        for key in ("all", "side:" + side, "pair:" + pair):
            if key in lk and _parse(lk[key]) > now:
                return key
        return None

    def _lock(self, key, until, now, why):
        old = self.s["locks"].get(key)
        if old is None or _parse(old) < until:
            self.s["locks"][key] = _iso(until)
            self.note(now, f"PAUSE {key} until {_iso(until)} UTC: {why}")

    def _after_close(self, t, now):
        side = "short" if t["is_short"] else "long"
        self._lock("pair:" + t["pair"], now + timedelta(hours=2), now, "2 h cooldown after a trade")
        closed = [x for x in self.s["trades"] if not x["is_open"]]
        if t["exit_reason"] == "stop_loss" and t["close_profit_abs"] < 0:
            n = sum(1 for x in closed if x["exit_reason"] == "stop_loss" and x["close_profit_abs"] < 0
                    and bool(x["is_short"]) == bool(t["is_short"]) and _parse(x["close_date"]) > now - timedelta(hours=24))
            if n >= 3:
                self._lock("side:" + side, now + timedelta(hours=12), now, f"{n} {side} stop-outs in 24 h")
        week = sorted((x for x in closed if _parse(x["close_date"]) > now - timedelta(hours=168)),
                      key=lambda x: x["close_date"])
        if len(week) >= 5:
            before = sum(x["close_profit_abs"] for x in closed if _parse(x["close_date"]) <= now - timedelta(hours=168))
            eq = peak = self.s["wallet_start"] + before
            dd = 0.0
            for x in week:
                eq += x["close_profit_abs"]
                peak = max(peak, eq)
                dd = max(dd, (peak - eq) / peak if peak > 0 else 0)
            if dd >= 0.08:
                self._lock("all", now + timedelta(hours=24), now, f"drawdown {dd * 100:.1f}% in 7 days")

    # ---------- trades ----------
    def _close(self, t, price, reason, now):
        sign = -1 if t["is_short"] else 1
        fee_close = price * t["amount"] * FEE
        fund = self.funding(t["pair"], t["is_short"], t["open_rate"] * t["amount"], _parse(t["open_date"]), now)
        pnl = sign * (price - t["open_rate"]) * t["amount"] - t["fee_open_cost"] - fee_close + fund
        t.update(is_open=0, close_rate=price, close_date=_iso(now), exit_reason=reason, fee_close_cost=fee_close,
                 funding_fees=fund, close_profit_abs=round(pnl, 6), close_profit=pnl / t["stake_amount"])
        self.note(now, f"EXIT {t['pair']} {'short' if t['is_short'] else 'long'} {reason} at {price:g}: "
                       f"{pnl:+.2f} USDT")
        self._after_close(t, now)

    def _levels(self, t):
        if t["is_short"]:
            return t["open_rate"] * (1 - SHORT_TP), t["open_rate"] * (1 + SHORT_SL)
        return t["open_rate"] * (1 + LONG_TP), t["open_rate"] * (1 - LONG_SL)

    def check_price(self, t, price, now):
        """Exit at the polled price, like PumpML's custom_exit / stop (checked in that order by freqtrade: stop first)."""
        tp, sl = self._levels(t)
        if (t["is_short"] and price >= sl) or (not t["is_short"] and price <= sl):
            self._close(t, price, "stop_loss", now)
        elif (t["is_short"] and price <= tp) or (not t["is_short"] and price >= tp):
            self._close(t, price, "take_profit", now)
        elif now - _parse(t["open_date"]) >= timedelta(hours=MAX_HOURS):
            self._close(t, price, "time_limit", now)

    def check_candle(self, t, start, high, low, now):
        """A closed 1 h candle that began after entry: a wick through the stop fills at the stop price."""
        if not t["is_open"] or start < _parse(t["open_date"]).replace(minute=0, second=0, microsecond=0):
            return
        _, sl = self._levels(t)
        if (t["is_short"] and high >= sl) or (not t["is_short"] and low <= sl):
            self._close(t, sl, "stop_loss", now)

    def enter(self, now, latest_candle, price_of):
        """Open trades for the latest closed candle's signals: strongest first, protections and free slots allowing."""
        key = _iso(latest_candle)
        sigs = self.signals.get(key)
        for k in [k for k in self.signals if k <= key]:
            self.signals.pop(k, None)
        if not sigs or key in self.s["done_candles"]:
            return
        self.s["done_candles"] = (self.s["done_candles"] + [key])[-48:]
        if now - (latest_candle + timedelta(hours=1)) > timedelta(minutes=LATE_MIN):
            self.note(now, f"skip {len(sigs)} signal(s) of candle {key}: seen too late (restart?)")
            return
        open_pairs = {t["pair"] for t in self.open_trades()}
        cands = sorted(((p, pl, ps, c) for p, (pl, ps, c) in sigs.items() if p not in open_pairs),
                       key=lambda x: -max(x[1], x[2]))
        for pair, pl, ps, _ in cands:
            short = ps > pl
            side, pred = ("short", ps) if short else ("long", pl)
            if len(self.open_trades()) >= self.max_open:
                self.note(now, f"no slot for {pair} {side} ({pred * 100:.2f}%)")
                continue
            cash = self.s["wallet_start"] + self.realized() - sum(t["stake_amount"] for t in self.open_trades())
            if cash < self.stake:
                self.note(now, f"no cash for {pair}")
                continue
            lk = self._locked(now, pair, side)
            if lk:
                self.note(now, f"skip {pair} {side} ({pred * 100:.2f}%): paused ({lk})")
                continue
            price = price_of(pair)
            if not price:
                self.note(now, f"skip {pair}: no price")
                continue
            amount = self.stake / price
            t = {"id": self.s["next_id"], "pair": pair, "is_open": 1, "open_rate": price, "close_rate": None,
                 "amount": amount, "stake_amount": self.stake, "open_date": _iso(now), "close_date": None,
                 "close_profit": None, "close_profit_abs": None, "exit_reason": None,
                 "stop_loss": self._levels({"is_short": short, "open_rate": price})[1],
                 "enter_tag": "ml_short" if short else "ml_long", "fee_open_cost": self.stake * FEE,
                 "fee_close_cost": None, "is_short": int(short), "funding_fees": 0.0, "pred": pred}
            self.s["next_id"] += 1
            self.s["trades"].append(t)
            self.note(now, f"ENTER {pair} {side} at {price:g} (prediction {pred * 100:.2f}%)")
