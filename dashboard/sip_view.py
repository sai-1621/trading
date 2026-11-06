"""The Index ETF SIP tab, shared by the local dashboard (dashboard.py) and the shareable page (build_share.py).
Uses the page's own CSS classes and a few tokens: --up, --down, --mute/--fg, --line, --accent or --acc."""

SIP_JS = r"""
function sipView(b) {
  const c = b.cur, fmt = v => v == null ? '–' : (v < 0 ? '−' : '') + c + Math.abs(v).toLocaleString('en-IN', {maximumFractionDigits: 0});
  const pctf = v => v == null ? '–' : (v > 0 ? '+' : '') + v + '%';
  const cl = v => v > 0 ? 'up' : v < 0 ? 'down' : '';
  const E = s => String(s ?? '').replace(/[&<>"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch]));
  const tbl = (head, rows, empty) => rows.length ? `<div class="tbl wrap"><table><tr>${head.map(h => `<th>${h}</th>`).join('')}</tr>${rows.map(r => `<tr>${r.map(x => `<td>${x}</td>`).join('')}</tr>`).join('')}</table></div>` : `<div class="empty">${empty}</div>`;
  const gain = b.value - b.invested, set = b.settings || {}, BT = b.backtest || {}, SC = BT.scenarios || {};
  const live = (set.LIVE_TRADING || '').toLowerCase() === 'true';
  let h = `<p class="note">${E(b.hours)}. ${live ? 'LIVE: real orders.' : 'Paper mode: no real orders.'} Plan: ${E(set.SIP_AMOUNT ? c + Number(set.SIP_AMOUNT).toLocaleString('en-IN') : '')} a month into ${E((set.ETFS || '').replace(/,/g, ', ').replace(/:/g, ' '))}${set.ETFS && !set.ETFS.includes('%') ? '%' : ''}.</p>
  <div class="tiles cards">
    <div class="tile card"><div class="k">Invested</div><div class="v"><b>${fmt(b.invested)}</b></div></div>
    <div class="tile card"><div class="k">Worth now</div><div class="v"><b>${fmt(b.value)}</b></div></div>
    <div class="tile card"><div class="k">Gain</div><div class="v ${cl(gain)}"><b class="${cl(gain)}">${fmt(gain)}</b></div></div>
    <div class="tile card"><div class="k">Yearly return (XIRR)</div><div class="v"><b>${b.xirr_pct == null ? '–' : pctf(b.xirr_pct)}</b></div><div class="s mute">needs a few months</div></div>
    <div class="tile card"><div class="k">Charges paid</div><div class="v"><b>${fmt(b.charges)}</b></div><div class="s mute">cash waiting ${fmt(b.cash)}</div></div>
  </div>`;
  h += `<section><h2>Holdings</h2>${tbl(['ETF', 'Units'], Object.entries(b.holdings).map(([e, u]) => [E(e), u]), 'Nothing bought yet. The first SIP runs on the next SIP day after 09:45 IST.')}</section>`;
  h += `<section><h2>Purchases</h2>${tbl(['Date', 'ETF', 'Units', 'Price', 'Amount', 'Charges'], b.purchases.map(p => [new Date(p.time).toLocaleDateString('en-IN', {day: 'numeric', month: 'short', year: 'numeric'}), E(p.etf), p.units, c + p.price, fmt(p.value), c + p.charges]), 'No purchases yet.')}</section>`;
  if (Object.keys(SC).length) {
    const base = SC.nifty, amt = BT.sip_amount;
    h += `<section><h2>Backtest: ${c}${Number(amt).toLocaleString('en-IN')} a month since January 2016</h2><p class="sub mute">Same rules as the bot: whole units at each month's first open, after charges, before tax, nothing ever sold.</p>
      ${tbl(['Portfolio', 'Invested', 'Worth now', 'Yearly return (XIRR)', 'Investments’ CAGR', 'Worst fall'],
        Object.values(SC).map(s => [E(s.name), fmt(s.invested), fmt(s.value), `<span class="${cl(s.xirr_pct)}">${pctf(s.xirr_pct)}</span>`, pctf(s.cagr_pct), s.worst_fall_pct + '%']), '')}
      <div class="card" style="margin-top:10px">${sipChart(Object.values(SC).slice(0, 3), base)}</div></section>`;
    const sy = Object.entries(BT.start_years || {});
    h += `<section><h2>When you start matters</h2><p class="sub mute">The NIFTY 50 SIP started in different years, valued today. Recent starts show low or negative returns because the market has fallen this year; SIPs need years.</p>
      ${tbl(['Started', 'Invested', 'Worth now', 'Yearly return (XIRR)'], sy.map(([y, r]) => [y, fmt(r.invested), fmt(r.value), `<span class="${cl(r.xirr_pct)}">${pctf(r.xirr_pct)}</span>`]), '')}</section>`;
  }
  return h;
}
function sipChart(scen, base) {
  const W = 900, H = 260, m = {t: 12, r: 12, b: 24, l: 64};
  const cs = getComputedStyle(document.documentElement);
  const tok = (...n) => { for (const x of n) { const v = cs.getPropertyValue(x).trim(); if (v) return v; } return '#888'; };
  const colors = [tok('--accent', '--acc'), tok('--up'), tok('--warn', '--down')], mute = tok('--mute'), line = tok('--line');
  const all = scen.flatMap(s => s.curve.map(p => p[2])).concat(base.curve.map(p => p[1]));
  const hi = Math.max(...all) * 1.05, n = base.curve.length;
  const x = i => m.l + i / (n - 1) * (W - m.l - m.r), y = v => m.t + (1 - v / hi) * (H - m.t - m.b);
  const ticks = [0.25, 0.5, 0.75, 1].map(f => Math.round(hi * f / 100000) * 100000).filter(v => v > 0);
  let s = `<svg width="100%" viewBox="0 0 ${W} ${H}" role="img" aria-label="Value of the SIP vs money put in">`;
  ticks.forEach(v => s += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}" stroke="${line}"/><text x="${m.l - 6}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="${mute}">${(v / 100000).toFixed(1)}L</text>`);
  base.curve.forEach((p, i) => { if (p[0].slice(5, 7) === '01' && Number(p[0].slice(0, 4)) % 2 === 0) s +=`<text x="${x(i)}" y="${H - 6}" text-anchor="middle" font-size="11" fill="${mute}">${p[0].slice(0, 4)}</text>`; });
  s += `<path d="${base.curve.map((p, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ',' + y(p[1]).toFixed(1)).join('')}" fill="none" stroke="${mute}" stroke-width="2" stroke-dasharray="5 4"/>`;
  scen.forEach((sc, k) => s += `<path d="${sc.curve.map((p, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ',' + y(p[2]).toFixed(1)).join('')}" fill="none" stroke="${colors[k]}" stroke-width="2" stroke-linejoin="round"/>`);
  s += `</svg><div class="legend mute" style="display:flex;flex-wrap:wrap;gap:6px 16px;font-size:12px"><span>- - money put in</span>${scen.map((sc, k) => `<span><i style="display:inline-block;width:14px;height:3px;background:${colors[k]};vertical-align:middle;margin-right:6px"></i>${sc.name}</span>`).join('')}</div>`;
  return s;
}
"""
