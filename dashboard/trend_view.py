"""Dashboard tab for the paper crypto trend bot (futures_lab/trend_bot.py).
collect_trend() reads the bot's files (read-only); TREND_JS renders them with the page's own CSS classes."""
import json
import re
from pathlib import Path

CAPITAL = 1000.0
# futures_lab/results_v2.json: the rule on data it was not chosen on (2018 - Oct 2021) and on 2025-26 (seen)
RESEARCH = {"fresh": "2018 – Oct 2021: +389% (BTC +358%), worst drop −31%, 53% of trades won",
            "seen": "2025 – Oct 2026: −11%, worst drop −42%"}


def _alive(pid_text):
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


def collect_trend(b):
    d: Path = b["dir"]
    try:
        st = json.loads((d / "trend_paper.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    pid = (d / "trend_bot.pid").read_text(encoding="utf-8").strip() if (d / "trend_bot.pid").exists() else ""
    hist = st.get("history") or []
    eq = st.get("equity", CAPITAL)
    b0 = st.get("btc_start")
    curve = [{"day": h[0], "value": h[1], "btc": round(CAPITAL * h[2] / b0, 2) if (h[2] and b0) else None} for h in hist]
    peak, dd = CAPITAL, 0.0
    for h in hist:
        peak = max(peak, h[1])
        dd = min(dd, h[1] / peak - 1)
    closed = st.get("closed") or []
    wins = sum(1 for t in closed if t["pnl_pct"] > 0)
    prices = st.get("prices") or {}
    opens = [{"symbol": c, "weight": w, "notional": round(w * eq, 2), "since": (st.get("open_trades") or {}).get(c, {}).get("entry_day"),
              "entry": (st.get("open_trades") or {}).get(c, {}).get("entry"), "last": prices.get(c),
              "pnl_pct": round((st.get("open_trades") or {}).get(c, {}).get("pnl_pct", 0.0), 2)}
             for c, w in (st.get("weights") or {}).items()]
    try:
        tail = (d / "trend_bot.log").read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
    except OSError:
        tail = []
    return {"id": b["id"], "name": b["name"], "cur": b["cur"], "hours": b["hours"], "kind": "trend",
            "alive": _alive(pid), "started": pid, "has_account": bool(st), "start": st.get("start"),
            "last_fill": st.get("last_exec_day"), "btc_ok": st.get("btc_above_200d"),
            "summary": {"value": round(eq, 2), "ret_pct": round((eq / CAPITAL - 1) * 100, 2),
                        "btc_pct": round((hist[-1][2] / b0 - 1) * 100, 2) if (hist and b0 and hist[-1][2]) else None,
                        "leverage": round(sum((st.get("weights") or {}).values()), 2), "open": len(opens),
                        "closed": len(closed), "wins": wins,
                        "win_pct": round(wins / len(closed) * 100, 1) if closed else None, "max_dd_pct": round(dd * 100, 2)},
            "positions": opens, "closed": closed[::-1][:300], "curve": curve, "research": RESEARCH, "log": tail}


TREND_JS = r"""
function trendView(b) {
  const S = b.summary || {}, Rz = b.research || {};
  const F = (v, d = 0) => v == null || v === '' ? '–' : Number(v).toLocaleString('en-US', {maximumFractionDigits: d, minimumFractionDigits: d});
  const P = v => v == null ? '–' : (v > 0 ? '+' : v < 0 ? '−' : '') + F(Math.abs(v), 1) + '%';
  const cl = v => v > 0 ? 'up' : v < 0 ? 'down' : '';
  const E = s => String(s ?? '').replace(/[&<>"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch]));
  const tbl = (head, rows, empty) => rows.length ? `<div class="wrap"><table><tr>${head.map(h => `<th>${h}</th>`).join('')}</tr>${rows.map(r => `<tr>${r.join('')}</tr>`).join('')}</table></div>` : `<div class="empty">${empty}</div>`;
  const td = (x, k = '') => `<td class="${k}">${x}</td>`;
  const state = b.alive ? '<span class="up">● running</span>' : '<span class="down">● stopped</span>';
  let h = `<div class="note">${E(b.hours)} · ${state}${b.started ? ' · ' + E(b.started) : ''}</div>
  <div class="note">Paper only, no orders, public prices. Long only: buys a coin closing above its 55-day high while BTC is above its 200-day average and the coin is trending cleanly; sells below the 20-day low or when BTC drops below its 200-day average. Size follows volatility (40%/yr target), max 3x in total, 0.75x per coin. Judge it after months.</div>`;
  if (!b.has_account) return h + '<div class="empty">No paper account yet.</div>';
  h += `<div class="cards">
   <div class="card">Account<b class="${cl(S.ret_pct)}">${F(S.value, 2)} USDT</b><span class="mute">${P(S.ret_pct)} since ${E(b.start)}</span></div>
   <div class="card">BTC, same dates<b class="${cl(S.btc_pct)}">${P(S.btc_pct)}</b><span class="mute">${b.btc_ok ? 'BTC above its 200-day average: buying allowed' : 'BTC below its 200-day average: in cash'}</span></div>
   <div class="card">Exposure now<b>${F(S.leverage, 2)}x</b><span class="mute">${F(S.open)} coin(s) held</span></div>
   <div class="card">Closed trades<b>${F(S.closed)}</b><span class="mute">${S.closed ? F(S.wins) + ' won (' + F(S.win_pct, 1) + '%)' : 'none yet'}</span></div>
   <div class="card">Worst drop so far<b class="${cl(S.max_dd_pct)}">${P(S.max_dd_pct)}</b><span class="mute">from the highest value</span></div>
  </div>
  <div class="note">Research: ${E(Rz.fresh)} (data the rule was not chosen on). ${E(Rz.seen)} (already seen). Last fill ${E(b.last_fill)} 01:00 UTC.</div>`;
  h += `<section><h2>Account vs BTC</h2>${trendChart(b.curve || [])}</section>`;
  h += `<section><h2>Open positions</h2>${tbl(['Coin', 'Since', 'Weight', 'Position', 'Entry', 'Last fill price', 'Result so far (% of account)'],
    (b.positions || []).map(p => [td(E(p.symbol)), td(E(p.since)), td(F(p.weight, 2) + 'x'), td(F(p.notional, 2) + ' USDT'), td(F(p.entry, 4)), td(F(p.last, 4)), td(P(p.pnl_pct), cl(p.pnl_pct))]),
    'No open positions (in cash).')}</section>`;
  h += `<section><h2>Closed trades</h2>${tbl(['Coin', 'Bought', 'Sold', 'Entry', 'Exit', 'Price change', 'Result (% of account)'],
    (b.closed || []).map(t => [td(E(t.symbol)), td(E(t.entry_day)), td(E(t.exit_day)), td(F(t.entry, 4)), td(F(t.exit, 4)), td(P(t.price_change_pct), cl(t.price_change_pct)), td(P(t.pnl_pct), cl(t.pnl_pct))]),
    'No closed trades yet. Trend trades usually last weeks.')}</section>`;
  h += `<section><h2>Bot log</h2><pre>${(b.log || []).map(E).join('\n') || 'No log yet.'}</pre></section>`;
  return h;
}
function trendChart(pts) {
  if (pts.length < 2) return '<div class="empty">The chart starts after the second daily fill.</div>';
  const W = 800, H = 240, L = 64, T = 10, Rr = 10, Bm = 26;
  const vals = pts.flatMap(p => [p.value, p.btc]).filter(v => v != null);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = Math.max((hi - lo) * 0.1, 5); lo -= pad; hi += pad;
  const x = i => L + i * (W - L - Rr) / (pts.length - 1), y = v => T + (hi - v) * (H - T - Bm) / (hi - lo);
  const line = (k, style) => `<polyline points="${pts.map((p, i) => p[k] == null ? null : x(i).toFixed(1) + ',' + y(p[k]).toFixed(1)).filter(Boolean).join(' ')}" fill="none" ${style}/>`;
  let s = `<svg width="100%" viewBox="0 0 ${W} ${H}" role="img" aria-label="Paper account against BTC started at the same amount">`;
  for (let k = 0; k <= 4; k++) { const v = lo + (hi - lo) * k / 4;
    s += `<line x1="${L}" x2="${W - Rr}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="var(--mute)">${Math.round(v)}</text>`; }
  s += line('btc', 'stroke="var(--mute)" stroke-width="1.5" stroke-dasharray="5 4"') + line('value', 'stroke="var(--acc)" stroke-width="2"');
  s += `<text x="${L}" y="${H - 6}" font-size="11" fill="var(--mute)">${pts[0].day}</text><text x="${W - Rr}" y="${H - 6}" text-anchor="end" font-size="11" fill="var(--mute)">${pts[pts.length - 1].day}</text></svg>`;
  return s + `<div class="mute" style="display:flex;gap:16px;font-size:12px"><span><i style="display:inline-block;width:14px;height:3px;background:var(--acc);vertical-align:middle;margin-right:6px"></i>Paper account (USDT)</span><span>- - BTC, started at 1,000</span></div>`;
}
"""
