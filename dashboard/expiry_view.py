"""Dashboard tab for the NSE expiry-day paper bot (nse_fno_lab/expiry_bot.py, strategy 2 iron fly).
collect_expiry() reads the bot's files (read-only); EXPIRY_JS renders them with the page's own CSS classes."""
import csv
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))


def _read_csv(p: Path) -> list[dict]:
    try:
        with p.open(encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def _num(v):
    try:
        return float(v) if v not in (None, "", "None") else None
    except ValueError:
        return None


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


def _group(rows, key):
    out = {}
    for r in rows:
        k = key(r)
        g = out.setdefault(k, {"group": k, "flies": 0, "net": 0.0, "won": 0, "hold": 0.0, "hold_n": 0})
        n = _num(r.get("net_rs_after_charges"))
        if n is None:
            continue
        g["flies"] += 1
        g["net"] += n
        g["won"] += n > 0
        h = _num(r.get("hold_to_1515_gross_rs"))
        if h is not None:
            g["hold"] += h
            g["hold_n"] += 1
    for g in out.values():
        g["net"] = round(g["net"], 2)
        g["hold"] = round(g["hold"], 2)
        g["win_pct"] = round(g["won"] / g["flies"] * 100, 1) if g["flies"] else None
        g["avg"] = round(g["net"] / g["flies"], 2) if g["flies"] else None
    return sorted(out.values(), key=lambda g: str(g["group"]))


def _width(fid: str):
    m = re.search(r"W(\d+)", fid or "")
    return int(m.group(1)) if m else None


def _capital(results, margin):
    """Margin per fly from logs/fly_margin.json (Angel One's calculator), the most each fly could lose, and the
    margin blocked at the busiest moment of each expiry day (flies open at the same time)."""
    per_w = {int(w): f for w, f in (margin.get("flies") or {}).items()}
    if not per_w:
        return {}, None
    lot = next(iter(per_w.values())).get("lot") or 65

    def m_of(w):
        f = per_w.get(w) or per_w[min(per_w, key=lambda x: abs(x - (w or 0)))]
        return f["margin_rs"]

    days = {}
    for r in results:
        w, lots = _width(r.get("fid")), int(_num(r.get("lots")) or 1)
        cr = _num(r.get("credit_exec_first"))
        r["margin_rs"] = round(m_of(w) * lots, 0)
        r["max_loss_rs"] = round((w - cr) * lot * lots, 0) if (w and cr is not None) else None
        n = _num(r.get("net_rs_after_charges"))
        r["ret_on_margin_pct"] = round(n / r["margin_rs"] * 100, 2) if n is not None and r["margin_rs"] else None
        days.setdefault(r.get("date"), []).append((r.get("entry_time") or "", r.get("exit_time") or "99:99", r["margin_rs"]))
    peak = 0.0
    for flies in days.values():
        for t, _, _ in flies:   # busiest moment = some entry time
            peak = max(peak, sum(m for a, e, m in flies if a <= t < e))
    return {"per_width": {str(w): {"margin_rs": f["margin_rs"], "max_loss_rs": f["max_loss_rs"],
                                   "credit_rs": f["credit_rs"]} for w, f in sorted(per_w.items())},
            "lot": lot, "checked": margin.get("checked"), "expiry": margin.get("expiry"),
            "planned_rs": round(sum(m_of(w) for w in (150, 150, 200, 200)), 0),
            "peak_rs": round(peak, 0) if peak else None}, m_of


def collect_expiry(b: dict) -> dict:
    d: Path = b["dir"]
    logs = d / "logs"
    status = {}
    try:
        margin = json.loads((logs / "fly_margin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        margin = {}
    try:
        status = json.loads((logs / "expiry_bot_status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    pid = (d / "expiry_bot.pid").read_text(encoding="utf-8").strip() if (d / "expiry_bot.pid").exists() else ""
    results = _read_csv(logs / "paper_fly_results.csv")          # real records only (test_ files ignored)
    for r in results:
        r["width"] = re.sub(r"^\d+_", "", r.get("fid", ""))
        r["entry"] = (r.get("fid", "")[:4][:2] + ":" + r.get("fid", "")[2:4]) if r.get("fid") else ""
    nets = [_num(r.get("net_rs_after_charges")) for r in results]
    nets = [x for x in nets if x is not None]
    ratios = [_num(r.get("credit_ratio")) for r in results if _num(r.get("credit_ratio"))]
    today = datetime.now(IST).strftime("%Y%m%d")
    live = []
    for sf in sorted(logs.glob(f"paper_fly_*_{today}.json")):
        try:
            st = json.loads(sf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        marks = [m for m in _read_csv(logs / "paper_fly_marks.csv") if m.get("date") == datetime.now(IST).date().isoformat()]
        last = {}
        for m in marks:
            last[m.get("fid")] = m
        for f in st.get("flies", []):
            m = last.get(f["fid"], {})
            live.append({"fid": f["fid"], "symbol": f["symbol"], "K": f["K"], "W": f["W"], "status": f["status"],
                         "credit": round(f.get("shadow_credit") or 0, 2), "entry_time": f["entry_time"],
                         "adjustments": f.get("adjustments", 0), "exit": f.get("exit_reason") or "",
                         "exit_time": f.get("exit_time") or "", "net": f.get("final_net") if f["status"] == "closed" else None,
                         "mtm": _num(m.get("mtm_rs_after_charges")), "spot": _num(m.get("spot")),
                         "last_mark": m.get("time_s") or m.get("time"), "filters": (f.get("filters") or {}).get("pass")})
    try:
        tail = (logs / "expiry_bot.log").read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
    except OSError:
        tail = []
    cap, m_of = _capital(results, margin)
    if cap:
        cap["in_use_rs"] = round(sum(m_of(int(f["W"])) for f in live if f["status"] == "open"), 0)
        cap["open_flies"] = sum(1 for f in live if f["status"] == "open")
        base = cap.get("peak_rs") or cap["planned_rs"]
        cap["ret_on_peak_pct"] = round(sum(nets) / base * 100, 2) if nets and base else None
        for f in live:
            f["margin_rs"] = round(m_of(int(f["W"])), 0)
    return {"id": b["id"], "name": b["name"], "cur": b["cur"], "hours": b["hours"], "kind": "expiry",
            "status": status, "started": pid, "alive": _alive(pid), "capital": cap,
            "summary": {"days": len({r.get("date") for r in results}), "flies": len(nets), "net": round(sum(nets), 2),
                        "won": sum(1 for x in nets if x > 0), "avg": round(sum(nets) / len(nets), 2) if nets else None,
                        "avg_credit_ratio": round(sum(ratios) / len(ratios), 3) if ratios else None,
                        "filters_pass_net": round(sum(_num(r.get("net_rs_after_charges")) or 0 for r in results
                                                      if r.get("filters_pass") == "True"), 2)},
            "by_width": _group(results, lambda r: r["width"]), "by_entry": _group(results, lambda r: r["entry"]),
            "by_day": _group(results, lambda r: r.get("date")),
            "results": results[::-1][:300], "today": live, "log": tail}


EXPIRY_JS = r"""
function expiryView(b) {
  const c = b.cur, S = b.summary || {}, st = b.status || {};
  const F = (v, d = 0) => v == null || v === '' ? '–' : Number(v).toLocaleString('en-IN', {maximumFractionDigits: d, minimumFractionDigits: 0});
  const R = v => v == null || v === '' ? '–' : (Number(v) < 0 ? '−' : '') + c + F(Math.abs(Number(v)), 0);
  const cl = v => v > 0 ? 'up' : v < 0 ? 'down' : '';
  const E = s => String(s ?? '').replace(/[&<>"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch]));
  const tbl = (head, rows, empty) => rows.length ? `<div class="wrap"><table><tr>${head.map(h => `<th>${h}</th>`).join('')}</tr>${rows.map(r => `<tr>${r.join('')}</tr>`).join('')}</table></div>` : `<div class="empty">${empty}</div>`;
  const td = (x, k = '') => `<td class="${k}">${x}</td>`;
  const state = b.alive ? (st.state === 'tracking' ? `<span class="up">● tracking ${E(st.symbol)} now</span>` : `<span class="up">● running</span> <span class="mute">(${E(st.reason || 'waiting')})</span>`) : '<span class="down">● stopped</span>';
  const nx = st.next_expiry || {};
  let h = `<div class="note">${E(b.hours)} · ${state}${b.started ? ' · pid ' + E(b.started) : ''}</div>
  <div class="note">Paper only, no orders. Real option prices: shorts sold at the bid, wings bought at the ask, closed at the ask / bid. 1 lot per fly. Stops are checked every 60 s, so a spike that reverses within a minute is missed.</div>
  <div class="cards">
   <div class="card">Next NIFTY expiry<b>${E(nx.NIFTY || '–')}</b><span class="mute">Tuesday, holiday-shifted</span></div>
   <div class="card">Next SENSEX expiry<b>${E(nx.SENSEX || '–')}</b><span class="mute">${st.bse_keys ? 'tracked' : 'skipped: needs Dhan / Angel / Kite keys'}</span></div>
   <div class="card">Paper flies so far<b>${F(S.flies)}</b><span class="mute">${F(S.days)} expiry day(s) · ${S.flies ? F(S.won) + ' won' : 'none yet'}</span></div>
   <div class="card">Net P&L after charges<b class="${cl(S.net)}">${S.flies ? R(S.net) : '–'}</b><span class="mute">avg ${S.flies ? R(S.avg) : '–'} per fly</span></div>
   <div class="card">Credit vs VIX model<b>${S.avg_credit_ratio == null ? '–' : F(S.avg_credit_ratio, 2) + '×'}</b><span class="mute">real executable credit ÷ Black-Scholes at India VIX</span></div>
   <div class="card">When the filters said go<b class="${cl(S.filters_pass_net)}">${S.flies ? R(S.filters_pass_net) : '–'}</b><span class="mute">net of flies whose entry passed the strategy's filters</span></div>
  </div>`;
  const K = b.capital || {};
  if (K.per_width) {
    const pw = K.per_width, ws = Object.keys(pw);
    h += `<section><h2>Capital</h2><div class="cards">
     ${ws.map(w => `<div class="card">Margin for 1 fly, wings ${E(w)}<b>${R(pw[w].margin_rs)}</b><span class="mute">1 lot (${F(K.lot)} units) · most it can lose ${R(pw[w].max_loss_rs)}</span></div>`).join('')}
     <div class="card">Capital needed<b>${R(K.peak_rs || K.planned_rs)}</b><span class="mute">${K.peak_rs ? 'margin blocked at the busiest moment (flies open together)' : '4 flies open together (2 × 150, 2 × 200)'}</span></div>
     <div class="card">In use now<b>${R(K.in_use_rs)}</b><span class="mute">${F(K.open_flies)} fly(s) open</span></div>
     <div class="card">Return on that capital<b class="${cl(K.ret_on_peak_pct)}">${K.ret_on_peak_pct == null ? '–' : (K.ret_on_peak_pct > 0 ? '+' : '') + F(K.ret_on_peak_pct, 2) + '%'}</b><span class="mute">net P&L so far ÷ capital needed</span></div>
    </div><div class="note">Margin from Angel One's margin calculator for the ${E(K.expiry)} expiry, checked ${E((K.checked || '').replace('T', ' '))}. Exchange margin is far above the most a fly can lose, because the exchange blocks money for the short options. On expiry day an extra 2% margin on short options can apply, so the real figure can be higher. Refresh: <code>angelone_bot\\.venv\\Scripts\\python nse_fno_lab\\scripts\\fly_margin.py</code></div></section>`;
  }
  h += `<section><h2>Today</h2>${tbl(['Fly', 'Opened', 'Strikes', 'Credit / unit', 'Margin', 'Status', 'Last check', 'Spot', 'MTM after charges', 'Re-centres', 'Filters'],
    (b.today || []).map(r => [td(E(r.fid)), td(E(r.entry_time)), td(`${F(r.K)} ± ${F(r.W)}`), td(F(r.credit, 2)), td(R(r.margin_rs)),
      td(r.status === 'open' ? '<span class="up">open</span>' : `closed ${E(r.exit_time)}<br><span class="mute">${E(r.exit)}</span>`),
      td(E(r.last_mark || '–')), td(F(r.spot, 2)), td(r.status === 'open' ? R(r.mtm) : R(r.net), cl(r.status === 'open' ? r.mtm : r.net)),
      td(F(r.adjustments)), td(r.filters == null ? '–' : r.filters ? 'pass' : '<span class="warn">fail</span>')]),
    'No paper flies today. The bot opens them at 09:45 and 10:15 on an expiry day.')}</section>`;
  const grp = (title, rows, label) => `<section><h2>${title}</h2>${tbl([label, 'Flies', 'Won', 'Net after charges', 'Avg per fly', 'Held to 15:15 instead (gross)'],
    rows.map(g => [td(E(g.group)), td(F(g.flies)), td(g.flies ? F(g.win_pct, 1) + '%' : '–'), td(R(g.net), cl(g.net)), td(R(g.avg), cl(g.avg)), td(g.hold_n ? R(g.hold) : '–', cl(g.hold))]),
    'No finished expiry days yet.')}</section>`;
  h += grp('By wing width', b.by_width || [], 'Width (R = the strategy\'s own width)');
  h += grp('By entry time', b.by_entry || [], 'Entry');
  h += grp('By expiry day', b.by_day || [], 'Day');
  h += `<section><h2>Every paper fly</h2>${tbl(['Day', 'Fly', 'Spot', 'ATM', 'Credit', 'VIX model', 'Ratio', 'Exit', 'Why', 'Re-centres', 'Charges', 'Net', 'Margin', 'Max loss', 'Return on margin', 'Held to 15:15', 'Filters'],
    (b.results || []).map(r => [td(E(r.date)), td(E(r.fid)), td(F(r.spot_in, 2)), td(F(r.K_first)), td(F(r.credit_exec_first, 2)), td(F(r.bsm_credit_flat, 2)),
      td(r.credit_ratio ? F(r.credit_ratio, 2) + '×' : '–'), td(E(r.exit_time)), td(E(r.exit_reason)), td(E(r.adjustments)), td(R(r.charges_rs)),
      td(R(r.net_rs_after_charges), cl(Number(r.net_rs_after_charges))), td(R(r.margin_rs)), td(R(r.max_loss_rs)),
      td(r.ret_on_margin_pct == null ? '–' : (r.ret_on_margin_pct > 0 ? '+' : '') + F(r.ret_on_margin_pct, 2) + '%', cl(r.ret_on_margin_pct)), td(R(r.hold_to_1515_gross_rs), cl(Number(r.hold_to_1515_gross_rs))),
      td(r.filters_pass === 'True' ? 'pass' : `<span class="warn" title="${E(r.filter_notes)}">fail</span>`)]),
    'No finished paper flies yet. First expiry: Tuesday 3 Nov 2026.')}</section>`;
  h += `<section><h2>Bot log</h2><pre>${(b.log || []).map(E).join('\n') || 'No log yet.'}</pre></section>`;
  return h;
}
"""
