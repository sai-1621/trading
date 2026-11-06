"""Daily ETF prices from Yahoo Finance (free, unofficial): split- and dividend-adjusted closes."""
import json
import time
from datetime import datetime, timezone

import requests

import config
from market import IST

_s = requests.Session()
_s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})


def daily(symbol, start_year=2015, max_age_h=12):
    """[(date, open, close)] for an NSE symbol (or ^NSEI), closed sessions only, cached."""
    ysym = symbol if symbol.startswith("^") else f"{symbol}.NS"
    f = config.CACHE_DIR / f"{ysym.replace('^', '_')}.json"
    if f.exists() and time.time() - f.stat().st_mtime < max_age_h * 3600:
        rows = json.loads(f.read_text())
    else:
        p1 = int(datetime(start_year, 1, 1, tzinfo=timezone.utc).timestamp())
        for attempt in range(4):
            r = _s.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(ysym)}",
                       params={"interval": "1d", "period1": p1, "period2": int(time.time())}, timeout=30)
            if r.status_code == 429:
                time.sleep(3 + 5 * attempt)
                continue
            r.raise_for_status()
            break
        res = r.json()["chart"]["result"][0]
        q, adj = res["indicators"]["quote"][0], res["indicators"]["adjclose"][0]["adjclose"]
        rows = []
        for i, t in enumerate(res.get("timestamp") or []):
            o, c, a = q["open"][i], q["close"][i], adj[i]
            if None in (o, c, a) or c <= 0:
                continue
            rows.append([datetime.fromtimestamp(t, IST).date().isoformat(), o * a / c, a])
        f.write_text(json.dumps(rows))
    today = datetime.now(IST).date().isoformat()
    from datetime import date
    return clean([(date.fromisoformat(d), o, c) for d, o, c in rows if d < today])


def clean(rows):
    """Yahoo sometimes shows a few days around an ETF unit split at the unsplit price (a 90% 'fall' that reverses
    days later), and some opening prices far from any real trade. Drop such stretches; replace bad opens with the close."""
    out = []
    i = 0
    while i < len(rows):
        if out:
            prev = out[-1][2]
            jump = rows[i][2] / prev
            if jump < 0.6 or jump > 1.67:
                # does the price come back near the previous close within 5 sessions? then those days are bad data
                back = next((j for j in range(i + 1, min(i + 6, len(rows))) if 0.8 < rows[j][2] / prev < 1.25), None)
                if back is not None:
                    i = back
                    continue
        d, o, c = rows[i]
        ref = out[-1][2] if out else c
        if not (0.92 < o / ref < 1.08) and 0.92 < c / ref < 1.08:
            o = c
        out.append((d, o, c))
        i += 1
    return out


def last_price(symbol):
    r = _s.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.NS", params={"interval": "1m", "range": "1d"}, timeout=15)
    r.raise_for_status()
    return float(r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"])
