"""Settings for the NSE F&O strategy lab. Read from .env next to this file (or environment variables).
All times are India time (IST). Money is in rupees. Every number here can be overridden in .env.

The 2026 market facts below (lot sizes, expiry days, STT rates) are the user's stated constraints. Check them
against the latest NSE / BSE circulars and your broker's charge sheet before trading real money.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(BASE_DIR / ".env")


def _num(name, default, cast=float):
    v = os.getenv(name, "").strip()
    return cast(v) if v else cast(default)


def _bool(name, default):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# ---- broker / account ----
BROKER = os.getenv("BROKER", "angelone")          # angelone (SmartAPI) | paper
LIVE_TRADING = _bool("LIVE_TRADING", False)       # stays False until readiness passes
ANGEL_API_KEY = os.getenv("ANGEL_API_KEY", "")
ANGEL_CLIENT_CODE = os.getenv("ANGEL_CLIENT_CODE", "")
ANGEL_PIN = os.getenv("ANGEL_PIN", "")
ANGEL_TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET", "")

CAPITAL = _num("CAPITAL", 300000)
MAX_DAILY_LOSS_PCT = _num("MAX_DAILY_LOSS_PCT", 1.5)
MAX_TRADES_PER_DAY = _num("MAX_TRADES_PER_DAY", 3, int)
MAX_LOSSES_PER_DAY = _num("MAX_LOSSES_PER_DAY", 2, int)   # stop for the day after this many losing trades

# ---- 2026 contract specs (user-stated) ----
LOT = {"NIFTY": _num("LOT_NIFTY", 65, int), "BANKNIFTY": _num("LOT_BANKNIFTY", 30, int),
       "SENSEX": _num("LOT_SENSEX", 20, int), "FINNIFTY": _num("LOT_FINNIFTY", 60, int)}
STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100, "SENSEX": 100, "FINNIFTY": 50}
WEEKLY_EXPIRY_WEEKDAY = {"NIFTY": 1, "SENSEX": 3}         # Mon=0: NIFTY Tuesday, SENSEX Thursday
MONTHLY_ONLY = {"BANKNIFTY", "FINNIFTY"}                  # monthly expiry = last Tuesday of the month
EXCHANGE_OF = {"NIFTY": "NSE", "BANKNIFTY": "NSE", "FINNIFTY": "NSE", "SENSEX": "BSE"}

# ---- session ----
MARKET_OPEN = "09:15"
NO_ENTRY_BEFORE = os.getenv("NO_ENTRY_BEFORE", "09:20")
SQUARE_OFF = os.getenv("SQUARE_OFF", "15:15")
MARKET_CLOSE = "15:30"

# ---- charges (percent of value unless named PER_ORDER) ----
BROKERAGE_PER_ORDER = _num("BROKERAGE_PER_ORDER", 20)     # F&O flat per executed order (Angel One)
EQ_BROKERAGE_PCT = _num("EQ_BROKERAGE_PCT", 0.1)          # equity: lower of Rs 20 or 0.1%, min Rs 5... (Angel One)
EQ_BROKERAGE_MIN = _num("EQ_BROKERAGE_MIN", 5)
STT_OPT_SELL_PCT = _num("STT_OPT_SELL_PCT", 0.15)         # on sell-side premium (post-April 2026)
STT_OPT_EXERCISE_PCT = _num("STT_OPT_EXERCISE_PCT", 0.15)  # on intrinsic value if exercised ITM
STT_FUT_SELL_PCT = _num("STT_FUT_SELL_PCT", 0.05)
STT_EQ_INTRADAY_SELL_PCT = _num("STT_EQ_INTRADAY_SELL_PCT", 0.025)
STT_EQ_DELIVERY_PCT = _num("STT_EQ_DELIVERY_PCT", 0.1)    # both sides
TXN_PCT = {  # exchange transaction charges
    ("NSE", "OPT"): _num("TXN_NSE_OPT_PCT", 0.03503), ("BSE", "OPT"): _num("TXN_BSE_OPT_PCT", 0.0325),
    ("NSE", "FUT"): _num("TXN_NSE_FUT_PCT", 0.00173), ("NSE", "EQ"): _num("TXN_NSE_EQ_PCT", 0.00297),
}
IPFT_OPT_PCT = _num("IPFT_OPT_PCT", 0.0005)               # NSE investor protection fund, options premium
SEBI_PCT = _num("SEBI_PCT", 0.0001)                       # Rs 10 per crore
STAMP_BUY_PCT = {"OPT": 0.003, "FUT": 0.002, "EQ_INTRADAY": 0.003, "EQ_DELIVERY": 0.015}
GST_PCT = _num("GST_PCT", 18)                             # on brokerage + exchange + SEBI
SLIPPAGE_PCT = _num("SLIPPAGE_PCT", 0.1)                  # assumed, per side, of the traded price

RISK_FREE = _num("RISK_FREE", 0.065)                      # for option pricing / greeks

# ---- strategy 1: NIFTY directional option buying ----
S1 = {
    "symbol": os.getenv("S1_SYMBOL", "NIFTY"),
    "vix_min": _num("S1_VIX_MIN", 11), "vix_max": _num("S1_VIX_MAX", 22),
    "adx_min": _num("S1_ADX_MIN", 20),
    "or_width_min_atr": 0.0,                              # no minimum: a quiet open then expansion is the setup;
    "or_width_max_atr": 1.2,                              # skip opens wider than 1.2x daily ATR(14) (move already made)
    "vwap_dist_atr": 0.25,                                # close must be this many 5m-ATRs beyond VWAP
    "breakout_bars": 3,                                   # close above the high of the previous 3 bars
    "atr_fast": 5, "atr_slow": 20, "atr_expansion": 1.2,  # ATR(5)/ATR(20) on 5-minute bars
    "oi_window_bars": 3, "oi_strikes": 2, "oi_min_shift": 0.02,
    "gamma_theta_min": 2.0,                               # 0.5*gamma*ATR5^2 / 5-minute theta (about ATR >= 1.4x the VIX-implied move)
    "hold_bars_expected": 6, "edge_k": 1.5, "slippage_pts": 0.5,
    "edge_ratio_min": _num("S1_EDGE_RATIO_MIN", 0),        # optional, off by default
    "min_dte_days": 2,                                    # never buy options expiring in under 2 days
    "stop_atr": 1.2, "stop_min_pct": 0.06, "stop_max_pct": 0.18,   # = 15-45 pts on NIFTY 25,000;
    "premium_stop_pct": 30, "target_r": 2.0, "trail_atr": 1.0, "breakeven_at_r": 1.0,
    "time_stop_bars": 6, "time_stop_min_r": 0.5, "last_entry": "14:30", "cooldown_bars": 2,
    "max_premium_capital_pct": 25,
    # leg 2: midday flag continuation after a qualified morning impulse
    "flag_enabled": _bool("S1_FLAG", True), "flag_min_bars": 6, "flag_max_bars": 15,
    "flag_contraction": 0.8,                              # flag volume (or range) < 0.8x the impulse average
    "flag_gamma_theta_min": _num("S1_FLAG_GT_MIN", 1.6),
    # climax test (pre-registered 1 Nov 2026, before any 3-year data): min | band | band_pullback
    "gt_mode": os.getenv("S1_GT_MODE", "min"), "gt_band": (1.50, 1.90),
    "pullback_frac": 0.30, "pullback_bars": 2,            # limit at close - 30% of the bar's range, next 2 bars
}

# ---- strategy 2: expiry-day iron fly (NIFTY Tuesday / SENSEX Thursday) ----
S2 = {
    "symbol": os.getenv("S2_SYMBOL", "NIFTY"),
    "entry_time": os.getenv("S2_ENTRY", "09:45"), "last_adjust": "13:30",
    "vix_max": _num("S2_VIX_MAX", 18), "vix_jump_max_pct": 7, "gap_max_pct": 0.8,
    "or_max_expected_move": 0.6,                          # open-to-entry range vs expected daily move (x sqrt(minutes/30))
    "min_premium_vs_realized": 1.05,                      # straddle >= 1.05x average recent open-close move
    "wing_mult": 1.0,                                     # wings at ATM +/- (wing_mult x credit), rounded up
    "wing_iv_mult": _num("S2_WING_IV_MULT", 1.35),          # model smile: wing IV = 1.35 x ATM IV (backtests)
    "profit_take_pct": 40, "stop_pct_of_max_loss": 60, "max_adjustments": 1,
    "event_days": [d for d in os.getenv("EVENT_DAYS", "").split(",") if d],
}

# ---- strategy 3: F&O stocks relative strength + volume breakout ----
S3 = {
    "scan_time": "09:30", "universe_size": 50, "picks_long": 3, "picks_short": 3,
    "min_vol_ratio": 1.5, "min_rs_pct": 0.3, "use_delivery": _bool("S3_USE_DELIVERY", False),
    "delivery_ratio_min": 1.2, "nifty_or_max_pct": 1.2, "vix_max": 24,
    "stop_atr": 1.5, "stop_min_pct": 0.4, "stop_max_pct": 2.0, "target_r": 2.0,
    "trail_atr": 2.0, "breakeven_at_r": 1.0, "last_entry": "13:30", "time_stop_bars": 12,
    "mode": os.getenv("S3_MODE", "intraday"),            # intraday | swing3
    "swing_days": 3, "mis_leverage": 5.0, "max_position_capital_pct": 33,
}
