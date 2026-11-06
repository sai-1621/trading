"""Settings for the index ETF SIP bot, read from .env next to this file. India time (IST), money in rupees."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TRADER_DIR = BASE_DIR / "trader"
TRADER_DIR.mkdir(exist_ok=True)
CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(BASE_DIR / ".env")


def _bool(name, default):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _num(name, default, cast=float):
    v = os.getenv(name, "").strip()
    return cast(v) if v else cast(default)


def parse_weights(text):
    """'NIFTYBEES:70,MON100:30' -> {'NIFTYBEES': 0.7, 'MON100': 0.3}"""
    pairs = [x.split(":") for x in text.split(",") if x.strip()]
    w = {s.strip().upper(): float(v) for s, v in pairs}
    total = sum(w.values())
    return {s: v / total for s, v in w.items()}


# ---- what to buy ----
ETFS = parse_weights(os.getenv("ETFS", "NIFTYBEES:100"))   # target weights; new money tops up what is under its weight
SIP_AMOUNT = _num("SIP_AMOUNT", 10000)                     # rupees each month
SIP_DAY = _num("SIP_DAY", 1, int)                          # first trading day on or after this day of the month
SIP_AFTER = os.getenv("SIP_AFTER", "09:45")                # place the orders after this time (IST)
PAUSE_BELOW_200DMA = _bool("PAUSE_BELOW_200DMA", False)    # hold the month's money as cash while NIFTY is below its 200-day average

# ---- broker ----
ANGEL_API_KEY = os.getenv("ANGEL_API_KEY", "")
ANGEL_CLIENT_CODE = os.getenv("ANGEL_CLIENT_CODE", "")
ANGEL_PIN = os.getenv("ANGEL_PIN", "")
ANGEL_TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET", "")
STATIC_IP = os.getenv("STATIC_IP", "")
LIVE_TRADING = _bool("LIVE_TRADING", False)
PRODUCT = "DELIVERY"                                       # CNC: units go to your demat account and are kept

# ---- charges on an ETF delivery buy (verify on angelone.in) ----
BROKERAGE_PCT = _num("BROKERAGE_PCT", 0.1)
BROKERAGE_MAX = _num("BROKERAGE_MAX", 20)
BROKERAGE_MIN = _num("BROKERAGE_MIN", 5)
STAMP_BUY_PCT = _num("STAMP_BUY_PCT", 0.015)
EXCHANGE_TXN_PCT = _num("EXCHANGE_TXN_PCT", 0.00297)
SEBI_FEE_PCT = _num("SEBI_FEE_PCT", 0.0001)
GST_PCT = _num("GST_PCT", 18)
ASSUMED_SPREAD_PCT = _num("ASSUMED_SPREAD_PCT", 0.05)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

POLL_SECONDS = _num("POLL_SECONDS", 60, int)
STATE_FILE = BASE_DIR / "state.json"
LOG_FILE = BASE_DIR / "bot.log"
LOCK_FILE = BASE_DIR / "bot.lock"
PID_FILE = BASE_DIR / "bot.pid"
