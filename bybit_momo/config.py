"""Settings for the Bybit momentum bot, read from .env next to this file (or environment variables).
Paper mode unless LIVE_TRADING=true AND API keys are set AND the readiness check passes (see README)."""
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent
STATE_DIR = BASE / "state"
STATE_DIR.mkdir(exist_ok=True)


def _load_env(p: Path):
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(BASE / ".env")


def _num(k, d, cast=float):
    v = os.getenv(k, "").strip()
    return cast(v) if v else cast(d)


def _bool(k, d):
    return os.getenv(k, str(d)).strip().lower() in ("1", "true", "yes", "on")


API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
TESTNET = _bool("BYBIT_TESTNET", False)
LIVE_TRADING = _bool("LIVE_TRADING", False)
MARKET = os.getenv("MARKET", "linear")           # linear (USDT perpetuals) | spot

CAPITAL = _num("PAPER_CAPITAL", 1000)            # USDT
RISK_PCT = _num("RISK_PCT", 1.0)                 # % of equity lost if the stop is hit (incl. estimated costs)
MAX_OPEN = _num("MAX_OPEN", 5, int)
MAX_POSITION_USDT = _num("MAX_POSITION_USDT", 250)
LIQ_FRAC = _num("LIQ_FRAC", 0.10)                # position <= 10% of the coin's average 5-minute turnover
LEVERAGE = _num("LEVERAGE", 1, int)              # perps only; 1 = no leverage beyond the position cap
DAILY_LOSS_PCT = _num("DAILY_LOSS_PCT", 3.0)     # stop opening trades for the day after this loss
LEGS = [x.strip() for x in os.getenv("LEGS", "ignition,flag").split(",") if x.strip()]

# limit chase (entries): post-only at the best bid, re-priced every CHASE_SECONDS, up to CHASE_STEPS times,
# abandoned if the best bid runs more than CHASE_MAX_PCT above the signal close
CHASE_SECONDS = _num("CHASE_SECONDS", 8)
CHASE_STEPS = _num("CHASE_STEPS", 6, int)
CHASE_MAX_PCT = _num("CHASE_MAX_PCT", 0.2)

TURNOVER_MIN = _num("TURNOVER_MIN", 5e5)
TURNOVER_MAX = _num("TURNOVER_MAX", 5e8)
UNIVERSE_REFRESH_MIN = _num("UNIVERSE_REFRESH_MIN", 30)
HISTORY_BARS = 400
WS_CHUNK = 10                                    # symbols per WebSocket subscription
FEE_TAKER, FEE_MAKER = 0.00055, 0.0002           # Bybit perps (spot: 0.001 / 0.001)
PAPER_EXIT_SLIP = 0.0005
KILL_FILE = BASE / "KILL"
