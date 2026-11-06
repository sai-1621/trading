"""Telegram messages for trades, errors, crashes and the daily summary. Does nothing when not set up."""
import logging
import time

import requests

import config

log = logging.getLogger("bot")
_last_sent = {}


def notify(text: str, key: str | None = None, every_seconds: int = 0) -> None:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return
    if key and time.time() - _last_sent.get(key, 0) < every_seconds:
        return
    mode = "LIVE" if config.LIVE_TRADING else "PAPER"
    try:
        requests.post(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                      json={"chat_id": config.TELEGRAM_CHAT_ID, "text": f"[Angel One bot {mode}] {text}"}, timeout=10)
        if key:
            _last_sent[key] = time.time()
    except requests.RequestException as e:
        log.warning("Telegram message failed: %s", e)
