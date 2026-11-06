"""Builds results/strategy-lab.html from the results/*.json files written by lab.py."""
import json
from html import escape
from pathlib import Path

HERE = Path(__file__).resolve().parent
R = HERE / "results"
MARKETS = [("nse", "NSE stocks", "Angel One intraday charges, ₹40,000 max position"),
           ("us", "US stocks", "IBKR commission and fees, $4,000 max position"),
           ("crypto-inr", "Crypto, INR markets", "CoinDCX 0.59% fee per side"),
           ("crypto-usdt", "Crypto, USDT markets", "CoinDCX 0.20% fee per side")]
BIG = [("nse", "NSE stocks", "₹10 lakh positions"), ("us", "US stocks", "$100,000 positions")]
STRATS = ["VWAP pullback", "Order block"]


def load(name):
    return json.loads((R / f"{name}.json").read_text())


def num(v, plus=True):
    if v is None:
        return "–"
    s = f"{v:+.2f}" if plus else f"{v:.2f}"
    return s.replace("-", "−")


def cls(v):
    return "" if v is None else ("gain" if v > 0 else "loss" if v < 0 else "")


def r(v):
    return f'<span class="r {cls(v)}">{num(v)}R</span>'


def describe(name, side, p):
    side_w = "Buy (long)" if side == "long" else "Sell short"
    trend = "only with the daily trend" if p.get("trend") else "any daily trend"
    if name == "VWAP pullback":
        stop = "stop under the dip bar" if p["stop"] == "bar-low" else "stop 1 ATR under VWAP"
        return f"{side_w}; {p['k']} bars on the right side of VWAP before the dip; {stop}; target {p['target']:g}R; {trend}"
    zone = "candle's full range" if p["zone"] == "wick" else "candle's body"
    fvg = "fair value gap required" if p["fvg"] else "no gap required"
    tgt = "target the move's high" if p["target"] == "leg" else f"target {p['target']:g}R"
    return f"{side_w}; zone = {zone}; {fvg}; retest within {p['wait']} bars; {tgt}; {trend}"


def dumbbell(rows):
    """rows: [(group, label, gross, net)] -> SVG, gross (hollow) to net (filled), on one R scale."""
    lo, hi = -1.6, 0.4
    W, left, right, rowh, top = 760, 230, 24, 26, 34
    groups = []
    for g, *_ in rows:
        if g not in groups:
            groups.append(g)
    H = top + len(rows) * rowh + len(groups) * 24 + 26
    x = lambda v: left + (max(lo, min(hi, v)) - lo) / (hi - lo) * (W - left - right)
    s = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="R per trade before and after charges, check days">']
    for t in [-1.5, -1.0, -0.5, 0, 0.25]:
        s.append(f'<line x1="{x(t):.1f}" x2="{x(t):.1f}" y1="{top - 8}" y2="{H - 22}" class="{"zero" if t == 0 else "grid"}"/>')
        s.append(f'<text x="{x(t):.1f}" y="{H - 6}" text-anchor="middle" class="tick">{num(t) if t else "0"}R</text>')
    s.append(f'<text x="{x(0) + 6:.1f}" y="{top - 14}" class="tick">break-even</text>')
    y = top
    for g in groups:
        y += 18
        s.append(f'<text x="0" y="{y}" class="grp">{escape(g)}</text>')
        y += 6
        for gg, label, gross, net in rows:
            if gg != g:
                continue
            cy = y + rowh / 2
            s.append(f'<text x="14" y="{cy + 4:.1f}" class="lbl">{escape(label)}</text>')
            s.append(f'<line x1="{x(gross):.1f}" x2="{x(net):.1f}" y1="{cy:.1f}" y2="{cy:.1f}" class="cost"/>')
            s.append(f'<circle cx="{x(gross):.1f}" cy="{cy:.1f}" r="5" class="gross"><title>before charges {num(gross)}R</title></circle>')
            s.append(f'<circle cx="{x(net):.1f}" cy="{cy:.1f}" r="5.5" class="net"><title>after charges {num(net)}R</title></circle>')
            clip = " ◂" if net < lo else ""
            s.append(f'<text x="{x(net) - 9:.1f}" y="{cy + 4:.1f}" text-anchor="end" class="val">{num(net)}{clip}</text>')
            y += rowh
    s.append("</svg>")
    return "".join(s)


