"""Delta-neutral funding-rate carry on Binance: long spot + short USDT-M perpetual, hourly simulation, 2021-01 .. 2026-10.

    .venv\\Scripts\\python backtest_funding_arb.py                 base case (rules below) + sensitivity table
    .venv\\Scripts\\python backtest_funding_arb.py --base-only

Rules (fixed before the first run, from the spec):
- Universe: perps in the point-in-time top 100 by 30-day dollar volume (yesterday's rank), with a Binance spot USDT pair
  that has >= $1M/day spot volume. "1000PEPE"-style perps are matched to the spot coin x1000.
- Funding rates are scaled to an 8-hour equivalent (most coins now settle every 4h, some every 1h).
- Entry: at a funding settlement, last rate >= 0.03%/8h AND the mean of the settlements in the last 24h >= 0.03%/8h
  (persistence = the proxy for "expected holding > 24h"; no predicted-rate history exists). Executed at the NEXT hour's
  open: buy spot, short the same quantity of perp. Highest rate first if more candidates than free slots.
- Exit: at a settlement with rate < 0.005%/8h (incl. negative), unwind at the next hour's open.
- Sizing: up to 10 pairs, each 1/10 of current equity. Per pair: perp margin = 40% of notional (isolated), so the short
  survives a +35% spike with a 2% maintenance margin; the rest buys spot. No spot leverage, so no borrow cost.
- Funding is paid/received on the perp notional at each settlement after entry (short receives positive rates).
- Liquidation: checked on every hour's perp HIGH (last price, stricter than Binance's mark price). Liquidated -> the
  whole perp margin is lost, the spot is sold at the lower of that hour's close and the liquidation level.
- De-risking: at an hour close, if perp margin equity < 15% of notional (price up ~20%+), the pair is re-sized: spot
  gains move to the futures wallet and both legs shrink equally so margin is back to 40%. Fees + slippage charged.
- Friction per leg: perp taker 0.05%, spot taker 0.10%, slippage 0.05% (top-30 coins) or 0.10% (others).
- Benchmark: USDC held at a constant 5% a year (an assumption; no rate history is downloaded).
Known bias: coins delisted from Binance before Oct 2026 are missing (e.g. LUNA, FTT); the spot hedge offsets most of
a collapse, but delisting events (forced perp settlement, spot halts) are not simulated.
"""
import argparse
import json
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OLD = HERE.parent / "freqtrade_bot" / "user_data" / "data_5y"
RESULTS = HERE / "results"
G0 = pd.Timestamp("2021-01-01", tz="UTC")
G1 = pd.Timestamp("2026-10-29 16:00", tz="UTC")
HOUR = pd.Timedelta(hours=1)


@dataclass(frozen=True)
class Params:
    entry_rate: float = 0.0003          # per 8h
    entry_mean24: float = 0.0003
    exit_rate: float = 0.00005
    top_n: int = 100
    min_spot_dvol: float = 1e6
    slots: int = 10
    margin_ratio: float = 0.40          # perp margin / notional
    mmr: float = 0.02                   # maintenance margin rate
    derisk_at: float = 0.15             # re-size when perp margin equity / notional falls below this
    fee_perp: float = 0.0005
    fee_spot: float = 0.0010
    slip_top: float = 0.0005
    slip_other: float = 0.0010
    top_slip_rank: int = 30
    liq_trigger: str = "last"           # "last": perp last-price high (strict); "mark_proxy": spot high must reach it too
    usdc_apy: float = 0.05
    capital: float = 100_000.0


# ----------------------------------------------------------------------------------------------- data
def _read(f):
    return pd.read_feather(f) if f.exists() else None


def load_perp(c):
    parts = [_read(DATA / "perp_ext" / f"{c}-1h.feather"), _read(OLD / f"{c}_USDT_USDT-1h-futures.feather")]
    d = pd.concat([p for p in parts if p is not None and len(p)]).drop_duplicates("date").set_index("date").sort_index()
    fparts = [_read(DATA / "perp_ext" / f"{c}-funding.feather"), _read(OLD / f"{c}_USDT_USDT-1h-funding_rate.feather")]
    f = pd.concat([p for p in fparts if p is not None and len(p)])
    f["date"] = pd.to_datetime(f["date"], utc=True).dt.floor("h")
    f = f.drop_duplicates("date").set_index("date")["funding_rate"].sort_index().astype(float)
    return d, f


