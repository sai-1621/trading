"""Settings for the Interactive Brokers US stock bot, read from .env next to this file (or real environment variables).
Clock times are New York time (ET). Money is in US dollars.
"""
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


def _list(name, default=""):
    return [x.strip().upper() for x in os.getenv(name, default).split(",") if x.strip()]


# ---- Interactive Brokers (TWS or IB Gateway must be running, with the API enabled) ----
IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = _num("IB_PORT", 7497, int)          # TWS paper 7497, TWS live 7496, Gateway paper 4002, Gateway live 4001
IB_CLIENT_ID = _num("IB_CLIENT_ID", 17, int)
IB_ACCOUNT = os.getenv("IB_ACCOUNT", "")       # optional: which account, if you have several
IB_DELAYED_DATA = _bool("IB_DELAYED_DATA", True)  # use free delayed quotes when you have no live data subscription

LIVE_TRADING = _bool("LIVE_TRADING", False)
PAPER_START_BALANCE = _num("PAPER_START_BALANCE", 5000)

# ---- universe & scanner ----
UNIVERSE = os.getenv("UNIVERSE", "SP500").upper()
WATCH_SIZE = _num("WATCH_SIZE", 30, int)
WATCH = _list("WATCH")
BLOCKED = _list("BLOCKED")
MIN_PRICE = _num("MIN_PRICE", 10)
NEWS_CHECK = _bool("NEWS_CHECK", True)
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "")   # "Your Name you@example.com": SEC EDGAR asks you to identify yourself

# ---- entry (opening-range breakout on 5-minute bars) ----
BAR_MINUTES = 5
OR_MINUTES = _num("OR_MINUTES", 15, int)            # 9:30-9:45
MIN_VOLUME_X = _num("MIN_VOLUME_X", 0)      # off, as in the original pack (the 9:30 bar holds the opening auction)
NO_ENTRIES_BEFORE = os.getenv("NO_ENTRIES_BEFORE", "09:45")
NO_ENTRIES_AFTER = os.getenv("NO_ENTRIES_AFTER", "15:30")
USE_LEVEL_SETUP = _bool("USE_LEVEL_SETUP", True)
TREND_FILTER = _bool("TREND_FILTER", True)

# ---- exit ----
STOP_RULE = os.getenv("STOP_RULE", "or-mid")
LEVEL_STOP_RULE = os.getenv("LEVEL_STOP_RULE", "atr:1.5")
TARGET_R = _num("TARGET_R", 2.0)
FLAT_BY = os.getenv("FLAT_BY", "15:55")
ASSUMED_SPREAD_PCT = _num("ASSUMED_SPREAD_PCT", 0.02)

# ---- time machine ----
TM_REPLAYS = _num("TM_REPLAYS", 10000, int)
TM_SEED = _num("TM_SEED", 19, int)
TM_MIN_DAYS = _num("TM_MIN_DAYS", 20, int)
TM_MIN_EDGE_PTS = _num("TM_MIN_EDGE_PTS", 0.0)

# ---- budget & risk (the original pack's example config) ----
BUDGET = _num("BUDGET", 5000)
CASH_BUFFER_PCT = _num("CASH_BUFFER_PCT", 10)
MAX_POSITION_PCT = _num("MAX_POSITION_PCT", 75)
MAX_ORDER = _num("MAX_ORDER", 4000)
MIN_ORDER = _num("MIN_ORDER", 100)
MAX_LOSS_PER_TRADE = _num("MAX_LOSS_PER_TRADE", 50)
MAX_DAILY_LOSS = _num("MAX_DAILY_LOSS", 150)
MAX_TRADES_PER_DAY = _num("MAX_TRADES_PER_DAY", 3, int)
MAX_ORDERS_PER_DAY = _num("MAX_ORDERS_PER_DAY", 10, int)
MAX_OPEN_POSITIONS = _num("MAX_OPEN_POSITIONS", 2, int)
MIN_REWARD_RISK = _num("MIN_REWARD_RISK", 1.0)
PDT_GUARD = _bool("PDT_GUARD", False)   # turn on if your account is still under the old pattern-day-trader rule

# ---- charges (IBKR Pro Fixed, US stocks; verify on interactivebrokers.com) ----
COMMISSION_PER_SHARE = _num("COMMISSION_PER_SHARE", 0.005)
COMMISSION_MIN = _num("COMMISSION_MIN", 1.0)
COMMISSION_MAX_PCT = _num("COMMISSION_MAX_PCT", 1.0)
SEC_FEE_RATE = _num("SEC_FEE_RATE", 0.0000206)      # x value of sells
FINRA_TAF_PER_SHARE = _num("FINRA_TAF_PER_SHARE", 0.000195)  # x shares sold
FINRA_TAF_MAX = _num("FINRA_TAF_MAX", 9.79)

# ---- TradingView webhook (optional) ----
TV_WEBHOOK = _bool("TV_WEBHOOK", False)
TV_PORT = _num("TV_PORT", 8788, int)
TV_SECRET = os.getenv("TV_SECRET", "")
TV_ONLY = _bool("TV_ONLY", False)

# ---- Telegram (optional) ----
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

POLL_SECONDS = _num("POLL_SECONDS", 20, int)
STATE_FILE = BASE_DIR / "state.json"
LOG_FILE = BASE_DIR / "bot.log"
LOCK_FILE = BASE_DIR / "bot.lock"
PID_FILE = BASE_DIR / "bot.pid"