def main():
    data = {m: load(m) for m, *_ in MARKETS}
    big = {m: load(f"big/{m}") for m, *_ in BIG}
    combos = sum(s["combinations"] for d in data.values() for s in d["strategies"].values())
    positive = sum(s["check_positive"] for d in data.values() for s in d["strategies"].values())
    trades = sum(s["best"]["pick"]["trades"] + s["best"]["check"]["trades"] for d in data.values() for s in d["strategies"].values())

    rows = []
    for m, title, _ in MARKETS:
        d = data[m]
        b = d["baseline_orb"]["check"]
        rows.append((title, "Current breakout bot", b["avg_r"], b["avg_r_net"]))
        for n in STRATS:
            c = d["strategies"][n]["best"]["check"]
            rows.append((title, n, c["avg_r"], c["avg_r_net"]))

    tables = []
    for m, title, costs in MARKETS:
        d = data[m]
        b = d["baseline_orb"]
        trs = [f'<tr class="base"><td><b>Current breakout bot</b><div class="set">Opening-range breakout, as running now</div></td>'
               f'<td class="n">{b["pick"]["trades"]}</td><td class="n">{r(b["pick"]["avg_r"])}</td><td class="n">{r(b["pick"]["avg_r_net"])}</td>'
               f'<td class="n">{b["check"]["trades"]}</td><td class="n">{r(b["check"]["avg_r"])}</td><td class="n">{r(b["check"]["avg_r_net"])}</td>'
               f'<td class="n">–</td><td class="n">–</td></tr>']
        for n in STRATS:
            s = d["strategies"][n]
            bb, tm = s["best"], s["best_with_time_machine"]
            tm_txt = (f'{tm["trades"]} kept, {r(tm["avg_r_net"])}' if tm and tm["trades"] else "0 kept")
            trs.append(f'<tr><td><b>{n}</b><div class="set">{escape(describe(n, bb["side"], bb["params"]))}</div></td>'
                       f'<td class="n">{bb["pick"]["trades"]}</td><td class="n">{r(bb["pick"]["avg_r"])}</td><td class="n">{r(bb["pick"]["avg_r_net"])}</td>'
                       f'<td class="n">{bb["check"]["trades"]}</td><td class="n">{r(bb["check"]["avg_r"])}</td><td class="n">{r(bb["check"]["avg_r_net"])}</td>'
                       f'<td class="n tm">{tm_txt}</td><td class="n"><span class="pill {"ok" if s["check_positive"] else "no"}">{s["check_positive"]} of {s["check_judged"]}</span></td></tr>')
        tables.append(f'''<section class="mkt" id="{m}">
  <header><h3>{title}</h3><p>{d["symbols"]} {"coins" if "crypto" in m else "stocks"} · {d["days"][0]} to {d["days"][1]} · pick {d["pick_days"]} days, check {d["check_days"]} days · {costs}</p></header>
  <div class="scroll"><table>
    <thead><tr><th rowspan="2">Strategy and best settings</th><th colspan="3">Pick days (settings chosen here)</th><th colspan="3">Check days (unseen)</th><th rowspan="2">With Time Machine, check days</th><th rowspan="2">Settings that made money, check days</th></tr>
    <tr><th>Trades</th><th>Before charges</th><th>After</th><th>Trades</th><th>Before charges</th><th>After</th></tr></thead>
    <tbody>{"".join(trs)}</tbody></table></div></section>''')

    big_rows = []
    for m, title, size in BIG:
        d = big[m]
        b = d["baseline_orb"]["check"]
        cells = [f'<td>{title}<div class="set">{size}</div></td>', f'<td class="n">{r(b["avg_r_net"])}</td>']
        for n in STRATS:
            s = d["strategies"][n]
            c, p = s["best"]["check"], s["best"]["pick"]
            cells.append(f'<td class="n">{r(p["avg_r_net"])} → {r(c["avg_r_net"])}<div class="set">{s["check_positive"]} of {s["check_judged"]} settings positive</div></td>')
        big_rows.append(f"<tr>{''.join(cells)}</tr>")

    nse_big = big["nse"]["strategies"]["VWAP pullback"]
    html = f'''<title>Order Blocks vs VWAP</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700&family=Source+Sans+3:wght@400;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* Layout: one reading column, a verdict up top, one chart of before/after charges, then a ledger per market. */
:root {{
  --bg: #f6f7f9; --surface: #ffffff; --fg: #17202b; --mute: #5d6878; --line: #dde2ea;
  --ink: #2f5d8a; --gain: #1d7a4f; --loss: #b23a3a; --loss-soft: #f6e3e3; --gain-soft: #e1f2e9;
  --display: "Bricolage Grotesque", "Segoe UI", system-ui, sans-serif;
  --body: "Source Sans 3", "Segoe UI", system-ui, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, Consolas, monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg: #0f141b; --surface: #161d27; --fg: #e4e9f0; --mute: #95a1b2; --line: #2a3442;
  --ink: #8db7e0; --gain: #5cc493; --loss: #f07f7f; --loss-soft: #3a1f23; --gain-soft: #17352a; color-scheme: dark }} }}
:root[data-theme="dark"] {{
  --bg: #0f141b; --surface: #161d27; --fg: #e4e9f0; --mute: #95a1b2; --line: #2a3442;
  --ink: #8db7e0; --gain: #5cc493; --loss: #f07f7f; --loss-soft: #3a1f23; --gain-soft: #17352a; color-scheme: dark }}
body {{ background: var(--bg); color: var(--fg); font: 16px/1.55 var(--body); }}
.wrap {{ max-width: 1000px; margin: 0 auto; padding-block: 40px 64px; padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); display: grid; gap: 40px; }}
.eyebrow {{ font: 500 12px/1 var(--mono); letter-spacing: .08em; text-transform: uppercase; color: var(--mute); }}
h1 {{ font: 700 clamp(32px, 5vw, 48px)/1.05 var(--display); margin: 10px 0 14px; text-wrap: balance; letter-spacing: -.01em; }}
h2 {{ font: 700 24px/1.2 var(--display); margin: 0 0 6px; text-wrap: balance; }}
h3 {{ font: 600 19px/1.2 var(--display); margin: 0; }}
p {{ margin: 0; max-width: 68ch; }}
.lede {{ font-size: 19px; }}
.mute, header p, .sub {{ color: var(--mute); }}
.facts {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-top: 22px; }}
.fact {{ background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 14px 16px; }}
.fact b {{ display: block; font: 600 28px/1.1 var(--mono); font-variant-numeric: tabular-nums; }}
.fact span {{ color: var(--mute); font-size: 14px; }}
.fact.bad b {{ color: var(--loss); }}
figure {{ margin: 0; background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 18px 18px 10px; overflow-x: auto; }}
figure svg {{ width: 100%; min-width: 600px; height: auto; display: block; }}
.legend {{ display: flex; flex-wrap: wrap; gap: 6px 20px; font-size: 14px; color: var(--mute); margin: 4px 0 10px; }}
.legend i {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; vertical-align: -1px; }}
.grid {{ stroke: var(--line); }} .zero {{ stroke: var(--fg); stroke-width: 1.5; }}
.tick {{ fill: var(--mute); font: 12px var(--mono); }}
.grp {{ fill: var(--fg); font: 600 14px var(--body); }}
.lbl {{ fill: var(--fg); font: 14px var(--body); }}
.val {{ fill: var(--loss); font: 500 12px var(--mono); }}
.cost {{ stroke: var(--loss); stroke-width: 2; opacity: .45; }}
.gross {{ fill: var(--surface); stroke: var(--ink); stroke-width: 2; }}
.net {{ fill: var(--loss); }}
.mkt {{ display: grid; gap: 10px; }}
.mkt header p {{ font-size: 14px; }}
.scroll {{ overflow-x: auto; background: var(--surface); border: 1px solid var(--line); border-radius: 6px; }}
table {{ border-collapse: collapse; width: 100%; min-width: 820px; font-size: 15px; }}
th {{ font: 600 12px/1.3 var(--body); letter-spacing: .03em; text-transform: uppercase; color: var(--mute); text-align: right; padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: bottom; }}
th:first-child {{ text-align: left; }}
td {{ padding: 12px; border-bottom: 1px solid var(--line); vertical-align: top; }}
tbody tr:last-child td {{ border-bottom: 0; }}
tr.base td {{ background: color-mix(in srgb, var(--bg) 60%, transparent); }}
td.n {{ text-align: right; font-family: var(--mono); font-variant-numeric: tabular-nums; white-space: nowrap; }}
td.tm {{ white-space: normal; min-width: 110px; }}
.set {{ font: 13px/1.4 var(--body); color: var(--mute); margin-top: 3px; max-width: 46ch; white-space: normal; }}
.r.gain {{ color: var(--gain); }} .r.loss {{ color: var(--loss); }}
.pill {{ display: inline-block; font: 500 13px var(--mono); padding: 2px 8px; border-radius: 99px; }}
.pill.no {{ background: var(--loss-soft); color: var(--loss); }} .pill.ok {{ background: var(--gain-soft); color: var(--gain); }}
.two {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }}
.card {{ background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 18px; display: grid; gap: 8px; align-content: start; }}
.card ol {{ margin: 0; padding-left: 20px; display: grid; gap: 4px; }}
.notes {{ display: grid; gap: 10px; padding-left: 20px; margin: 0; max-width: 72ch; }}
code {{ font: 14px var(--mono); }}
</style>
<div class="wrap">
  <section>
    <div class="eyebrow">Strategy lab · run 27 Oct 2026 · paper research, no real orders</div>
    <h1>Order Blocks vs VWAP</h1>
    <p class="lede">Neither strategy made money after charges in any market. Before charges both sit close to zero R per trade, so there is no edge for the charges to come out of. Neither has been added to the bots.</p>
    <div class="facts">
      <div class="fact"><b>{combos}</b><span>setting combinations tested across 4 markets</span></div>
      <div class="fact bad"><b>{positive}</b><span>made money on the unseen check days at the bots' position sizes</span></div>
      <div class="fact"><b>{trades:,}</b><span>simulated trades for the best settings alone</span></div>
      <div class="fact"><b>1 R</b><span>= the amount risked on a trade (entry to stop)</span></div>
    </div>
  </section>

  <section>
    <h2>Before and after charges, on the unseen days</h2>
    <p class="sub">Best setting of each strategy (chosen on the pick days), measured on the check days. The gap between the dots is what charges and the bid/ask spread cost per trade.</p>
    <figure>
      <div class="legend"><span><i style="background:var(--surface);border:2px solid var(--ink)"></i>before charges</span><span><i style="background:var(--loss)"></i>after charges</span><span>◂ = off the scale</span></div>
      {dumbbell(rows)}
    </figure>
  </section>

  <section style="display:grid;gap:28px">
    <div><h2>Market by market</h2><p class="sub">R per trade, averaged. Settings were picked on the earlier days only; the check days played no part in choosing them. "With Time Machine" re-runs the best setting through the bots' 10,000-replay gate (1,000 replays here).</p></div>
    {"".join(tables)}
  </section>

  <section style="display:grid;gap:12px">
    <h2>With much bigger positions</h2>
    <p class="sub">Fixed brokerage weighs less on large trades, so this re-run shows the strategies at their cheapest. Pick days → check days, R per trade after charges.</p>
    <div class="scroll"><table style="min-width:640px">
      <thead><tr><th>Market</th><th>Current breakout bot, check</th><th>VWAP pullback</th><th>Order block</th></tr></thead>
      <tbody>{"".join(big_rows)}</tbody></table></div>
    <p>The one positive number, VWAP pullback <b>short</b> on NSE ({num(nse_big["best"]["check"]["avg_r_net"])}R on the check days), lost {num(nse_big["best"]["pick"]["avg_r_net"])}R on the pick days first. The check days were a falling month, which favours short trades. That is the market's direction, not an edge, and it needs about ₹10 lakh of capital per trade.</p>
  </section>

  <section class="two">
    <div class="card"><h3>Order block rules tested</h3><ol>
      <li>Swing high: a bar higher than the two bars either side.</li>
      <li>Break of structure: a close above the latest swing high.</li>
      <li>Order block: the last down candle before that up-move; the zone runs from its high (or body) to its low.</li>
      <li>Optional: the move must leave a fair value gap.</li>
      <li>Buy with a limit order when price comes back into the zone; stop just under the zone; target 1R, 2R or the move's high.</li>
    </ol><p class="mute">Shorts use the mirror image. One trade per stock per day; out by the bot's flat-by time.</p></div>
    <div class="card"><h3>VWAP pullback rules tested</h3><ol>
      <li>VWAP: the day's volume-weighted average price, from the open.</li>
      <li>Trend: the last 3 or 6 bars closed above a rising VWAP.</li>
      <li>Signal: a green bar that dips to VWAP and closes back above it.</li>
      <li>Buy at that bar's close; stop under the dip bar or 1 ATR under VWAP; target 1R, 1.5R or 2R.</li>
    </ol><p class="mute">Shorts use the mirror image. Both strategies were also tested with and without the bots' 20-day trend filter.</p></div>
  </section>

  <section style="display:grid;gap:10px">
    <h2>Limits of this test</h2>
    <ul class="notes">
      <li>Free 5-minute stock data only goes back 60 days, so each stock period is about 2–3 months; crypto uses 4 months of 15-minute bars.</li>
      <li>There is no single agreed definition of an order block. This is a common Smart Money Concepts version; other versions could differ, but a near-zero result before charges suggests the core idea adds little.</li>
      <li>Crypto INR order blocks were positive before charges ({num(data["crypto-inr"]["strategies"]["Order block"]["best"]["check"]["avg_r"])}R) on 99 trades, but the same rule on USDT markets was not, and the 0.59% fee per side wipes it out either way.</li>
      <li>Short selling was tested for stocks only. CoinDCX spot does not allow it.</li>
    </ul>
    <p class="mute">Source: <code>begin\\strategy_lab\\lab.py</code>, results in <code>strategy_lab\\results\\</code>. The index ETF SIP remains the only approach that made money in these tests.</p>
  </section>
</div>
'''
    out = R / "strategy-lab.html"
    out.write_text(html, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
