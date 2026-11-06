"""News Monitor (trader-news), for NSE stocks.

Reads Google News headlines for each watched company from the last day, tags each one, and flags
event risk: a stock with results or a board meeting in the news today can gap or swing hard,
so the bot does not open a new day trade in it (NEWS_CHECK=true).
"""
import logging
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import requests

from market import IST, now
from skills import write_json

log = logging.getLogger("bot")

TAGS = {
    "results": r"\bq[1-4]\b.*\b(results?|profit|earnings|revenue)\b|\b(results?|earnings)\b.*\b(today|date|preview|declared)\b"
               r"|quarterly (results?|earnings)|\bnet (profit|loss)\b",
    "board_meeting": r"board meeting|board to consider|fund ?rais|rights issue|bonus issue|stock split",
    "dividend": r"dividend|record date",
    "deal": r"acqui|merger|stake|block deal|bulk deal|takeover|buyback|open offer",
    "regulatory": r"\bsebi\b|\brbi\b|penalty|probe|raid|\bed\b|\bcbi\b|court|tribunal|nclt",
    "management": r"resign|appoint|\bceo\b|\bmd\b|managing director|chairman",
    "rating": r"upgrade|downgrade|target price|rating|brokerage|buy call|sell call",
}
EVENT_RISK = {"results", "board_meeting"}


def short_name(company: str) -> str:
    """'Reliance Industries Ltd.' -> 'Reliance Industries'; 'HDFC Bank Ltd.' -> 'HDFC Bank'."""
    return re.sub(r"\b(ltd|limited|corporation|corp|company|co|inc)\b\.?", "", company, flags=re.I).strip(" .,")


def headlines(company: str, symbol: str) -> list[dict]:
    q = f'"{company}" OR "{symbol}" share when:1d'
    r = requests.get("https://news.google.com/rss/search",
                     params={"q": q, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"}, timeout=15)
    r.raise_for_status()
    items = []
    for it in ET.fromstring(r.content).iter("item"):
        title = (it.findtext("title") or "").strip()
        try:
            when = parsedate_to_datetime(it.findtext("pubDate")).astimezone(IST)
        except (TypeError, ValueError):
            continue
        tags = [k for k, pat in TAGS.items() if re.search(pat, title, re.I)]
        items.append({"time": when.isoformat(), "title": title, "tags": tags})
    items.sort(key=lambda x: x["time"], reverse=True)
    return items


def check(watchlist: list[dict]) -> dict:
    out = {}
    cutoff = now().replace(hour=0, minute=0, second=0, microsecond=0)
    for w in watchlist:
        try:
            items = [i for i in headlines(w["name"], w["symbol"]) if i["time"] >= cutoff.isoformat()]
        except Exception as e:
            log.warning("News: %s skipped (%s)", w["symbol"], e)
            continue
        # event risk only from headlines that name this company (not round-ups or namesakes)
        key = short_name(w["name"])
        risk = sorted({t for i in items if re.search(rf"\b({re.escape(key)}|{re.escape(w['symbol'])})\b", i["title"], re.I)
                       for t in i["tags"] if t in EVENT_RISK})
        out[w["symbol"]] = {"count": len(items), "event_risk": risk, "latest": items[:5]}
    write_json("news.json", {"checked_at": now().isoformat(), "stocks": out})
    flagged = [f"{s}({','.join(v['event_risk'])})" for s, v in out.items() if v["event_risk"]]
    log.info("NEWS: %d stocks checked. Event risk today (no new trades): %s", len(out), ", ".join(flagged) or "none")
    return out
