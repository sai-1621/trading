"""Black-Scholes prices, greeks and implied volatility (European, no dividends; fine for index options).
All functions are vectorised: S, K, T, sigma can be numpy arrays. T in years, sigma as a fraction (0.14 = 14%).
cp = +1 for a call (CE), -1 for a put (PE). T is in TRADING years (nse_calendar.years_to_expiry);
theta is returned per trading day (one 375-minute session).
"""
import numpy as np
from scipy.special import ndtr

SQRT2PI = np.sqrt(2 * np.pi)


def _d1d2(S, K, T, r, sigma):
    S, K, T, sigma = (np.asarray(x, float) for x in (S, K, T, sigma))
    v = sigma * np.sqrt(T)
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / v
    return d1, d1 - v


def price(S, K, T, r, sigma, cp):
    d1, d2 = _d1d2(S, K, T, r, sigma)
    cp = np.asarray(cp, float)
    return cp * (S * ndtr(cp * d1) - K * np.exp(-r * T) * ndtr(cp * d2))


def delta(S, K, T, r, sigma, cp):
    d1, _ = _d1d2(S, K, T, r, sigma)
    cp = np.asarray(cp, float)
    return np.where(cp > 0, ndtr(d1), ndtr(d1) - 1)


def gamma(S, K, T, r, sigma):
    d1, _ = _d1d2(S, K, T, r, sigma)
    return np.exp(-0.5 * d1 ** 2) / SQRT2PI / (np.asarray(S, float) * sigma * np.sqrt(T))


def vega(S, K, T, r, sigma):
    d1, _ = _d1d2(S, K, T, r, sigma)
    return np.asarray(S, float) * np.exp(-0.5 * d1 ** 2) / SQRT2PI * np.sqrt(T) / 100  # per 1 vol point


def theta(S, K, T, r, sigma, cp):
    d1, d2 = _d1d2(S, K, T, r, sigma)
    cp = np.asarray(cp, float)
    S = np.asarray(S, float)
    t = (-S * np.exp(-0.5 * d1 ** 2) / SQRT2PI * sigma / (2 * np.sqrt(T))
         - cp * r * K * np.exp(-r * T) * ndtr(cp * d2))
    return t / 252


def implied_vol(px, S, K, T, r, cp, lo=0.01, hi=3.0, iters=60):
    """Bisection (robust, vectorised). Returns nan where the price is below intrinsic or above the upper bound."""
    px, S, K, T = (np.asarray(x, float) for x in (px, S, K, T))
    a = np.full(np.broadcast(px, S, K, T).shape, lo)
    b = np.full_like(a, hi)
    for _ in range(iters):
        m = (a + b) / 2
        too_high = price(S, K, T, r, m, cp) > px
        b = np.where(too_high, m, b)
        a = np.where(too_high, a, m)
    iv = (a + b) / 2
    bad = (price(S, K, T, r, lo, cp) > px + 1e-9) | (price(S, K, T, r, hi, cp) < px - 1e-9)
    return np.where(bad, np.nan, iv)