def daily_perp_dvol():
    """Daily perp dollar volume for ALL perps (the ranking universe includes perps without spot)."""
    out = {}
    for f in OLD.glob("*-1h-futures.feather"):
        c = f.name.split("_USDT_USDT")[0]
        d = pd.read_feather(f, columns=["date", "close", "volume"]).set_index("date")
        e = _read(DATA / "perp_ext" / f"{c}-1h.feather")
        if e is not None and len(e):
            d = pd.concat([e.set_index("date")[["close", "volume"]], d])
        d = d[~d.index.duplicated()].sort_index()
        out[c] = (d["close"] * d["volume"]).resample("1D").sum()
    dv = pd.DataFrame(out).sort_index()
    return dv.rolling(30, min_periods=20).mean()


@dataclass
class Coin:
    name: str
    off: int                      # global hour index of row 0
    po: np.ndarray
    ph: np.ndarray
    pc: np.ndarray
    so: np.ndarray
    sc: np.ndarray
    sh: np.ndarray
    ok: np.ndarray                # both markets have a real bar within the last 3 hours
    stamp: np.ndarray             # funding settled at this hour
    rate: np.ndarray              # raw rate at that stamp (0 elsewhere)
    rate8: np.ndarray             # last 8h-equivalent rate known at this hour (incl. a stamp at this hour)
    mean24: np.ndarray            # mean 8h-equivalent rate of stamps in the last 24h
    elig: np.ndarray              # top-N perp and spot liquid (yesterday's values)
    top_slip: np.ndarray          # rank <= top_slip_rank


def build(p: Params, verbose=True):
    uni = pd.read_csv(DATA / "universe_candidates.csv")
    rank = daily_perp_dvol().rank(axis=1, ascending=False).shift(1)        # yesterday's rank
    gidx = pd.date_range(G0, G1, freq="h")
    coins = []
    for r in uni.itertuples():
        sf = DATA / "spot" / f"{r.spot}.feather"
        if not sf.exists() or r.perp not in rank.columns:
            continue
        s = pd.read_feather(sf)
        if len(s) < 24 * 40:
            continue
        s = s.drop_duplicates("date").set_index("date").sort_index()
        perp, fund = load_perp(r.perp)
        lo = max(perp.index[0], s.index[0], G0)
        hi = min(perp.index[-1], s.index[-1], G1)
        if hi - lo < pd.Timedelta(days=40):
            continue
        idx = pd.date_range(lo, hi, freq="h")
        pr = perp.reindex(idx)
        sp = s.reindex(idx)
        real = pr["close"].notna() & sp["close"].notna()
        ok = real.rolling(3, min_periods=1).max().astype(bool).values
        pr = pr.ffill()
        sp = sp.ffill()
        mult = float(r.mult)
        spot_dvol = (s["close"] * s["volume"]).resample("1D").sum().rolling(30, min_periods=20).mean().shift(1)
        # funding: 8h equivalent using the gap to the previous settlement
        f = fund[(fund.index >= lo - pd.Timedelta(days=2)) & (fund.index <= hi)]
        gap_h = f.index.to_series().diff().dt.total_seconds().div(3600).fillna(8).clip(1, 8)
        r8 = f * 8 / gap_h
        m24 = r8.rolling("24h").mean()
        fi = f.reindex(idx)
        stamp = fi.notna().values
        rate = fi.fillna(0).values
        rate8 = r8.reindex(idx.union(r8.index)).ffill().reindex(idx).values
        mean24 = m24.reindex(idx.union(m24.index)).ffill().reindex(idx).values
        day = idx.floor("D")
        rk = rank[r.perp].reindex(day).values
        sdv = spot_dvol.reindex(day).values
        elig = (rk <= p.top_n) & (sdv >= p.min_spot_dvol) & ok
        coins.append(Coin(r.perp, int((lo - G0) / HOUR), pr["open"].values, pr["high"].values, pr["close"].values,
                          sp["open"].values * mult, sp["close"].values * mult, sp["high"].values * mult, ok, stamp, rate,
                          np.nan_to_num(rate8), np.nan_to_num(mean24), np.nan_to_num(elig).astype(bool),
                          np.nan_to_num(rk, nan=999) <= p.top_slip_rank))
    if verbose:
        print(f"{len(coins)} coins with perp + spot data", flush=True)
    return coins, gidx


