"""Turns results/results.json into a self-contained results page: results/backtest-2016.html.
    python build_page.py
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def build():
    data = json.loads((HERE / "results" / "results.json").read_text(encoding="utf-8"))
    out = HERE / "results" / "backtest-2016.html"
    out.write_text(TEMPLATE.replace("__DATA__", json.dumps(data).replace("</", "<\\/")), encoding="utf-8")
    return out


TEMPLATE = r"""<title>Strategy Backtest 2016–2026</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* Layout: a research report. Verdict first, then growth chart, then the full strategy ledger, years, and the trained bot's choices. */
:root{
  --bg:#f4f5f7; --panel:#ffffff; --fg:#1a1d24; --mute:#5d6573; --line:#dde1e8; --chip:#eceff4;
  --accent:#2f5bd3; --accent-fg:#ffffff; --up:#137a4f; --down:#b3261e;
  --s1:#2f5bd3; --s2:#d9822b; --s3:#7a4fc9; --s4:#137a4f; --s5:#8a93a3;
  --sans:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif; --mono:"IBM Plex Mono",ui-monospace,Consolas,monospace;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#101318; --panel:#171b22; --fg:#e3e7ee; --mute:#97a0ae; --line:#2a303a; --chip:#222833;
  --accent:#86a5ff; --accent-fg:#0e1526; --up:#5fd0a0; --down:#ff8a80;
  --s1:#86a5ff; --s2:#f0a85a; --s3:#b79cf2; --s4:#5fd0a0; --s5:#7d8696; color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#101318; --panel:#171b22; --fg:#e3e7ee; --mute:#97a0ae; --line:#2a303a; --chip:#222833;
  --accent:#86a5ff; --accent-fg:#0e1526; --up:#5fd0a0; --down:#ff8a80;
  --s1:#86a5ff; --s2:#f0a85a; --s3:#b79cf2; --s4:#5fd0a0; --s5:#7d8696; color-scheme:dark}
body{background:var(--bg);color:var(--fg);font-family:var(--sans);font-size:14px;line-height:1.5}
body::before{content:"";position:fixed;inset:0 0 auto;height:env(safe-area-inset-top,0px);z-index:10;background:var(--panel)}
.wrap{max-width:1180px;margin-inline:auto;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));padding-block:0 40px}
header{position:sticky;top:env(safe-area-inset-top,0px);z-index:5;background:var(--panel);border-bottom:1px solid var(--line)}
header .wrap{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;padding-block:12px}
h1{font-size:20px;font-weight:600;margin:0;text-wrap:balance}
.meta{color:var(--mute);font-size:13px}
.tabs{display:flex;gap:6px;flex-wrap:wrap}
.tab{font:inherit;border:1px solid var(--line);background:var(--panel);color:var(--fg);padding:6px 14px;border-radius:6px;cursor:pointer}
.tab[aria-selected="true"]{background:var(--accent);border-color:var(--accent);color:var(--accent-fg)}
.tab:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.verdict{margin:18px 0 0;padding:14px 16px;background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:8px;max-width:75ch}
.verdict b{font-weight:600}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,200px),1fr));gap:10px;margin:14px 0}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px;min-width:0}
.tile .k{color:var(--mute);font-size:12px}.tile .v{font-family:var(--mono);font-size:22px;font-weight:500;font-variant-numeric:tabular-nums}
.tile .s{color:var(--mute);font-size:12px}
section{margin-top:26px}
h2{font-size:16px;font-weight:600;margin:0 0 4px;text-wrap:balance}
.sub{color:var(--mute);font-size:13px;margin:0 0 10px;max-width:80ch}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px;min-width:0}
.chart{position:relative;width:100%;min-height:300px}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:12px;color:var(--mute);margin-top:6px}
.legend i{display:inline-block;width:14px;height:3px;border-radius:2px;vertical-align:middle;margin-right:6px}
.tip{position:absolute;pointer-events:none;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:12px;box-shadow:0 4px 14px rgba(0,0,0,.12);display:none;white-space:nowrap}
.tbl{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
tr:last-child td{border-bottom:none}
th{color:var(--mute);font-weight:500;font-size:12px}
td.n,th.n{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums}
tr.ref td{background:var(--chip)}
.up{color:var(--up)}.down{color:var(--down)}.mute{color:var(--mute)}
.pill{display:inline-block;font-size:12px;padding:1px 8px;border-radius:999px;background:var(--chip)}
.pill.ok{color:var(--up)}.pill.bad{color:var(--down)}
ul.notes{margin:0;padding-left:18px;color:var(--mute);font-size:13px;max-width:85ch}
ul.notes li{margin:4px 0}
</style>

<header><div class="wrap">
  <div><h1>Strategy Backtest 2016–2026</h1><div class="meta" id="meta"></div></div>
  <div class="tabs" role="tablist" id="tabs"></div>
</div></header>
<div class="wrap" id="main"></div>

<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const keys = Object.keys(D.markets);
let tab = (location.hash || '').slice(1);
if (!keys.includes(tab)) { try { tab = localStorage.getItem('bt-tab') || keys[0]; } catch (e) { tab = keys[0]; } if (!keys.includes(tab)) tab = keys[0]; }
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pct = (v, d = 1) => v == null ? '–' : (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(d) + '%';
const cls = v => v > 0 ? 'up' : v < 0 ? 'down' : '';
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

function lineChart(el, series) {
  const W = Math.max(320, el.clientWidth), H = 300, m = {t: 12, r: 12, b: 26, l: 46};
  const all = series.flatMap(s => s.pts.map(p => p[1]));
  const lo = Math.min(...all), hi = Math.max(...all);
  const t0 = new Date(series[0].pts[0][0]).getTime(), t1 = Math.max(...series.map(s => new Date(s.pts[s.pts.length - 1][0]).getTime()));
  const logLo = Math.log(lo * 0.95), logHi = Math.log(hi * 1.05);
  const x = t => m.l + (t - t0) / (t1 - t0) * (W - m.l - m.r);
  const y = v => m.t + (logHi - Math.log(v)) / (logHi - logLo) * (H - m.t - m.b);
  const ticks = [0.5, 0.75, 1, 1.5, 2, 3, 4, 6, 8, 12, 16].filter(v => v >= lo * 0.95 && v <= hi * 1.05);
  const years = []; for (let yr = new Date(t0).getFullYear() + 1; yr <= new Date(t1).getFullYear(); yr++) years.push(yr);
  const muted = css('--mute'), line = css('--line');
  let svg = `<svg width="100%" viewBox="0 0 ${W} ${H}" role="img" aria-label="Growth of 1 invested">`;
  ticks.forEach(v => { svg += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}" stroke="${line}" stroke-width="1"/><text x="${m.l - 6}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="${muted}">${v}×</text>`; });
  const step = years.length > 7 ? 2 : 1;
  years.filter((_, i) => i % step === 0).forEach(yr => { const t = new Date(yr, 0, 1).getTime(); svg += `<text x="${x(t)}" y="${H - 8}" text-anchor="middle" font-size="11" fill="${muted}">${yr}</text>`; });
  series.forEach(s => { svg += `<path d="${s.pts.map((p, i) => (i ? 'L' : 'M') + x(new Date(p[0]).getTime()).toFixed(1) + ',' + y(p[1]).toFixed(1)).join('')}" fill="none" stroke="${s.color}" stroke-width="${s.w || 2}" stroke-linecap="round" stroke-linejoin="round" ${s.dash ? 'stroke-dasharray="5 4"' : ''}/>`; });
  svg += `<line class="hover" x1="0" x2="0" y1="${m.t}" y2="${H - m.b}" stroke="${muted}" stroke-width="1" visibility="hidden"/></svg>`;
  el.innerHTML = svg + '<div class="tip"></div>';
  const tip = el.querySelector('.tip'), hl = el.querySelector('.hover'), sv = el.querySelector('svg');
  sv.addEventListener('mousemove', ev => {
    const r = sv.getBoundingClientRect(), px = (ev.clientX - r.left) * W / r.width;
    const t = t0 + (px - m.l) / (W - m.l - m.r) * (t1 - t0);
    if (px < m.l || px > W - m.r) { tip.style.display = 'none'; hl.setAttribute('visibility', 'hidden'); return; }
    const rows = series.map(s => { let best = s.pts[0]; for (const p of s.pts) { if (new Date(p[0]).getTime() <= t) best = p; else break; } return [s, best]; });
    const dt = new Date(rows[0][1][0]);
    tip.innerHTML = `<div class="mute">${dt.toLocaleDateString('en-IN', {month: 'short', year: 'numeric'})}</div>` + rows.map(([s, p]) => `<div><i style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${s.color};margin-right:6px"></i>${esc(s.name)}: ${p[1].toFixed(2)}×</div>`).join('');
    tip.style.display = 'block';
    const left = (ev.clientX - r.left) + 14, maxLeft = el.clientWidth - tip.offsetWidth - 4;
    tip.style.left = Math.max(4, Math.min(left, maxLeft)) + 'px'; tip.style.top = '10px';
    hl.setAttribute('x1', px); hl.setAttribute('x2', px); hl.setAttribute('visibility', 'visible');
  });
  sv.addEventListener('mouseleave', () => { tip.style.display = 'none'; hl.setAttribute('visibility', 'hidden'); });
}

function render() {
  document.getElementById('meta').textContent = `Daily strategies on today's index stocks · tested Jan 2016 to ${new Date(D.run_at).toLocaleDateString('en-IN', {month: 'short', year: 'numeric'})} · after costs, before tax`;
  document.getElementById('tabs').innerHTML = keys.map(k => `<button class="tab" role="tab" id="t-${k}" aria-selected="${k === tab}">${esc(D.markets[k].name)}</button>`).join('');
  keys.forEach(k => document.getElementById('t-' + k).onclick = () => { tab = k; try { localStorage.setItem('bt-tab', k); } catch (e) {} render(); });
  const M = D.markets[tab], S = M.strategies, hold = S.hold, T = M.trained;
  const ranked = Object.entries(S).sort((a, b) => (b[1].full.cagr_pct ?? -99) - (a[1].full.cagr_pct ?? -99));
  const beatHold = ranked.filter(([k, s]) => k !== 'hold' && s.full.cagr_pct > hold.full.cagr_pct);
  const betterRisk = ranked.filter(([k, s]) => k !== 'hold' && s.full.sharpe > hold.full.sharpe && s.full.max_dd_pct < hold.full.max_dd_pct);
  const t19 = T.stats, h19 = hold.since_2019;
  const bestOther = ranked.find(([k]) => k !== 'hold');
  let h = `<div class="verdict">${beatHold.length
      ? `<b>${beatHold.length} strategy${beatHold.length > 1 ? 'ies' : ''} beat buying and holding</b> over 2016–2026: ${beatHold.map(([, s]) => esc(s.label)).join(', ')}.`
      : `<b>No strategy beat simply buying and holding these stocks</b> over 2016–2026 (${pct(hold.full.cagr_pct)} a year).`}
    ${betterRisk.length ? ` ${betterRisk.length} gave better returns for the risk taken, with smaller falls: ${betterRisk.slice(0, 3).map(([, s]) => esc(s.label)).join(', ')}.` : ''}
    The trained bot made ${pct(t19.cagr_pct)} a year from 2019, against ${pct(h19.cagr_pct)} for buy-and-hold over the same years.</div>`;
  h += `<div class="tiles">
    <div class="tile"><div class="k">Buy and hold, every stock</div><div class="v">${pct(hold.full.cagr_pct)}</div><div class="s">a year since 2016 · worst fall ${hold.full.max_dd_pct}%</div></div>
    <div class="tile"><div class="k">Index (${esc(M.index_name)})</div><div class="v">${pct(M.index.cagr_pct)}</div><div class="s">a year since 2016 · worst fall ${M.index.max_dd_pct}%</div></div>
    <div class="tile"><div class="k">Best strategy: ${esc(bestOther[1].label)}</div><div class="v ${cls(bestOther[1].full.cagr_pct - hold.full.cagr_pct)}">${pct(bestOther[1].full.cagr_pct)}</div><div class="s">a year · worst fall ${bestOther[1].full.max_dd_pct}%</div></div>
    <div class="tile"><div class="k">Trained bot (from 2019)</div><div class="v ${cls(t19.cagr_pct - h19.cagr_pct)}">${pct(t19.cagr_pct)}</div><div class="s">a year · worst fall ${t19.max_dd_pct}% · hold ${pct(h19.cagr_pct)}</div></div>
  </div>`;
  h += `<section id="growth"><h2>Growth of 1 invested, since January 2016</h2><p class="sub">Equal amounts in every stock, rebalanced daily. Log scale, so equal slopes mean equal yearly returns.</p>
    <div class="card"><div class="chart" id="c1"></div><div class="legend" id="l1"></div></div></section>`;
  h += `<section id="trained"><h2>Trained bot vs buy-and-hold, since January 2019</h2><p class="sub">The bot learned on 2016–2018 which strategy (or cash) works in each market condition, then traded each quarter using only what it knew before that quarter.</p>
    <div class="card"><div class="chart" id="c2"></div><div class="legend" id="l2"></div></div></section>`;
  h += `<section id="ledger"><h2>Every strategy, January 2016 to today</h2><p class="sub">Per year (CAGR), total, worst fall from a peak, Sharpe ratio (return per unit of risk; higher is better), and how often each strategy is invested. Costs: ${esc(M.cost_note)}.</p><div class="tbl"><table>
    <thead><tr><th>Strategy</th><th class="n">Per year</th><th class="n">Total</th><th class="n">Worst fall</th><th class="n">Sharpe</th><th class="n">Invested</th><th class="n">Trades / stock / yr</th><th class="n">Stocks that made money</th><th>vs buy and hold</th></tr></thead><tbody>
    <tr class="ref"><td>Index (${esc(M.index_name)})</td><td class="n">${pct(M.index.cagr_pct)}</td><td class="n">${pct(M.index.total_pct, 0)}</td><td class="n">${M.index.max_dd_pct}%</td><td class="n">${M.index.sharpe}</td><td class="n">100%</td><td class="n">–</td><td class="n">–</td><td class="mute">reference</td></tr>
    ${ranked.map(([k, s]) => { const f = s.full; const verdict = k === 'hold' ? '<span class="mute">reference</span>'
        : f.cagr_pct > hold.full.cagr_pct ? '<span class="pill ok">Higher return</span>'
        : (f.sharpe > hold.full.sharpe && f.max_dd_pct < hold.full.max_dd_pct) ? '<span class="pill">Lower return, less risk</span>' : '<span class="pill bad">Worse</span>';
      return `<tr class="${k === 'hold' ? 'ref' : ''}"><td>${esc(s.label)}</td><td class="n ${cls(f.cagr_pct)}">${pct(f.cagr_pct)}</td><td class="n">${pct(f.total_pct, 0)}</td><td class="n">${f.max_dd_pct}%</td><td class="n">${f.sharpe ?? '–'}</td><td class="n">${s.time_in_market_pct}%</td><td class="n">${s.trades_per_stock_per_year}</td><td class="n">${s.stocks_profitable_pct}%</td><td>${verdict}</td></tr>`; }).join('')}
    </tbody></table></div></section>`;
  const yrs = Object.keys(hold.full.by_year);
  const cols = [['Index', M.index.by_year], ['Buy and hold', hold.full.by_year], ...ranked.filter(([k]) => k !== 'hold').slice(0, 4).map(([, s]) => [s.label, s.full.by_year]), ['Trained bot', T.stats.by_year]];
  h += `<section id="years"><h2>Year by year</h2><p class="sub">The four best strategies by yearly return, next to the benchmarks. The trained bot starts in 2019.</p><div class="tbl"><table>
    <thead><tr><th>Year</th>${cols.map(c => `<th class="n">${esc(c[0])}</th>`).join('')}</tr></thead><tbody>
    ${yrs.map(y => `<tr><td>${y}${y === String(new Date(D.run_at).getFullYear()) ? ' <span class="mute">(so far)</span>' : ''}</td>${cols.map(c => `<td class="n ${cls(c[1][y])}">${c[1][y] == null ? '–' : pct(c[1][y])}</td>`).join('')}</tr>`).join('')}
    </tbody></table></div></section>`;
  const names = {bull_calm: 'Bull, calm', bull_volatile: 'Bull, volatile', bear_calm: 'Bear, calm', bear_volatile: 'Bear, volatile'};
  const recent = T.choices.slice(-8).reverse();
  h += `<section id="choices"><h2>What the trained bot chose</h2><p class="sub">Market condition = the index above or below its 100-day average (bull or bear) and its volatility above or below the past year's median. Latest 8 quarters; days spent in each condition since 2019: ${Object.entries(T.regime_days).map(([r, n]) => `${names[r]} ${n}`).join(' · ')}.</p><div class="tbl"><table>
    <thead><tr><th>Quarter</th>${Object.values(names).map(n => `<th>${n}</th>`).join('')}</tr></thead><tbody>
    ${recent.map(q => `<tr><td>${esc(q.from)}</td>${Object.keys(names).map(r => `<td>${esc((S[q.pick[r]] || {}).label || (q.pick[r] === 'cash' ? 'Cash' : q.pick[r]))}</td>`).join('')}</tr>`).join('')}
    </tbody></table></div></section>`;
  h += `<section id="notes"><h2>How to read this</h2><ul class="notes">
    <li>Stocks are today's members of the index. Companies that dropped out since 2016 are missing, which makes every line here look better than reality (survivorship bias).</li>
    <li>Prices include dividends. A signal at the close is traded at the next day's open, paying ${esc(M.cost_note)} on each buy and sell. Tax is not included.</li>
    <li>The intraday opening-range strategy the live bots trade can't be tested this far back: free 5-minute data only covers the last 60 days.</li>
    <li>Past results, even honest ones, do not promise future returns.</li></ul></section>`;
  document.getElementById('main').innerHTML = h;

  const s1 = [{name: 'Index', pts: M.index.curve, color: css('--s5'), dash: true}, {name: 'Buy and hold', pts: hold.full.curve, color: css('--s1'), w: 2.5},
    ...ranked.filter(([k]) => k !== 'hold').slice(0, 2).map(([, s], i) => ({name: s.label, pts: s.full.curve, color: css(i ? '--s3' : '--s2')}))];
  const s2 = [{name: 'Index', pts: M.index_2019.curve, color: css('--s5'), dash: true}, {name: 'Buy and hold', pts: hold.since_2019.curve, color: css('--s1'), w: 2.5},
    {name: 'Trained bot', pts: T.stats.curve, color: css('--s4'), w: 2.5}];
  const draw = () => { lineChart(document.getElementById('c1'), s1); lineChart(document.getElementById('c2'), s2); };
  document.getElementById('l1').innerHTML = s1.map(s => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join('');
  document.getElementById('l2').innerHTML = s2.map(s => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join('');
  draw();
  window.onresize = () => { clearTimeout(window.__rz); window.__rz = setTimeout(draw, 150); };
}
render();
</script>
"""

if __name__ == "__main__":
    print("Wrote", build())
