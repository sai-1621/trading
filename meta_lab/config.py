"""Settings for the meta-labeling lab. Every iteration is a named dict in ITERATIONS, so no run is ever lost or edited
after the fact: a change = a new iteration number."""
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "freqtrade_bot" / "user_data" / "data_5y"     # Binance USDT-M futures, 1h, Nov 2021 - Oct 2026
CACHE = HERE / "cache"
RESULTS = HERE / "results"

# ---- friction (per side). Taker fee 0.05% + slippage 0.10% => 0.30% round trip, plus real funding paid on longs.
FEE = 0.0005
SLIP = 0.0010

# ---- universe: point-in-time 30-day mean daily dollar volume, and enough history for the slow indicators
MIN_DVOL = 5e6
MIN_BARS = 220                     # 4h bars (~37 days)

# ---- walk-forward
OOS_START = pd.Timestamp("2023-01-01", tz="UTC")     # research OOS: 2023-01 .. 2025-12, retrained every quarter
HOLDOUT_START = pd.Timestamp("2026-01-01", tz="UTC") # final holdout: touched ONCE, with the frozen final iteration
EMBARGO = pd.Timedelta(days=1)                       # extra gap after the purge
RETRAIN = "QS"                                       # quarterly folds

# ---- portfolio
CAPITAL = 10_000.0
SLOTS = 10                         # max open positions; each gets CAPITAL / SLOTS notional (no compounding, no leverage)

# ---- pre-registered pass rules (set before any run; see README)
PASS = {"pf": 1.35, "t_trade": 2.0, "t_weekly": 2.0, "payoff_ratio": 1.5}

LGB = dict(n_estimators=400, learning_rate=0.02, num_leaves=15, min_child_samples=300, subsample=0.8,
           subsample_freq=1, colsample_bytree=0.7, reg_lambda=5.0, random_state=7, verbose=-1, n_jobs=4)
MIN_TRAIN = 300                    # trades needed before a fold is trained
THRESHOLDS = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]

ITERATIONS = {
    # Iteration 1: Donchian-20 breakout on 4h, fixed ATR triple barrier, label = net trade return > 0
    1: dict(name="donchian20_4h_triple_barrier",
            signal="donchian", donch=20, stop_atr=2.0, target_atr=3.0, max_bars=30, exit="barrier",
            label="net_positive", features="base"),
    # Iteration 2 (after diagnosing 1): structural fixes
    #  - exit: no fixed target (it capped every winner at 1.5R); 2-ATR initial stop + 3-ATR chandelier trail, 60 bars
    #  - features: drop f_age (calendar-time proxy), add cross-sectional ranks (stationary)
    #  - threshold: fixed "keep the top 30%" rule instead of the unstable max-return grid search
    2: dict(name="donchian20_4h_chandelier_ranks",
            signal="donchian", donch=20, stop_atr=2.0, target_atr=None, trail_atr=3.0, max_bars=60,
            exit="chandelier", label="net_positive", features="v2", threshold="top30"),
    # Iteration 3 (after diagnosing 2): alt breakouts trailed BTC by 1.1%/trade and the model only learned regimes
    #  - timeframe 1d (cost per R ~2.5x lower) and a point-in-time top-40 liquidity universe
    #  - regime as a fixed gate (BTC > EMA200), not a learnable feature; model sees trade-specific features only
    #  - Donchian 20 days, 2-ATR initial stop, 3-ATR chandelier, max 60 days (classic trend-following settings)
    3: dict(name="donchian20_1d_top40_btcgate",
            tf="1d", top_n=40, regime="btc_ema200",
            signal="donchian", donch=20, stop_atr=2.0, target_atr=None, trail_atr=3.0, max_bars=60,
            exit="chandelier", label="net_positive", features="v3", threshold="top30",
            lgb=dict(min_child_samples=40)),       # ~10x fewer trades than on 4h, so smaller leaves
}