# ----------------------------------------------------------------------------------------------- simulation
@dataclass
class Pos:
    coin: Coin
    q: float
    p0: float
    margin: float
    entry_h: int
    s_entry: float
    p_entry: float
    funding: float = 0.0
    fees: float = 0.0
    derisks: int = 0
    outlay: float = 0.0
    exit_pending: bool = False
    log: dict = field(default_factory=dict)


def _slip(p, c, k):
    return p.slip_top if c.top_slip[k] else p.slip_other


def simulate(coins, gidx, p: Params):
    H = len(gidx)
    # entry candidates per global hour (decided at a settlement, executed next hour)
    cand = {}
    for c in coins:
        k = np.flatnonzero(c.stamp & c.elig & (c.rate8 >= p.entry_rate) & (c.mean24 >= p.entry_mean24))
        for kk in k:
            cand.setdefault(c.off + kk, []).append((c, kk))
    cash = p.capital
    pos: dict[str, Pos] = {}
    pending: list = []
    trades, eq_h = [], np.full(H, np.nan)
    tot = dict(funding_in=0.0, funding_out=0.0, fees=0.0, slippage=0.0, liquidations=0, derisks=0, liq_loss=0.0)

    def value(ps, k):
        c = ps.coin
        return ps.q * c.sc[k] + ps.margin + ps.q * (ps.p0 - c.pc[k])

    def close(ps, k, why, s_px, p_px):
        nonlocal cash
        c = ps.coin
        sl = _slip(p, c, k)
        s_eff, p_eff = s_px * (1 - sl), p_px * (1 + sl)
        fee = ps.q * s_eff * p.fee_spot + ps.q * p_eff * p.fee_perp
        perp_eq = ps.margin + ps.q * (ps.p0 - p_eff) if why != "liquidated" else 0.0
        inflow = ps.q * s_eff + perp_eq - fee
        cash += inflow
        tot["fees"] += fee
        tot["slippage"] += ps.q * (s_px - s_eff) + ps.q * (p_eff - p_px)
        ps.fees += fee
        trades.append(dict(coin=c.name, t_in=gidx[ps.entry_h], t_out=gidx[c.off + k], why=why,
                           days=(c.off + k - ps.entry_h) / 24, notional=ps.q * ps.p_entry, funding=ps.funding,
                           fees=ps.fees, derisks=ps.derisks, pnl=inflow - ps.outlay,
                           ret_pct=(inflow / ps.outlay - 1) * 100,
                           entry_rate8=ps.log.get("rate8"), basis_entry_pct=(ps.p_entry / ps.s_entry - 1) * 100))
        del pos[c.name]

    for g in range(H):
        # (a) executions at this hour's open
        for ps in [x for x in pos.values() if x.exit_pending]:
            k = min(g - ps.coin.off, len(ps.coin.po) - 1)
            close(ps, k, "funding_low", ps.coin.so[k], ps.coin.po[k])
        if pending:
            equity = cash + sum(value(x, g - 1 - x.coin.off) for x in pos.values())
            for c, kk in sorted(pending, key=lambda t: -t[0].rate8[t[1]]):
                k = kk + 1
                if len(pos) >= p.slots or c.name in pos or k >= len(c.po) or not c.ok[k]:
                    continue
                slot = min(equity / p.slots, cash)
                if slot <= 100:
                    continue
                sl = _slip(p, c, k)
                s_eff, p_eff = c.so[k] * (1 + sl), c.po[k] * (1 - sl)
                notional = slot / (1 + p.margin_ratio + p.fee_spot + p.fee_perp)
                q = notional / s_eff
                margin = p.margin_ratio * q * p_eff
                fee = q * s_eff * p.fee_spot + q * p_eff * p.fee_perp
                cash -= q * s_eff + margin + fee
                tot["fees"] += fee
                tot["slippage"] += q * (s_eff - c.so[k]) + q * (c.po[k] - p_eff)
                pos[c.name] = Pos(c, q, p_eff, margin, g, s_eff, p_eff, fees=fee, outlay=q * s_eff + margin + fee,
                                  log={"rate8": c.rate8[kk]})
            pending = []
        # (b) funding, (c) liquidation, (d) de-risk, (e) exit decisions
        for ps in list(pos.values()):
            c = ps.coin
            k = g - c.off
            if k >= len(c.pc):                                   # data ended (delisting): unwind at last price
                kl = len(c.pc) - 1
                close(ps, kl, "data_end", c.sc[kl] * 0.99, c.pc[kl] * 1.01)
                continue
            if c.stamp[k] and g > ps.entry_h:
                amt = ps.q * c.po[k] * c.rate[k]
                ps.margin += amt
                ps.funding += amt
                tot["funding_in" if amt > 0 else "funding_out"] += amt
            liq_px = (ps.margin + ps.q * ps.p0) / (ps.q * (1 + p.mmr))
            trig = c.ph[k] if p.liq_trigger == "last" else min(c.ph[k], c.sh[k] * c.pc[k] / c.sc[k])
            if trig >= liq_px:
                tot["liquidations"] += 1
                tot["liq_loss"] += ps.margin + ps.q * (ps.p0 - liq_px)
                # conservative: the spot is sold no higher than the liquidation level (in spot terms), so a jump
                # through the liquidation price never turns into a windfall on the unhedged spot
                close(ps, k, "liquidated", min(c.sc[k], liq_px * c.sc[k] / c.pc[k]), liq_px)
                continue
            perp_eq = ps.margin + ps.q * (ps.p0 - c.pc[k])
            if perp_eq < p.derisk_at * ps.q * c.pc[k]:
                E = value(ps, k)
                q2 = E / (1 + p.margin_ratio) / c.sc[k]
                dq = ps.q - q2
                sl = _slip(p, c, k)
                cost = dq * c.sc[k] * (p.fee_spot + sl) + dq * c.pc[k] * (p.fee_perp + sl)
                tot["fees"] += dq * c.sc[k] * p.fee_spot + dq * c.pc[k] * p.fee_perp
                tot["slippage"] += dq * (c.sc[k] + c.pc[k]) * sl
                tot["derisks"] += 1
                ps.fees += cost
                ps.derisks += 1
                ps.q, ps.p0 = q2, c.pc[k]
                ps.margin = E - cost - q2 * c.sc[k]
            if c.stamp[k] and c.rate8[k] < p.exit_rate:
                ps.exit_pending = True
        # (f) new entry decisions (stamps at this hour), executed next hour
        if g in cand:
            pending = [(c, kk) for c, kk in cand[g] if c.name not in pos]
        eq_h[g] = cash + sum(value(x, g - x.coin.off) for x in pos.values())
    eq = pd.Series(eq_h, index=gidx).ffill()
    return eq, pd.DataFrame(trades), tot


