"""Charges on an NSE intraday equity order through Angel One (rates in config.py; verify before going live)."""
import config


def order_charges(value: float, side: str) -> float:
    """Total charges in rupees for one executed order of `value` rupees."""
    brokerage = max(config.BROKERAGE_MIN, min(config.BROKERAGE_MAX, value * config.BROKERAGE_PCT / 100))
    brokerage = min(brokerage, value)  # never more than the order itself
    txn = value * config.EXCHANGE_TXN_PCT / 100
    sebi = value * config.SEBI_FEE_PCT / 100
    stt = value * config.STT_SELL_PCT / 100 if side == "sell" else 0.0
    stamp = value * config.STAMP_BUY_PCT / 100 if side == "buy" else 0.0
    gst = (brokerage + txn + sebi) * config.GST_PCT / 100
    return brokerage + txn + sebi + stt + stamp + gst


def round_trip(entry_value: float, exit_value: float) -> float:
    return order_charges(entry_value, "buy") + order_charges(exit_value, "sell")


def round_trip_frac(value: float) -> float:
    """Round-trip charges as a fraction of the position value (for backtests)."""
    return round_trip(value, value) / value if value else 0.0
