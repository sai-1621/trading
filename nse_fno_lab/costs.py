"""Trading charges for NSE/BSE options, futures and equity, plus break-even maths.

Rates live in config.py (2026 user-stated STT; verify against your broker's charge sheet).
Every function returns rupees. `qty` is the number of units (lots x lot size).

    .venv\\Scripts\\python costs.py          -> prints the break-even tables used in STRATEGIES.md
"""
from dataclasses import dataclass, asdict

import config as C


@dataclass
class Charges:
    brokerage: float = 0.0
    stt: float = 0.0
    exchange: float = 0.0
    sebi: float = 0.0
    stamp: float = 0.0
    gst: float = 0.0
    slippage: float = 0.0

    @property
    def total(self) -> float:
        return self.brokerage + self.stt + self.exchange + self.sebi + self.stamp + self.gst + self.slippage

    def __add__(self, o: "Charges") -> "Charges":
        return Charges(**{k: getattr(self, k) + getattr(o, k) for k in asdict(self)})

    def as_dict(self) -> dict:
        d = {k: round(v, 2) for k, v in asdict(self).items()}
        d["total"] = round(self.total, 2)
        return d


def _gst(brokerage, exchange, sebi):
    return (brokerage + exchange + sebi) * C.GST_PCT / 100


def option_order(premium: float, qty: int, side: str, exchange: str = "NSE", slippage: bool = True) -> Charges:
    """One executed option order. side = 'buy' | 'sell'. Slippage = SLIPPAGE_PCT of the premium traded."""
    value = premium * qty
    brk = C.BROKERAGE_PER_ORDER if qty > 0 else 0.0
    exch = value * (C.TXN_PCT[(exchange, "OPT")] + (C.IPFT_OPT_PCT if exchange == "NSE" else 0)) / 100
    sebi = value * C.SEBI_PCT / 100
    return Charges(brokerage=brk, stt=value * C.STT_OPT_SELL_PCT / 100 if side == "sell" else 0.0,
                   exchange=exch, sebi=sebi, stamp=value * C.STAMP_BUY_PCT["OPT"] / 100 if side == "buy" else 0.0,
                   gst=_gst(brk, exch, sebi), slippage=value * C.SLIPPAGE_PCT / 100 if slippage else 0.0)


def option_exercise_stt(intrinsic: float, qty: int) -> float:
    """STT charged when a long option expires in the money and is exercised (avoid: square off by 15:15)."""
    return intrinsic * qty * C.STT_OPT_EXERCISE_PCT / 100


def option_round_trip(entry_premium: float, exit_premium: float, qty: int, long: bool = True,
                      exchange: str = "NSE") -> Charges:
    """Charges to open and close one option position (long = buy then sell; short = sell then buy)."""
    a, b = ("buy", "sell") if long else ("sell", "buy")
    return option_order(entry_premium, qty, a, exchange) + option_order(exit_premium, qty, b, exchange)


def futures_order(price: float, qty: int, side: str, exchange: str = "NSE", slippage: bool = True) -> Charges:
    value = price * qty
    brk = C.BROKERAGE_PER_ORDER
    exch = value * C.TXN_PCT[(exchange, "FUT")] / 100
    sebi = value * C.SEBI_PCT / 100
    return Charges(brokerage=brk, stt=value * C.STT_FUT_SELL_PCT / 100 if side == "sell" else 0.0,
                   exchange=exch, sebi=sebi, stamp=value * C.STAMP_BUY_PCT["FUT"] / 100 if side == "buy" else 0.0,
                   gst=_gst(brk, exch, sebi), slippage=value * C.SLIPPAGE_PCT / 100 if slippage else 0.0)