# ----------------------------------------------------------------------------------------------- metrics
def metrics(eq: pd.Series, p: Params, tot=None, trades=None) -> dict:
    d = eq.resample("1D").last().dropna()
    r = d.pct_change().dropna()
    years = (d.index[-1] - d.index[0]).days / 365.25
    cagr = (d.iloc[-1] / d.iloc[0]) ** (1 / years) - 1
    dd = d / d.cummax() - 1
    rf_d = (1 + p.usdc_apy) ** (1 / 365) - 1
    ex = r - rf_d
    downside = np.sqrt((np.minimum(r, 0) ** 2).mean())
    out = {"years": round(years, 2), "end_equity": round(d.iloc[-1], 0), "cagr_pct": round(cagr * 100, 2),
           "vol_pct": round(r.std() * np.sqrt(365) * 100, 2),
           "sharpe": round(r.mean() / r.std() * np.sqrt(365), 2) if r.std() > 0 else np.nan,
           "sharpe_vs_usdc": round(ex.mean() / ex.std() * np.sqrt(365), 2) if ex.std() > 0 else np.nan,
           "sortino": round(r.mean() / downside * np.sqrt(365), 2) if downside > 0 else np.nan,
           "max_dd_pct": round(dd.min() * 100, 2), "worst_day_pct": round(r.min() * 100, 2),
           "usdc_cagr_pct": round(p.usdc_apy * 100, 2), "excess_vs_usdc_pct": round((cagr - p.usdc_apy) * 100, 2)}
    if tot is not None:
        out.update({k: (round(v, 0) if isinstance(v, float) else v) for k, v in tot.items()})
    if trades is not None and len(trades):
        out.update({"pairs_opened": len(trades), "avg_hold_days": round(trades["days"].mean(), 1),
                    "median_hold_days": round(trades["days"].median(), 1)})
    return out


