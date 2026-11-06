"""The day-trader skill pack (github.com/Jakeschincariol/day-trader-skill), ported to NSE stocks via Angel One.

scanner -> news -> entry -> exit_plan -> timemachine -> budget -> risk -> broker, then alerts while a trade is open.
"""
import json

import config


def write_json(name: str, data) -> None:
    (config.TRADER_DIR / name).write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def today_bars(bars, day):
    return [b for b in bars if b["t"].date() == day]