def equity_order(price: float, qty: int, side: str, delivery: bool = False, slippage: bool = True,
                 slippage_pct: float | None = None) -> Charges:
    value = price * qty
    brk = min(20.0, max(C.EQ_BROKERAGE_MIN, value * C.EQ_BROKERAGE_PCT / 100), value)
    exch = value * C.TXN_PCT[("NSE", "EQ")] / 100
    sebi = value * C.SEBI_PCT / 100
    if delivery:
        stt = value * C.STT_EQ_DELIVERY_PCT / 100
        stamp = value * C.STAMP_BUY_PCT["EQ_DELIVERY"] / 100 if side == "buy" else 0.0
    else:
        stt = value * C.STT_EQ_INTRADAY_SELL_PCT / 100 if side == "sell" else 0.0
        stamp = value * C.STAMP_BUY_PCT["EQ_INTRADAY"] / 100 if side == "buy" else 0.0
    sp = C.SLIPPAGE_PCT if slippage_pct is None else slippage_pct
    return Charges(brokerage=brk, stt=stt, exchange=exch, sebi=sebi, stamp=stamp, gst=_gst(brk, exch, sebi),
                   slippage=value * sp / 100 if slippage else 0.0)


def equity_round_trip(entry: float, exit_: float, qty: int, long: bool = True, delivery: bool = False,
                      slippage_pct: float | None = None) -> Charges:
    a, b = ("buy", "sell") if long else ("sell", "buy")
    return (equity_order(entry, qty, a, delivery, slippage_pct=slippage_pct)
            + equity_order(exit_, qty, b, delivery, slippage_pct=slippage_pct))


def option_breakeven_points(premium: float, qty: int, exchange: str = "NSE") -> float:
    """Premium points a long option must gain just to cover the round trip (exit premium solved iteratively)."""
    x = premium
    for _ in range(20):
        cost = option_round_trip(premium, x, qty, True, exchange).total
        x = premium + cost / qty
    return x - premium


def iron_fly_charges(short_prem: float, wing_prem: float, qty: int, exchange: str = "NSE",
                     short_exit: float | None = None, wing_exit: float | None = None) -> Charges:
    """Open + close an iron fly: sell 2 ATM legs (premium each ~ short_prem), buy 2 wings (~ wing_prem each)."""
    se = short_prem * 0.5 if short_exit is None else short_exit   # assume shorts bought back at half
    we = wing_prem * 0.5 if wing_exit is None else wing_exit
    one = option_round_trip(short_prem, se, qty, long=False, exchange=exchange)
    wing = option_round_trip(wing_prem, we, qty, long=True, exchange=exchange)
    return one + one + wing + wing


def _print_tables():
    lot = C.LOT["NIFTY"]
    print(f"NIFTY option, 1 lot = {lot} units, slippage {C.SLIPPAGE_PCT}% per side, STT {C.STT_OPT_SELL_PCT}% on sells")
    print(f"{'premium':>8} {'lots':>4} {'round trip Rs':>13} {'of which slip':>13} {'break-even pts':>14} {'% of premium':>12}")
    for prem in (50, 100, 150, 200, 300):
        for lots in (1, 2):
            q = lots * lot
            rt = option_round_trip(prem, prem, q)
            be = option_breakeven_points(prem, q)
            print(f"{prem:>8} {lots:>4} {rt.total:>13.2f} {rt.slippage:>13.2f} {be:>14.2f} {be / prem * 100:>11.2f}%")
    print("\nbreakdown, 1 lot NIFTY, buy at 150 sell at 150:", option_round_trip(150, 150, lot).as_dict())
    print("\nIron fly 1 lot NIFTY (shorts 120 each, wings 25 each, shorts bought back at 60, wings sold at 10):")
    print(" ", iron_fly_charges(120, 25, lot, short_exit=60, wing_exit=10).as_dict())
    print("Iron fly 1 lot SENSEX (shorts 300, wings 60):",
          iron_fly_charges(300, 60, C.LOT["SENSEX"], "BSE", 150, 25).as_dict())
    print("\nStock intraday Rs 1,00,000 round trip:", equity_round_trip(1000, 1000, 100).as_dict())
    print("Stock delivery Rs 1,00,000 round trip:", equity_round_trip(1000, 1000, 100, delivery=True).as_dict())
    print("NIFTY futures 1 lot round trip at 25,000:",
          (futures_order(25000, lot, "buy") + futures_order(25000, lot, "sell")).as_dict())


if __name__ == "__main__":
    _print_tables()
