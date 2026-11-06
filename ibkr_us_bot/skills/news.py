"""News Monitor (trader-news), for US stocks, like the original pack: Yahoo Finance headlines per ticker, each
company's 8-K filings on SEC EDGAR, and Nasdaq's earnings calendar. Headlines are tagged for information;
event risk (see check()) means no new day trade in that stock today.

SEC EDGAR asks every program to identify itself: put "Your Name you@example.com" in SEC_USER_AGENT.
Without it, the 8-K check is skipped and only headlines are used.
"""
import logging
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import requests

import config
from market import ET as NY, now
from skills import write_json

log = logging.getLogger("bot")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

TAGS = {
    "earnings": r"\b(q[1-4]|quarter(ly)?)\b.*\b(results?|earnings|eps|revenue)\b|\bearnings\b.*\b(today|tonight|after the bell|before the bell|beat|miss|preview)\b|reports? earnings",
    "analyst": r"upgrade|downgrade|price target|initiat|reiterat|overweight|underweight|outperform",
    "deal": r"acqui|merger|takeover|buyout|to buy\b|stake|spin-?off",
    "halt": r"\bhalt",
    "regulatory": r"\bsec\b|\bftc\b|\bdoj\b|antitrust|probe|lawsuit|investigation|recall|\bfda\b",
    "management": r"\bceo\b|\bcfo\b|resign|steps down|appoint",
    "macro": r"\bfed\b|inflation|\bcpi\b|jobs report|tariff|rate cut|rate hike",
}
EVENT_RISK = {"halt"}   # earnings risk comes from the earnings calendar below, not from headlines
EIGHT_K_ITEMS = {"2.02": "earnings", "1.01": "deal", "2.01": "deal", "5.02": "management", "1.03": "bankruptcy",
                 "4.02": "restatement", "3.01": "delisting"}


def headlines(symbol):
    r = requests.get("https://feeds.finance.yahoo.com/rss/2.0/headline",
                     params={"s": symbol.replace(".", "-"), "region": "US", "lang": "en-US"}, headers=UA, timeout=15)
    r.raise_for_status()
    out = []
    for it in ET.fromstring(r.content).iter("item"):
        title = (it.findtext("title") or "").strip()
        try:
            when = parsedate_to_datetime(it.findtext("pubDate")).astimezone(NY)
        except (TypeError, ValueError):
            continue
        out.append({"time": when.isoformat(), "title": title, "tags": [k for k, p in TAGS.items() if re.search(p, title, re.I)]})
    return sorted(out, key=lambda x: x["time"], reverse=True)


def filings_8k(cik, since):
    if not (config.SEC_USER_AGENT and cik):
        return []
    r = requests.get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json",
                     headers={"User-Agent": config.SEC_USER_AGENT}, timeout=15)
    r.raise_for_status()
    rec = r.json()["filings"]["recent"]
    out = []
    for form, date, items in zip(rec["form"], rec["filingDate"], rec.get("items", [""] * len(rec["form"]))):
        if form == "8-K" and date >= since:
            tags = sorted({EIGHT_K_ITEMS[i.strip()] for i in items.split(",") if i.strip() in EIGHT_K_ITEMS})
            out.append({"date": date, "items": items, "tags": tags})
    return out


def earnings_calendar(day):
    """{symbol: "time-pre-market" | "time-after-hours" | "time-not-supplied"} from Nasdaq's public calendar."""
    try:
        r = requests.get("https://api.nasdaq.com/api/calendar/earnings", params={"date": day},
                         headers={**UA, "Accept": "application/json"}, timeout=20)
        r.raise_for_status()
        rows = ((r.json().get("data") or {}).get("rows")) or []
        return {row["symbol"]: row.get("time", "time-not-supplied") for row in rows}
    except Exception as e:
        log.warning("Earnings calendar for %s unavailable (%s)", day, e)
        return None


def check(watchlist):
    """Event risk (no new day trade today): the company reports before today's open (or at an unknown time),
    reported after yesterday's close, has a trading halt in the headlines, or filed an earnings 8-K today.
    A report after today's close is fine: the bot is flat by 15:55."""
    import market
    t = now()
    today = t.date().isoformat()
    prev = t.date()
    while True:
        prev = prev.fromordinal(prev.toordinal() - 1)
        if market.is_trading_day(prev):
            break
    cal_today, cal_prev = earnings_calendar(today), earnings_calendar(prev.isoformat())
    out = {}
    for w in watchlist:
        sym = w["symbol"]
        try:
            items = [i for i in headlines(sym) if i["time"][:10] == today]
            key = re.escape(sym) + "|" + re.escape(w["name"].split(" ")[0])
            risk = {tag for i in items if re.search(rf"\b({key})\b", i["title"], re.I) for tag in i["tags"] if tag in EVENT_RISK}
            when_today = (cal_today or {}).get(sym)
            if when_today and when_today != "time-after-hours":
                risk.add("earnings today" + (" before the open" if when_today == "time-pre-market" else " (time unknown)"))
            if (cal_prev or {}).get(sym) == "time-after-hours":
                risk.add("earnings after yesterday's close")
            k8 = filings_8k(w.get("cik"), today)
            if any("earnings" in f["tags"] for f in k8):
                risk.add("earnings 8-K filed today")
            out[sym] = {"count": len(items), "event_risk": sorted(risk), "reports_today": when_today,
                        "latest": items[:5], "8k_today": k8}
        except Exception as e:
            log.warning("News: %s skipped (%s)", sym, e)
    write_json("news.json", {"checked_at": t.isoformat(), "sec_8k": bool(config.SEC_USER_AGENT), "stocks": out})
    flagged = [f"{s}({','.join(v['event_risk'])})" for s, v in out.items() if v["event_risk"]]
    log.info("NEWS: %d stocks checked%s. Event risk today (no new trades): %s", len(out),
             "" if config.SEC_USER_AGENT else " (8-K check off: set SEC_USER_AGENT)", ", ".join(flagged) or "none")
    return out
