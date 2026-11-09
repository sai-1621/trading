"""Builds a shareable snapshot page of all the paper-trading bots: share/paper-trading.html.

    python build_share.py

The crypto (freqtrade) bots come from the EC2 server when the SSH tunnel to it is open; otherwise from this PC.
Reads the same files as dashboard.py (never changes them) and embeds a snapshot of the trades, signals,
skipped-signal follow-ups, watchlists and strategy test results. No process ids, file paths or logs.
"""
import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import dashboard

OUT = Path(__file__).resolve().parent / "share" / "paper-trading.html"
REMOTE_URL = os.getenv("SHARE_REMOTE", "http://localhost:8800/api/data")  # the EC2 dashboard, through the SSH tunnel


def _j(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def tests_for(bot_dir):
    """Plain-language strategy test results for one bot."""
    tr = bot_dir / "trader"
    out = []
    bt = _j(tr / "backtest.json")
    if bt:
        groups = bt["quotes"].items() if "quotes" in bt else [("", bt)]
        for q, v in groups:
            best = (v.get("ranked") or [None])[0]
            ck = best["check"] if best else {}
            out.append({"test": f"Intraday breakout backtest{(' (' + q + ')') if q else ''}", "date": bt["run_at"],
                        "result": f"{ck.get('avg_r_net')}R per trade after costs on {ck.get('trades')} unseen trades" if best else "not enough trades",
                        "pass": bool(best and ck.get("trades", 0) >= 10 and (ck.get("avg_r_net") or 0) > 0)})
    rg = _j(tr / "regime.json")
    if rg:
        groups = rg["quotes"].items() if "quotes" in rg else [("", rg)]
        for q, v in groups:
            tax = f", {round(v['tax'] * 100)}% tax" if v.get("tax") else (", no tax" if "tax" in v else "")
            out.append({"test": f"Market-condition training{(' (' + q + tax + ')') if q else ''}", "date": rg["run_at"],
                        "result": f"bot {v.get('portfolio_ret_pct', v.get('avg_ret_pct'))}% vs buy-and-hold "
                                  f"{v.get('bh_portfolio_ret_pct', v.get('bh_avg_ret_pct'))}% since {v.get('oos_start')}",
                        "pass": bool(v.get("pass"))})
    rs = _j(tr / "research.json")
    if rs:
        strategies = rs.get("strategies") or next(iter(rs.get("quotes", {}).values()), {})
        passed = [k for k, v in strategies.items() if v.get("passed")]
        out.append({"test": "Swing strategy research", "date": rs["run_at"],
                    "result": f"{len(passed)} of {len(strategies)} strategies passed" + (f": {', '.join(passed)}" if passed else ""),
                    "pass": bool(passed)})
    return out


def remote_bots(url):
    """The crypto bots run on the EC2 server. With the SSH tunnel open
    (ssh -i Trading-bot.pem -L 8800:localhost:8800 ec2-user@3.27.152.78), the server's dashboard answers on
    localhost:8800; its freqtrade bots replace the stale local copies. Returns {} when the tunnel is closed."""
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            bots = json.load(r)["bots"]
    except (OSError, ValueError, KeyError) as e:
        print(f"Server data not used ({e}); is the SSH tunnel open? Crypto bots come from this PC's files.")
        return {}
    return {b["id"]: b for b in bots if b.get("kind") == "freqtrade"}


def build():
    data = dashboard.collect()
    remote = remote_bots(REMOTE_URL)
    for i, b in enumerate(data["bots"]):
        if b.get("id") in remote:
            data["bots"][i] = {**remote[b["id"]], "source": "EC2 server"}
            print(f"{b['id']}: live data from the EC2 server")
    from sip_view import SIP_JS
    from ft_view import FT_JS
    from expiry_view import EXPIRY_JS
    from swing_view import SWING_JS
    from trend_view import TREND_JS
    # the margin note names a local command; the shared page doesn't need it
    EXPIRY_JS = re.sub(r" Refresh: <code>.*?</code>", "", EXPIRY_JS)
    views = SIP_JS + FT_JS + EXPIRY_JS + SWING_JS + TREND_JS
    for b, cfg in zip(data["bots"], dashboard.BOTS):
        for k in ("started", "log", "log_age_s", "log_age", "exists"):
            b.pop(k, None)
        if isinstance(b.get("status"), dict):
            b["status"].pop("pid", None)
        if not cfg.get("kind"):
            b["tests"] = tests_for(cfg["dir"])
    data["generated"] = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(data, default=str).replace("</", "<\\/")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(TEMPLATE.replace("__DATA__", payload).replace("/*SIP_JS*/", views), encoding="utf-8")
    return OUT


TEMPLATE = r"""<title>Paper Trading Bots</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* Layout: a trading-desk ledger. Header with snapshot time, bot tabs, then summary tiles, then ledgers top to bottom. */
:root{
  --bg:#f5f6f4; --panel:#ffffff; --fg:#1b1f1d; --mute:#5f6b66; --line:#dfe3e0; --chip:#edf0ee;
  --accent:#1f6f5c; --accent-fg:#ffffff; --up:#13795b; --down:#b3261e; --warn:#a15c07;
  --sans:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif; --mono:"IBM Plex Mono",ui-monospace,Consolas,monospace;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#111513; --panel:#181d1b; --fg:#e4e9e6; --mute:#94a19b; --line:#2a312e; --chip:#222926;
  --accent:#5fc2a4; --accent-fg:#0d1a15; --up:#5fd0a6; --down:#ff8a80; --warn:#f0b35a; color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#111513; --panel:#181d1b; --fg:#e4e9e6; --mute:#94a19b; --line:#2a312e; --chip:#222926;
  --accent:#5fc2a4; --accent-fg:#0d1a15; --up:#5fd0a6; --down:#ff8a80; --warn:#f0b35a; color-scheme:dark}
body{background:var(--bg);color:var(--fg);font-family:var(--sans);font-size:14px;line-height:1.45}
body::before{content:"";position:fixed;inset:0 0 auto;height:env(safe-area-inset-top,0px);z-index:10;background:var(--panel)}
.wrap{max-width:1360px;margin-inline:auto;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));padding-block:0 32px}
header{position:sticky;top:env(safe-area-inset-top,0px);z-index:5;background:var(--panel);border-bottom:1px solid var(--line)}
header .wrap{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;padding-block:12px}
h1{font-size:20px;font-weight:600;margin:0;text-wrap:balance}
.snap{color:var(--mute);font-size:13px}
.tabs{display:flex;gap:6px;flex-wrap:wrap}
.tab{font:inherit;border:1px solid var(--line);background:var(--panel);color:var(--fg);padding:6px 14px;border-radius:6px;cursor:pointer}
.tab[aria-selected="true"]{background:var(--accent);border-color:var(--accent);color:var(--accent-fg)}
.tab:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.note{color:var(--mute);font-size:13px;margin:12px 0 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,170px),1fr));gap:10px;margin:14px 0}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px;min-width:0}
.tile .k{color:var(--mute);font-size:12px;letter-spacing:.02em}
.tile .v{font-family:var(--mono);font-size:22px;font-weight:500;font-variant-numeric:tabular-nums;margin-top:2px}
.tile .s{color:var(--mute);font-size:12px}
section{margin-top:22px}
h2{font-size:15px;font-weight:600;margin:0 0 4px}
.sub{color:var(--mute);font-size:13px;margin:0 0 8px}
.tbl{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap;vertical-align:top}
tr:last-child td{border-bottom:none}
th{color:var(--mute);font-weight:500;font-size:12px;background:var(--panel)}
td.n{font-family:var(--mono);font-variant-numeric:tabular-nums}
td.w{white-space:normal;min-width:220px;max-width:420px}
.up{color:var(--up)}.down{color:var(--down)}.mute{color:var(--mute)}
.pill{display:inline-block;font-size:12px;padding:1px 8px;border-radius:999px;background:var(--chip)}
.pill.ok{color:var(--up)}.pill.bad{color:var(--down)}.pill.buy{color:var(--up);font-weight:600}
.empty{color:var(--mute);padding:12px;background:var(--panel);border:1px dashed var(--line);border-radius:8px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px;min-width:0}
.tile .v b{font-weight:500}
/* classes used by the bot views shared with the local dashboard */
:root{--acc:var(--accent);--card:var(--panel);--ink:var(--fg)}
#main .wrap{max-width:none;padding:0;overflow-x:auto}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,160px),1fr));gap:10px;margin:12px 0}
.card b{display:block;font-size:20px;font-weight:500;font-family:var(--mono);margin-top:2px}
#main section{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px;margin:12px 0}
.chip{display:inline-block;background:var(--chip);border-radius:999px;padding:1px 8px;margin:1px;font-size:12px}
.warn{color:var(--warn)} td.r{white-space:normal;min-width:240px}
.bar{height:8px;background:var(--chip);border-radius:999px;overflow:hidden;margin:4px 0 6px}.bar span{display:block;height:100%;background:var(--accent)}
pre{white-space:pre-wrap;font-size:12px;margin:0;color:var(--mute)}
</style>

<header><div class="wrap">
  <div><h1>Paper Trading Bots</h1><div class="snap" id="snap"></div></div>
  <div class="tabs" role="tablist" id="tabs"></div>
</div></header>
<div class="wrap" id="main"></div>

<script id="data" type="application/json">__DATA__</script>
<script>
/*SIP_JS*/
const DATA = JSON.parse(document.getElementById('data').textContent);
let tab = (location.hash || '').slice(1);
if (!DATA.bots.some(b => b.id === tab)) { try { tab = localStorage.getItem('tab') || DATA.bots[0].id; } catch (e) { tab = DATA.bots[0].id; } }
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const num = (v, d = 2) => v == null || v === '' || isNaN(v) ? '–' : Number(v).toLocaleString('en-IN', {maximumFractionDigits: d, minimumFractionDigits: Math.min(d, 2)});
const px = v => v == null ? '–' : Math.abs(v) >= 1 ? num(v, Math.abs(v) >= 1000 ? 2 : 4) : Number(v).toPrecision(4);
const when = s => { if (!s) return '–'; const d = new Date(s); return isNaN(d) ? esc(s) : d.toLocaleString('en-IN', {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}); };
const sign = v => v > 0 ? 'up' : v < 0 ? 'down' : '';
const money = (c, v) => v == null ? '–' : (v < 0 ? '−' : '') + c + num(Math.abs(v));
function table(cols, rows, empty) {
  if (!rows.length) return `<div class="empty">${empty}</div>`;
  return `<div class="tbl"><table><thead><tr>${cols.map(c => `<th scope="col">${c.h}</th>`).join('')}</tr></thead><tbody>${
    rows.map(r => `<tr>${cols.map(c => `<td class="${c.cls ? (typeof c.cls === 'function' ? c.cls(r) : c.cls) : ''}">${c.v(r)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
function render() {
  document.getElementById('snap').textContent = 'Paper money only · snapshot ' + new Date(DATA.generated).toLocaleString('en-IN', {dateStyle: 'medium', timeStyle: 'short'});
  document.getElementById('tabs').innerHTML = DATA.bots.map(b => `<button class="tab" role="tab" id="tab-${b.id}" aria-selected="${b.id === tab}">${esc(b.name)}</button>`).join('');
  DATA.bots.forEach(b => document.getElementById('tab-' + b.id).onclick = () => { tab = b.id; try { localStorage.setItem('tab', tab); } catch (e) {} render(); });
  const sel = DATA.bots.find(x => x.id === tab) || DATA.bots[0];
  if (sel.kind === 'sip') { document.getElementById('main').innerHTML = sipView(sel); return; }
  const V = {freqtrade: ftView, expiry: expiryView, swing: swingView, trend: trendView}[sel.kind];
  if (V) { document.getElementById('main').innerHTML = V(sel); return; }
  const b = sel, c = b.cur, P = b.paper_summary, H = b.shadow_summary;
  const open = b.positions.reduce((a, p) => a + (p.last != null ? (p.last - p.entry) * p.qty : 0), 0);
  let h = `<p class="note">${esc(b.hours)}. Trades use fake money at real market prices, after charges.</p>
  <div class="tiles">
    <div class="tile"><div class="k">Paper cash</div><div class="v">${money(c, b.paper_cash)}</div></div>
    <div class="tile"><div class="k">Realized profit</div><div class="v ${sign(b.realized_pnl)}">${money(c, b.realized_pnl)}</div></div>
    <div class="tile"><div class="k">Open profit</div><div class="v ${sign(open)}">${money(c, open)}</div><div class="s">${b.positions.length} open</div></div>
    <div class="tile"><div class="k">Paper trades</div><div class="v">${P.count || 0}</div><div class="s">${P.count ? `${P.win_pct}% won · avg ${P.avg_r}R` : 'none yet'}</div></div>
    <div class="tile"><div class="k">Signals seen</div><div class="v">${b.signals_summary.total}</div><div class="s">${b.signals_summary.bought} bought</div></div>
    <div class="tile"><div class="k">Skipped, followed anyway</div><div class="v ${sign(H.pnl)}">${H.count ? money(c, H.pnl) : '–'}</div><div class="s">${H.count ? `${H.count} closed · ${H.win_pct}% won` : 'none closed yet'}</div></div>
  </div>`;
  h += `<section><h2>Open positions</h2>${table([
    {h: 'Stock / coin', v: p => esc(p.symbol || p.market)}, {h: 'Opened', v: p => when(p.opened_at)}, {h: 'Qty', v: p => num(p.qty, 5), cls: 'n'},
    {h: 'Entry', v: p => px(p.entry), cls: 'n'}, {h: 'Stop loss', v: p => px(p.stop), cls: 'n'}, {h: 'Target', v: p => px(p.target), cls: 'n'},
    {h: 'Last price', v: p => px(p.last), cls: 'n'}, {h: 'Profit', v: p => p.last != null ? money(c, (p.last - p.entry) * p.qty) : '–', cls: p => 'n ' + sign((p.last ?? p.entry) - p.entry)},
    {h: 'Most it can lose', v: p => money(c, p.max_loss), cls: 'n'}], b.positions, 'No open positions in this snapshot.')}</section>`;
  h += `<section><h2>Closed paper trades</h2>${table([
    {h: 'Closed', v: r => when(r.time)}, {h: 'Stock / coin', v: r => esc(r.symbol || r.market)}, {h: 'Qty', v: r => num(r.qty, 5), cls: 'n'},
    {h: 'Entry', v: r => px(r.entry), cls: 'n'}, {h: 'Exit', v: r => px(r.price), cls: 'n'},
    {h: 'Profit', v: r => money(c, r.pnl), cls: r => 'n ' + sign(r.pnl)}, {h: 'R', v: r => num(r.r), cls: r => 'n ' + sign(r.r)},
    {h: 'Why it closed', v: r => esc(r.reason), cls: 'w'}], b.paper_trades, 'No closed paper trades yet.')}</section>`;
  h += `<section><h2>Signals</h2><p class="sub">Every breakout the bot saw, its plan, and whether it bought.</p>${table([
    {h: 'Time', v: r => when(r.time)}, {h: 'Stock / coin', v: r => esc(r.symbol)}, {h: 'Setup', v: r => esc(r.setup)},
    {h: 'Entry', v: r => px(r.entry), cls: 'n'}, {h: 'Stop loss', v: r => px(r.stop), cls: 'n'}, {h: 'Target', v: r => px(r.target), cls: 'n'},
    {h: 'Risk', v: r => r.risk_pct != null ? num(r.risk_pct) + '%' : '–', cls: 'n'},
    {h: 'Odds (10,000 replays)', v: r => r.time_machine ? `${esc(r.time_machine.verdict)}<br><span class="mute">target ${r.time_machine.target_first_pct}% · stop ${r.time_machine.stop_first_pct}%</span>` : '–'},
    {h: 'Decision', v: r => `<span class="pill ${r.decision === 'BOUGHT' ? 'buy' : ''}">${r.decision === 'BOUGHT' ? 'Bought' : 'Skipped'}</span>`},
    {h: 'Why', v: r => esc((r.stage ? r.stage + ': ' : '') + (r.reason || '')), cls: 'w'}],
    b.signals, 'No signals yet. One appears each time a price closes above its opening range during trading hours.')}</section>`;
  h += `<section><h2>Skipped signals, followed anyway</h2><p class="sub">What would have happened without the safety checks. Not counted in the paper results.</p>${table([
    {h: 'Opened', v: r => when(r.opened_at)}, {h: 'Stock / coin', v: r => esc(r.key)}, {h: 'Entry', v: r => px(r.entry), cls: 'n'},
    {h: 'Stop loss', v: r => px(r.stop), cls: 'n'}, {h: 'Target', v: r => px(r.target), cls: 'n'},
    {h: 'Exit', v: r => r.exit != null ? px(r.exit) : 'open, last ' + px(r.last), cls: 'n'},
    {h: 'Outcome', v: r => esc(r.outcome || 'still open')},
    {h: 'Profit', v: r => money(c, r.pnl ?? (r.last - r.entry) * r.qty), cls: r => 'n ' + sign(r.pnl ?? (r.last - r.entry))},
    {h: 'Skipped because', v: r => esc(r.skipped_because), cls: 'w'}], [...b.shadows_open, ...b.shadow_trades], 'None yet.')}</section>`;
  h += `<section><h2>Strategy tests</h2><p class="sub">Run on past data, judged only on periods the settings were not tuned on.</p>${table([
    {h: 'Test', v: r => esc(r.test)}, {h: 'Run', v: r => when(r.date)}, {h: 'Result', v: r => esc(r.result), cls: 'w'},
    {h: 'Verdict', v: r => `<span class="pill ${r.pass ? 'ok' : 'bad'}">${r.pass ? 'Pass' : 'Fail'}</span>`}], b.tests, 'No tests run yet.')}</section>`;
  h += `<section><h2>Watchlist</h2>${table([
    {h: 'Stock / coin', v: w => esc(w.symbol || w.market)}, {h: 'Chart setups', v: w => (w.tags || []).map(t => `<span class="pill">${esc(t.replace(/_/g, ' '))}</span>`).join(' ') || '–'},
    {h: 'Uptrend', v: w => w.above_ma20 == null ? '–' : w.above_ma20 ? '<span class="up">yes</span>' : '<span class="mute">no, skipped</span>'},
    {h: '20-day high', v: w => px(w.level_20d), cls: 'n'}, {h: 'News risk today', v: w => esc((b.news_risk[w.symbol] || []).join(', ')) || '–'}],
    b.watchlist, 'The scanner has not run yet.')}</section>`;
  document.getElementById('main').innerHTML = h;
}
render();
</script>
"""

if __name__ == "__main__":
    print("Wrote", build())
