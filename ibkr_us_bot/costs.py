"""Charges on a US stock order through Interactive Brokers (IBKR Pro Fixed). Rates in config.py; verify before going live."""
import config


def order_charges(value: float, side: str, shares: float) -> float:
    commission = max(config.COMMISSION_MIN, shares * config.COMMISSION_PER_SHARE)
    commission = min(commission, value * config.COMMISSION_MAX_PCT / 100)
    fees = 0.0
    if side == "sell":
        fees += value * config.SEC_FEE_RATE
        fees += min(config.FINRA_TAF_MAX, shares * config.FINRA_TAF_PER_SHARE)
    return commission + fees


def round_trip(entry_value: float, exit_value: float, shares: float) -> float:
    return order_charges(entry_value, "buy", shares) + order_charges(exit_value, "sell", shares)
