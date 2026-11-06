"""Settings for the Angel One NSE bot, read from .env next to this file (or real environment variables).
All clock times are India time (IST). Money is in rupees.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TRADER_DIR = BASE_DIR / "trader"     # each skill writes its latest output here
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


# ---- Angel One SmartAPI (smartapi.angelone.in) ----
ANGEL_API_KEY = os.getenv("ANGEL_API_KEY", "")
ANGEL_CLIENT_CODE = os.getenv("ANGEL_CLIENT_CODE", "")
ANGEL_PIN = os.getenv("ANGEL_PIN", "")
ANGEL_TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET", "")   # the secret behind your authenticator QR code
STATIC_IP = os.getenv("STATIC_IP", "")                    # the IP you registered with Angel One (SEBI rule)

LIVE_TRADING = _bool("LIVE_TRADING", False)
PAPER_START_BALANCE = _num("PAPER_START_BALANCE", 100000)
PRODUCT = "INTRADAY"                                      # MIS: squared off the same day

# ---- universe & scanner ----
UNIVERSE = os.getenv("UNIVERSE", "NIFTY100").upper()      # NIFTY50 | NIFTY100 | NIFTY200 | NIFTY500
WATCH_SIZE = _num("WATCH_SIZE", 30, int)                  # most traded stocks to watch each day
WATCH = _list("WATCH")                                    # optional fixed list, e.g. RELIANCE,TCS
BLOCKED = _list("BLOCKED")
MIN_PRICE = _num("MIN_PRICE", 50)
NEWS_CHECK = _bool("NEWS_CHECK", True)                    # skip a stock with results/board meeting news today

# ---- entry (opening-range breakout on 5-minute bars) ----
BAR_MINUTES = 5
OR_MINUTES = _num("OR_MINUTES", 15, int)                  # 9:15-9:30
MIN_VOLUME_X = _num("MIN_VOLUME_X", 1.0)
NO_ENTRIES_BEFORE = os.getenv("NO_ENTRIES_BEFORE", "09:30")
NO_ENTRIES_AFTER = os.getenv("NO_ENTRIES_AFTER", "14:30")
USE_LEVEL_SETUP = _bool("USE_LEVEL_SETUP", True)
TREND_FILTER = _bool("TREND_FILTER", True)                # only stocks above their 20-day average

# ---- exit ----
STOP_RULE = os.getenv("STOP_RULE", "or-mid")              # or-mid | or-low | pct:X | atr:K
LEVEL_STOP_RULE = os.getenv("LEVEL_STOP_RULE", "atr:1.5")
TARGET_R = _num("TARGET_R", 2.0)
FLAT_BY = os.getenv("FLAT_BY", "15:10")                   # before Angel One's auto square-off (charged extra)
ASSUMED_SPREAD_PCT = _num("ASSUMED_SPREAD_PCT", 0.05)     # bid/ask gap used in paper fills and the time machine

# ---- time machine ----
TM_REPLAYS = _num("TM_REPLAYS", 10000, int)
TM_SEED = _num("TM_SEED", 19, int)
TM_MIN_DAYS = _num("TM_MIN_DAYS", 20, int)
TM_MIN_EDGE_PTS = _num("TM_MIN_EDGE_PTS", 0.0)

# ---- budget & risk ----
BUDGET = _num("BUDGET", 100000)
CASH_BUFFER_PCT = _num("CASH_BUFFER_PCT", 10)
MAX_POSITION_PCT = _num("MAX_POSITION_PCT", 40)
MAX_ORDER = _num("MAX_ORDER", 40000)
MIN_ORDER = _num("MIN_ORDER", 2000)
MAX_LOSS_PER_TRADE = _num("MAX_LOSS_PER_TRADE", 500)
MAX_DAILY_LOSS = _num("MAX_DAILY_LOSS", 1500)
MAX_TRADES_PER_DAY = _num("MAX_TRADES_PER_DAY", 3, int)
MAX_ORDERS_PER_DAY = _num("MAX_ORDERS_PER_DAY", 10, int)
MAX_OPEN_POSITIONS = _num("MAX_OPEN_POSITIONS", 2, int)
MIN_REWARD_RISK = _num("MIN_REWARD_RISK", 1.0)
LEVERAGE = _num("LEVERAGE", 1.0)                          # 1 = no margin. MIS leverage multiplies losses too.

# ---- charges (intraday equity, NSE). Verify against angelone.in before going live ----
BROKERAGE_PCT = _num("BROKERAGE_PCT", 0.1)                # lower of Rs 20 or 0.1% per order, min Rs 5 (Nov 2025)
BROKERAGE_MAX = _num("BROKERAGE_MAX", 20)
BROKERAGE_MIN = _num("BROKERAGE_MIN", 5)
STT_SELL_PCT = _num("STT_SELL_PCT", 0.025)
EXCHANGE_TXN_PCT = _num("EXCHANGE_TXN_PCT", 0.00297)
SEBI_FEE_PCT = _num("SEBI_FEE_PCT", 0.0001)
STAMP_BUY_PCT = _num("STAMP_BUY_PCT", 0.003)
GST_PCT = _num("GST_PCT", 18)

# ---- TradingView webhook (optional) ----
TV_WEBHOOK = _bool("TV_WEBHOOK", False)
TV_PORT = _num("TV_PORT", 8787, int)
TV_SECRET = os.getenv("TV_SECRET", "")
TV_ONLY = _bool("TV_ONLY", False)                          # true = take entries only from TradingView alerts

# ---- Telegram (optional) ----
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

POLL_SECONDS = _num("POLL_SECONDS", 20, int)
# Source of the live 5-minute bars the bot checks every bar for all watched stocks. Angel One's candle API refuses
# about a third of 60 requests a minute (measured 2 Nov 2026), so the per-bar loop stays on Yahoo; Angel One still
# gives the prices at entry and exit, the Time Machine history, the scanner and the trend filter.
LIVE_BARS_SOURCE = os.getenv("LIVE_BARS_SOURCE", "yahoo").lower()      # yahoo | angel
STATE_FILE = BASE_DIR / "state.json"
LOG_FILE = BASE_DIR / "bot.log"
LOCK_FILE = BASE_DIR / "bot.lock"
PID_FILE = BASE_DIR / "bot.pid"