def by_year(eq):
    d = eq.resample("1D").last().dropna()
    out = {}
    for y, g in d.groupby(d.index.year):
        prev = d[d.index < g.index[0]]
        base = prev.iloc[-1] if len(prev) else g.iloc[0]
        dd = (g / g.cummax() - 1).min()
        out[int(y)] = {"return_pct": round((g.iloc[-1] / base - 1) * 100, 2), "max_dd_pct": round(dd * 100, 2)}
    return out


SQUEEZE_WINDOWS = {  # violent alt rallies, when funding is highest and shorts get squeezed
    "2021 alt season (Jan-May)": ("2021-01-01", "2021-05-31"),
    "May 2021 crash": ("2021-05-10", "2021-05-31"),
    "2021 Q4 top (Sep-Nov)": ("2021-09-01", "2021-11-30"),
    "2024 Q1 rally (Feb-Mar)": ("2024-02-01", "2024-03-31"),
    "2024 post-election (Nov-Dec)": ("2024-11-01", "2024-12-31"),
    "10 Oct 2025 crash week": ("2025-10-06", "2025-10-17"),
}


def windows(eq):
    out = {}
    for name, (a, b) in SQUEEZE_WINDOWS.items():
        e = eq[(eq.index >= pd.Timestamp(a, tz="UTC")) & (eq.index <= pd.Timestamp(b, tz="UTC") + pd.Timedelta(hours=23))]
        if len(e) < 2:
            continue
        out[name] = {"return_pct": round((e.iloc[-1] / e.iloc[0] - 1) * 100, 2),
                     "max_dd_pct_hourly": round((e / e.cummax() - 1).min() * 100, 2)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-only", action="store_true")
    a = ap.parse_args()
    base = Params()
    coins, gidx = build(base)
    RESULTS.mkdir(exist_ok=True)
    eq, tr, tot = simulate(coins, gidx, base)
    rep = {"params": base.__dict__, "base": metrics(eq, base, tot, tr), "by_year": by_year(eq),
           "squeeze_windows": windows(eq)}
    tr.to_csv(RESULTS / "trades_base.csv", index=False)
    eq.resample("1D").last().to_csv(RESULTS / "equity_base.csv")
    print(json.dumps(rep, indent=1, default=str), flush=True)
    if not a.base_only:
        scen = {
            "entry 0.01%/8h": replace(base, entry_rate=0.0001, entry_mean24=0.0001),
            "entry 0.02%/8h": replace(base, entry_rate=0.0002, entry_mean24=0.0002),
            "entry 0.05%/8h": replace(base, entry_rate=0.0005, entry_mean24=0.0005),
            "no 24h persistence filter": replace(base, entry_mean24=-1),
            "maker fees (perp 0.02%, spot 0.075% BNB), no slippage": replace(base, fee_perp=0.0002, fee_spot=0.00075,
                                                                           slip_top=0.0, slip_other=0.0),
            "5 pairs": replace(base, slots=5),
            "20 pairs": replace(base, slots=20),
            "margin 100% (1x short, no liquidation risk)": replace(base, margin_ratio=1.0, derisk_at=0.30),
            "margin 20% (aggressive, survives ~18%)": replace(base, margin_ratio=0.20, derisk_at=0.08),
            "top 30 coins only": replace(base, top_n=30),
        }
        scen["liquidation on mark-price proxy (spot high must reach it too)"] = replace(base, liq_trigger="mark_proxy")
        rows = {"base": rep["base"]}
        for name, pp in scen.items():
            cs = coins
            if (pp.top_n, pp.min_spot_dvol, pp.top_slip_rank) != (base.top_n, base.min_spot_dvol, base.top_slip_rank):
                cs, _ = build(pp, verbose=False)            # universe settings change the eligibility arrays
            e, t, tt = simulate(cs, gidx, pp)
            rows[name] = metrics(e, pp, tt, t)
            print(name, {k: rows[name][k] for k in ("cagr_pct", "sharpe", "max_dd_pct", "liquidations")}, flush=True)
        rep["sensitivity"] = rows
        cols = ["cagr_pct", "sharpe", "sharpe_vs_usdc", "sortino", "max_dd_pct", "worst_day_pct", "pairs_opened",
                "avg_hold_days", "liquidations", "derisks"]
        print(pd.DataFrame(rows).T[cols].to_string())
    (RESULTS / "report.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
