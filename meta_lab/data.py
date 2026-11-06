"""Load the 1h Binance futures candles + funding, resample to 4h, cache. Coins delisted before Oct 2026 are missing
(survivorship bias, which flatters any long strategy)."""
import pickle

import numpy as np
import pandas as pd

import config as C


def _load_coin(f):
    d = pd.read_feather(f).set_index("date").sort_index()
    d = d[~d.index.duplicated()]
    g = d.resample("4h", label="left", closed="left", origin="epoch")
    o = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                      "close": g["close"].last(), "volume": g["volume"].sum(), "n": g["close"].count()})
    o = o[o["n"] > 0].drop(columns="n")
    o["volume"] = o["volume"].astype(float)
    return o


def load_panel(refresh=False):
    """{coin: 4h DataFrame}, {coin: funding Series}. Cached in cache/panel4h.pkl."""
    C.CACHE.mkdir(exist_ok=True)
    p = C.CACHE / "panel4h.pkl"
    if p.exists() and not refresh:
        with p.open("rb") as fh:
            return pickle.load(fh)
    bars, fund = {}, {}
    for f in sorted(C.SRC.glob("*-1h-futures.feather")):
        coin = f.name.split("_USDT_USDT")[0]
        b = _load_coin(f)
        if len(b) < C.MIN_BARS + 50:
            continue
        bars[coin] = b
        ff = C.SRC / f.name.replace("-1h-futures", "-1h-funding_rate")
        if ff.exists():
            s = pd.read_feather(ff).set_index("date")["funding_rate"].sort_index()
            fund[coin] = s[~s.index.duplicated()].astype(float)
        else:
            fund[coin] = pd.Series(dtype=float)
    with p.open("wb") as fh:
        pickle.dump((bars, fund), fh)
    return bars, fund


def daily_panel(refresh=False):
    """Daily bars (00:00 UTC) built from the 4h panel."""
    p = C.CACHE / "panel1d.pkl"
    if p.exists() and not refresh:
        with p.open("rb") as fh:
            return pickle.load(fh)
    bars, fund = load_panel()
    out = {}
    for k, b in bars.items():
        g = b.resample("1D", label="left", closed="left")
        d = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                          "close": g["close"].last(), "volume": g["volume"].sum(), "n": g["close"].count()})
        d = d[d["n"] == 6].drop(columns="n")          # complete days only
        if len(d) >= 250:
            out[k] = d
    with p.open("wb") as fh:
        pickle.dump((out, {k: fund[k] for k in out}), fh)
    return out, {k: fund[k] for k in out}


def time_index(bars):
    idx = sorted(set().union(*[b.index for b in bars.values()]))
    return pd.DatetimeIndex(idx)


if __name__ == "__main__":
    b, f = load_panel(refresh=True)
    ti = time_index(b)
    print(len(b), "coins,", len(ti), "4h bars", ti[0], "->", ti[-1])
    print("BTC rows", len(b["BTC"]), "funding rows", len(f["BTC"]), np.round(b["BTC"]["close"].iloc[-1], 1))
