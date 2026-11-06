"""Dashboard tab for the paper momentum swing bot (angelone_bot/swing_bot.py).
collect_swing() reads the bot's files (read-only); SWING_JS renders them with the page's own CSS classes."""
import json
import re
from pathlib import Path

CAPITAL = 100000.0
# research/swing_v2_momentum_results.json: the same rule on held-back data (Jan 2025 - Oct 2026), for comparison
BACKTEST = {"period": "Jan 2025 – Oct 2026 (held-back test)", "return_pct": 14.8, "nifty_pct": -2.9,
            "win_pct": 34.3, "max_dd_pct": -21.3, "avg_days": 29.8}


def _alive(pid_text: str) -> bool:
    m = re.search(r"\d+", pid_text or "")
    if not m:
        return False
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, int(m.group()))
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
    k32.CloseHandle(h)
    return bool(ok) and code.value == 259


def collect_swing(b: dict) -> dict:
    d: Path = b["dir"]
    try:
        st = json.loads((d / "trader" / "swing_paper.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    pid = (d / "swing.pid").read_text(encoding="utf-8").strip() if (d / "swing.pid").exists() else ""
    eq = st.get("equity") or []
    value = eq[-1][1] if eq else CAPITAL
    n0 = st.get("nifty_start")
    curve = [{"day": e[0], "value": e[1], "nifty": round(CAPITAL * e[2] / n0, 2) if (e[2] and n0) else None}
             for e in eq]
    peak, dd = CAPITAL, 0.0
    for e in eq:
        peak = max(peak, e[1])
        dd = min(dd, e[1] / peak - 1)
    nifty_now = next((e[2] for e in reversed(eq) if e[2]), None)
    closed = st.get("closed") or []
    wins = sum(1 for t in closed if t["pnl"] > 0)
    positions = []
    for sym, p in (st.get("positions") or {}).items():
        last = p.get("last", p["entry"])
        positions.append({"symbol": sym, "shares": p["shares"], "entry": round(p["entry"], 2), "since": p["entry_day"],
                          "last": round(last, 2), "value": round(p["shares"] * last, 2),
                          "pnl": round(p["shares"] * last - p["cost"], 2), "ret_pct": round((last / p["entry"] - 1) * 100, 2)})
    try:
        tail = (d / "swing.log").read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
    except OSError:
        tail = []
    return {"id": b["id"], "name": b["name"], "cur": b["cur"], "hours": b["hours"], "kind": "swing",
            "alive": _alive(pid), "started": pid, "has_account": bool(st), "start": st.get("start"),
            "last_day": st.get("last_day"), "updated": st.get("updated"), "data_source": st.get("data_source"),
            "failed": st.get("failed_symbols") or [],
            "summary": {"value": round(value, 2), "ret_pct": round((value / CAPITAL - 1) * 100, 2),
                        "nifty_pct": round((nifty_now / n0 - 1) * 100, 2) if (nifty_now and n0) else None,
                        "cash": round(st.get("cash", CAPITAL), 2), "open": len(positions), "closed": len(closed),
                        "wins": wins, "win_pct": round(wins / len(closed) * 100, 1) if closed else None,
                        "realized": round(sum(t["pnl"] for t in closed), 2), "max_dd_pct": round(dd * 100, 2),
                        "days": len(eq)},
            "positions": sorted(positions, key=lambda p: p["since"]), "pending": st.get("pending") or [],
            "closed": closed[::-1][:300], "skipped": (st.get("skipped") or [])[::-1][:50], "curve": curve,
            "backtest": BACKTEST, "log": tail}


SWING_JS = r"""
function swingView(b) {
  const c = b.cur, S = b.summary || {}, B = b.backtest || {};
  const F = (v, d = 0) => v == null || v === '' ? '–' : Number(v).toLocaleString('en-IN', {maximumFractionDigits: d, minimumFractionDigits: d});
  const R = v => v == null ? '–' : (Number(v) < 0 ? '−' : '') + c + F(Math.abs(Number(v)), 0);
  const P = v => v == null ? '–' : (v > 0 ? '+' : v < 0 ? '−' : '') + F(Math.abs(v), 1) + '%';
  const cl = v => v > 0 ? 'up' : v < 0 ? 'down' : '';
  const E = s => String(s ?? '').replace(/[&<>"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch]));
  const tbl = (head, rows, empty) => rows.length ? `<div class="wrap"><table><tr>${head.map(h => `<th>${h}</th>`).join('')}</tr>${rows.map(r => `<tr>${r.join('')}</tr>`).join('')}</table></div>` : `<div class="empty">${empty}</div>`;
  const td = (x, k = '') => `<td class="${k}">${x}</td>`;
  const state = b.alive ? '<span class="up">● running</span>' : '<span class="down">● stopped</span>';
  let h = `<div class="note">${E(b.hours)} · ${state}${b.started ? ' · ' + E(b.started) : ''}</div>
  <div class="note">Paper only, no orders. Forward test of the momentum rule: buy at the next open after a close above the 20-day high, sell at the next open after a close below the 20-day low. ₹1,00,000, at most 5 stocks of ₹20,000, delivery charges included. Judge it after 3–6 months, not days.</div>`;
  if (!b.has_account) return h + '<div class="empty">No paper account yet. The bot starts one after the first trading-day close (16:00 IST).</div>';
  h += `<div class="cards">
   <div class="card">Account value<b class="${cl(S.ret_pct)}">${R(S.value)}</b><span class="mute">${P(S.ret_pct)} since ${E(b.start)}</span></div>
   <div class="card">Nifty 50, same dates<b class="${cl(S.nifty_pct)}">${P(S.nifty_pct)}</b><span class="mute">${S.ret_pct != null && S.nifty_pct != null ? (S.ret_pct >= S.nifty_pct ? 'bot ahead by ' : 'bot behind by ') + F(Math.abs(S.ret_pct - S.nifty_pct), 1) + ' pts' : ''}</span></div>
   <div class="card">Open positions<b>${F(S.open)} / 5</b><span class="mute">cash ${R(S.cash)}</span></div>
   <div class="card">Closed trades<b>${F(S.closed)}</b><span class="mute">${S.closed ? F(S.wins) + ' won (' + F(S.win_pct, 1) + '%)' : 'none yet'}</span></div>
   <div class="card">Realised P&L<b class="${cl(S.realized)}">${S.closed ? R(S.realized) : '–'}</b><span class="mute">after charges</span></div>
   <div class="card">Worst drop so far<b class="${cl(S.max_dd_pct)}">${P(S.max_dd_pct)}</b><span class="mute">from the highest value</span></div>
  </div>
  <div class="note">What the test showed (${E(B.period)}): ${P(B.return_pct)} vs Nifty ${P(B.nifty_pct)}, ${F(B.win_pct, 1)}% of trades won, worst drop ${P(B.max_dd_pct)}, about ${F(B.avg_days)} days per trade. Last processed close: ${E(b.last_day)} · data ${E(b.data_source || '–')}${(b.failed || []).length ? ' · <span class="warn">no data for ' + E(b.failed.join(', ')) + '</span>' : ''}</div>`;
  h += `<section><h2>Account vs Nifty 50</h2>${swingChart(b.curve || [])}</section>`;
  h += `<section><h2>Open positions</h2>${tbl(['Stock', 'Bought on', 'Shares', 'Buy price', 'Last close', 'Value', 'P&L', 'Change'],
    (b.positions || []).map(p => [td(E(p.symbol)), td(E(p.since)), td(F(p.shares)), td(F(p.entry, 2)), td(F(p.last, 2)), td(R(p.value)),
      td(R(p.pnl), cl(p.pnl)), td(P(p.ret_pct), cl(p.ret_pct))]), 'No open positions.')}</section>`;
  h += `<section><h2>Orders for the next open</h2>${tbl(['Side', 'Stock', 'Signal close', 'Why'],
    (b.pending || []).map(o => [td(o.side === 'buy' ? '<span class="up">buy</span>' : '<span class="down">sell</span>'), td(E(o.symbol)), td(E(o.signal_day)), td(E(o.why), 'r')]),
    'Nothing queued.')}</section>`;
  h += `<section><h2>Closed trades</h2>${tbl(['Stock', 'Bought', 'Sold', 'Shares', 'Buy', 'Sell', 'P&L after charges', 'Return'],
    (b.closed || []).map(t => [td(E(t.symbol)), td(E(t.entry_day)), td(E(t.exit_day)), td(F(t.shares)), td(F(t.entry, 2)), td(F(t.exit, 2)),
      td(R(t.pnl), cl(t.pnl)), td(P(t.ret_pct), cl(t.ret_pct))]), 'No closed trades yet. Trades last about a month on average.')}</section>`;
  if ((b.skipped || []).length) h += `<section><h2>Skipped buys</h2>${tbl(['Day', 'Stock', 'Why'], b.skipped.map(s => [td(E(s.day)), td(E(s.symbol)), td(E(s.why))]), '')}</section>`;
  h += `<section><h2>Bot log</h2><pre>${(b.log || []).map(E).join('\n') || 'No log yet.'}</pre></section>`;
  return h;
}
function swingChart(pts) {
  if (pts.length < 2) return '<div class="empty">The chart starts after the second trading day.</div>';
  const W = 800, H = 240, L = 64, T = 10, Rr = 10, Bm = 26;
  const vals = pts.flatMap(p => [p.value, p.nifty]).filter(v => v != null);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = Math.max((hi - lo) * 0.1, 500); lo -= pad; hi += pad;
  const x = i => L + i * (W - L - Rr) / (pts.length - 1), y = v => T + (hi - v) * (H - T - Bm) / (hi - lo);
  const line = (k, style) => { const seg = pts.map((p, i) => p[k] == null ? null : `${x(i).toFixed(1)},${y(p[k]).toFixed(1)}`).filter(Boolean).join(' ');
    return `<polyline points="${seg}" fill="none" ${style}/>`; };
  let s = `<svg width="100%" viewBox="0 0 ${W} ${H}" role="img" aria-label="Paper account value against the Nifty 50 started at the same amount">`;
  for (let k = 0; k <= 4; k++) { const v = lo + (hi - lo) * k / 4;
    s += `<line x1="${L}" x2="${W - Rr}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="var(--mute)">₹${Math.round(v).toLocaleString('en-IN')}</text>`; }
  s += `<line x1="${L}" x2="${W - Rr}" y1="${y(100000)}" y2="${y(100000)}" stroke="var(--mute)" stroke-dasharray="2 4"/>`;
  s += line('nifty', 'stroke="var(--mute)" stroke-width="1.5" stroke-dasharray="5 4"') + line('value', 'stroke="var(--acc)" stroke-width="2"');
  s += `<text x="${L}" y="${H - 6}" font-size="11" fill="var(--mute)">${pts[0].day}</text><text x="${W - Rr}" y="${H - 6}" text-anchor="end" font-size="11" fill="var(--mute)">${pts[pts.length - 1].day}</text></svg>`;
  return s + `<div class="mute" style="display:flex;gap:16px;font-size:12px"><span><i style="display:inline-block;width:14px;height:3px;background:var(--acc);vertical-align:middle;margin-right:6px"></i>Paper account</span><span>- - Nifty 50, started at ₹1,00,000</span></div>`;
}
"""
