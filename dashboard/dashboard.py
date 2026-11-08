"""Paper-trading dashboard for the bots. Open http://localhost:8800 in a browser.

Reads each bot's files (state.json, trader/*.jsonl, trader/*.json, bot.log); it never changes them.
    python dashboard.py            (from this folder, with any Python 3.10+)
"""
import json
import re
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8800
BOTS = [
    {"id": "angelone", "name": "Angel One NSE", "dir": ROOT / "angelone_bot", "cur": "₹", "task": "AngelOne Bot",
     "hours": "NSE 09:15–15:30 IST, entries 09:30–14:30, out by 15:10"},
    {"id": "ibkr", "name": "IBKR US stocks", "dir": ROOT / "ibkr_us_bot", "cur": "$", "task": "IBKR US Bot",
     "hours": "NYSE 09:30–16:00 ET (19:00–01:30 IST), entries 09:45–15:30 ET, out by 15:55 ET"},
    {"id": "pumpml", "name": "Crypto ML pump/dump 1x", "dir": ROOT / "freqtrade_bot", "cur": "USDT", "task": "",
     "kind": "freqtrade", "mode": "ml", "config": "config-pump.json", "db": "tradesv3.pumpml.dryrun.sqlite",
     "log": "pumpml.log", "strategy_file": "PumpML.py",
     "pairs_label": "all Binance USDT futures coins (list refreshes every 30 min, new listings included)",
     "hours": "Paper experiment: machine-learning model, long or short after sudden moves, 1x. Checks every hour, 24/7; each trade lasts at most a day."},
    {"id": "pumpml15", "name": "Crypto ML 1.5% strongest-first", "dir": ROOT / "freqtrade_bot", "cur": "USDT", "task": "",
     "kind": "freqtrade", "mode": "ml", "config": "config-pump.json", "overlay": "config-pumpml15.json",
     "virtual": "pumpml15_virtual.json", "db": "-", "log": "pumpml.log", "strategy_file": "PumpML.py", "min_pred": 0.015,
     "pairs_label": "all Binance USDT futures coins (list refreshes every 30 min, new listings included)",
     "hours": "Virtual wallet inside the main ML bot (since 7 Nov 2026, to save memory): same predictions, stricter rule: trades only predictions of at least 1.5%, strongest first when slots are short. Prices polled every 15 s; stops also checked against hourly candles and filled at the stop price, as an exchange-held stop would. Protections: a side pauses 12 h after 3 stop-outs in 24 h; everything pauses 24 h after an 8% drop in a week; 2 h cooldown per coin. Own 1,000 USDT wallet. On trial until 6 Feb 2027."},
    {"id": "sip", "name": "Index ETF SIP", "dir": ROOT / "index_etf_bot", "cur": "₹", "task": "Index ETF SIP Bot", "kind": "sip",
     "hours": "Buys on the first trading day of each month after 09:45 IST, never sells; values holdings after 15:35"},
]
# Removed 6 Nov 2026 at the user's request (code and records kept): Crypto trend (futures_lab, kind "trend"),
# NSE momentum swing (angelone_bot swing_bot.py, kind "swing"), NSE expiry iron fly (nse_fno_lab, kind "expiry").


def _xirr(flows):
    from datetime import date as _d
    if not flows or all(a >= 0 for _, a in flows) or all(a <= 0 for _, a in flows):
        return None
    t0 = flows[0][0]
    npv = lambda r: sum(a / (1 + r) ** ((d - t0).days / 365.25) for d, a in flows)
    lo, hi = -0.99, 10.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if npv(lo) * npv(mid) <= 0 else (mid, hi)
    return (lo + hi) / 2


def collect_sip(b):
    from datetime import date as _d
    d = b["dir"]
    st = _json(d / "state.json", {})
    bt = _json(d / "trader" / "backtest.json", {})
    values = st.get("values") or []
    value = values[-1][2] if values else st.get("invested", 0) - st.get("charges", 0)
    flows = [(_d.fromisoformat(m + "-01"), -1.0 * (st.get("invested", 0) / max(1, len(st.get("months", {})))))
             for m in sorted(st.get("months", {}))]
    x = _xirr(flows + [(_d.fromisoformat(values[-1][0]), value)]) if flows and values else None
    env = {}
    try:
        for line in (d / ".env").read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                if k.strip() in ("ETFS", "SIP_AMOUNT", "SIP_DAY", "PAUSE_BELOW_200DMA", "LIVE_TRADING"):
                    env[k.strip()] = v.strip()
    except OSError:
        pass
    scen = {k: {kk: vv for kk, vv in v.items() if kk != "units"} for k, v in (bt.get("scenarios") or {}).items()}
    return {"id": b["id"], "name": b["name"], "cur": b["cur"], "hours": b["hours"], "kind": "sip",
            "settings": env, "invested": st.get("invested", 0), "value": value, "cash": st.get("cash", 0),
            "charges": st.get("charges", 0), "xirr_pct": round(x * 100, 1) if x is not None else None,
            "holdings": st.get("holdings", {}), "purchases": (st.get("purchases") or [])[::-1][:200],
            "months": st.get("months", {}), "values": values[-400:],
            "backtest": {"run_at": bt.get("run_at"), "sip_amount": bt.get("sip_amount"), "scenarios": scen,
                         "start_years": bt.get("start_years", {})}}
DECISION_RE = re.compile(r"(ENTRY|EXIT PLAN|TIME MACHINE|BUDGET|RISK|BROKER|ALERT|SCANNER|NEWS|DAILY SUMMARY|ERROR|CRASH|Market closed)")


def _json(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _jsonl(p):
    try:
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        return []


def _tail(p, n=400):
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []
    return [l for l in lines if DECISION_RE.search(l)][-60:]


def _summary(trades):
    n = len(trades)
    if not n:
        return {"count": 0}
    pnl = sum(t.get("pnl", 0) for t in trades)
    wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
    return {"count": n, "wins": wins, "win_pct": round(wins / n * 100, 1), "pnl": round(pnl, 2),
            "avg_r": round(sum(t.get("r", 0) for t in trades) / n, 2),
            "targets": sum(1 for t in trades if t.get("outcome") == "target" or "target" in str(t.get("reason", ""))),
            "stops": sum(1 for t in trades if t.get("outcome") == "stop" or "stop" in str(t.get("reason", "")))}


def _pid_alive(pid_text):
    """True if the process in bot.pid ("pid 1234 since ...") is still running (Windows, read-only check)."""
    m = re.search(r"\d+", pid_text or "")
    if not m:
        return False
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, int(m.group()))      # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
    k32.CloseHandle(h)
    return bool(ok) and code.value == 259                     # STILL_ACTIVE


def _by_day(paper, shadows):
    """Per trading day: trades the bot took vs signals it skipped (followed on paper), newest day first."""
    days = {}
    for kind, ts in (("taken", paper), ("skipped", shadows)):
        for t in ts:
            d = t.get("day") or str(t.get("opened_at") or t.get("time") or "")[:10]
            if not d:
                continue
            row = days.setdefault(d, {"day": d, "taken": 0, "taken_pnl": 0.0, "taken_won": 0,
                                      "skipped": 0, "skipped_pnl": 0.0, "skipped_won": 0})
            row[kind] += 1
            row[kind + "_pnl"] = round(row[kind + "_pnl"] + (t.get("pnl") or 0), 2)
            row[kind + "_won"] += (t.get("pnl") or 0) > 0
    return sorted(days.values(), key=lambda r: r["day"], reverse=True)


MONEY_KEYS = ("BUDGET", "CASH_BUFFER_PCT", "MAX_POSITION_PCT", "MAX_ORDER", "MAX_LOSS_PER_TRADE", "MAX_DAILY_LOSS",
              "MAX_TRADES_PER_DAY", "MAX_OPEN_POSITIONS", "LEVERAGE", "LIVE_TRADING", "QUOTE", "FEE_RATE")


def _env(d, keys):
    """Only the named, non-secret settings from a bot's .env."""
    env = {}
    try:
        for line in (d / ".env").read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                if k.strip() in keys:
                    env[k.strip()] = v.split("#")[0].strip()
    except OSError:
        pass
    return env


def _n(s, k, default):
    try:
        return float(s.get(k) or default)
    except ValueError:
        return default


def _size(sig, s, cash, crypto):
    """Roughly what the bot would have bought for a signal (same four limits as its budget step)."""
    e, stop = sig.get("entry"), sig.get("stop")
    if not e or not stop or e <= stop:
        return None
    B, lev = _n(s, "BUDGET", 0), _n(s, "LEVERAGE", 1)
    fee = (_n(s, "FEE_RATE", 0.0059 if s.get("QUOTE", "INR").upper() == "INR" else 0.002)) if crypto else 0
    lim = {"max loss": _n(s, "MAX_LOSS_PER_TRADE", 0) / ((e - stop) + fee * (e + stop)),
           "position cap": B * _n(s, "MAX_POSITION_PCT", 100) / 100 * lev / e,
           "max order": _n(s, "MAX_ORDER", B) / e,
           "cash": max(0.0, min(cash or B, B) - B * _n(s, "CASH_BUFFER_PCT", 0) / 100) * lev / e}
    by = min(lim, key=lim.get)
    q = int(lim[by] * 1e6) / 1e6 if crypto else int(lim[by])
    return {"qty": q, "value": round(q * e, 2), "risk": round(q * (e - stop), 2), "decided_by": by}


def _per_symbol(paper, positions, shadows_open, shadows_closed):
    rows = {}

    def row(sym):
        return rows.setdefault(sym, {"symbol": sym, "trades": 0, "qty": 0, "capital": 0.0, "pnl": 0.0, "open_qty": 0,
                                     "open_value": 0.0, "shadow_trades": 0, "shadow_capital": 0.0, "shadow_pnl": 0.0})
    for t in paper:
        r = row(t.get("symbol") or t.get("market"))
        r["trades"] += 1
        r["qty"] += t.get("qty", 0)
        r["capital"] += t.get("qty", 0) * t.get("entry", 0)
        r["pnl"] += t.get("pnl", 0)
    for p in positions:
        r = row(p.get("symbol") or p.get("market"))
        r["open_qty"] += p.get("qty", 0)
        r["open_value"] += p.get("qty", 0) * p.get("entry", 0)
    for sh in list(shadows_closed) + list(shadows_open):
        r = row(sh.get("key"))
        r["shadow_trades"] += 1
        r["shadow_capital"] += (sh.get("qty") or 0) * (sh.get("entry") or 0)
        r["shadow_pnl"] += sh.get("pnl") or 0
    for r in rows.values():
        for k in ("capital", "pnl", "open_value", "shadow_capital", "shadow_pnl"):
            r[k] = round(r[k], 2)
    return sorted(rows.values(), key=lambda r: -(r["capital"] + r["open_value"] + r["shadow_capital"]))


def _capital(st, s, paper, positions):
    B = _n(s, "BUDGET", 0)
    cash = st.get("paper_balance")
    in_pos = sum(p.get("qty", 0) * p.get("entry", 0) for p in positions)
    at_risk = sum(p.get("max_loss") or p.get("qty", 0) * (p.get("entry", 0) - p.get("stop", 0)) for p in positions)
    day = st.get("day")
    today = [t for t in paper if str(t.get("time", "")).startswith(str(day))] if day else []
    traded = sum(t.get("qty", 0) * t.get("entry", 0) for t in paper)
    return {"budget": B, "cash": cash, "in_positions": round(in_pos, 2), "at_risk": round(at_risk, 2),
            "used_pct": round(in_pos / B * 100, 1) if B else None,
            "free": round(max(0.0, (cash if cash is not None else B) - B * _n(s, "CASH_BUFFER_PCT", 0) / 100), 2),
            "buffer": round(B * _n(s, "CASH_BUFFER_PCT", 0) / 100, 2),
            "max_order": _n(s, "MAX_ORDER", B), "max_position": round(B * _n(s, "MAX_POSITION_PCT", 100) / 100, 2),
            "max_loss_trade": _n(s, "MAX_LOSS_PER_TRADE", 0), "max_loss_day": _n(s, "MAX_DAILY_LOSS", 0),
            "max_trades_day": int(_n(s, "MAX_TRADES_PER_DAY", 0)), "max_open": int(_n(s, "MAX_OPEN_POSITIONS", 0)),
            "leverage": _n(s, "LEVERAGE", 1), "live": s.get("LIVE_TRADING", "false").lower() == "true",
            "entries_today": len(st.get("entries_today") or []),
            "pnl_today": round(sum(t.get("pnl", 0) for t in today), 2),
            "total_traded": round(traded, 2), "trades_total": len(paper)}


def collect():
    out = []
    for b in BOTS:
        if b.get("kind") == "sip":
            out.append(collect_sip(b))
            continue
        if b.get("kind") == "expiry":
            from expiry_view import collect_expiry
            out.append(collect_expiry(b))
            continue
        if b.get("kind") == "trend":
            from trend_view import collect_trend
            out.append(collect_trend(b))
            continue
        if b.get("kind") == "swing":
            from swing_view import collect_swing
            out.append(collect_swing(b))
            continue
        if b.get("kind") == "freqtrade":
            from ft_view import collect_ft
            out.append(collect_ft(b))
            continue
        d, tr = b["dir"], b["dir"] / "trader"
        st = _json(d / "state.json", {})
        paper = _jsonl(tr / "paper_trades.jsonl")
        shadows_closed = _jsonl(tr / "shadow_trades.jsonl")
        signals = _jsonl(tr / "signals.jsonl")
        wl = _json(tr / "watchlist.json", {}).get("hits", [])
        pid = (d / "bot.pid").read_text(encoding="utf-8").strip() if (d / "bot.pid").exists() else ""
        log_age = None
        if (d / "bot.log").exists():
            log_age = int(datetime.now().timestamp() - (d / "bot.log").stat().st_mtime)
        s = _env(d, MONEY_KEYS)
        positions = list((st.get("positions") or {}).values())
        shadows_open = list((st.get("shadows") or {}).values())
        crypto = b["id"] == "coindcx"
        for sig in signals[-200:]:
            if sig.get("qty") is None:
                sig["est"] = _size(sig, s, st.get("paper_balance"), crypto)
        out.append({
            "capital": _capital(st, s, paper, positions),
            "per_symbol": _per_symbol(paper, positions, shadows_open, shadows_closed),
            "id": b["id"], "name": b["name"], "cur": b["cur"], "hours": b["hours"], "exists": d.exists(),
            "started": pid, "log_age_s": log_age, "alive": _pid_alive(pid),
            "paper_cash": st.get("paper_balance"), "realized_pnl": st.get("realized_pnl", 0),
            "positions": positions,
            "shadows_open": shadows_open,
            "paper_trades": paper[-200:][::-1], "paper_summary": _summary(paper),
            "shadow_trades": shadows_closed[-200:][::-1], "shadow_summary": _summary(shadows_closed),
            "by_day": _by_day(paper, shadows_closed),
            "signals": signals[-200:][::-1],
            "signals_summary": {"total": len(signals), "bought": sum(s.get("decision") == "BOUGHT" for s in signals)},
            "watchlist": [{k: w.get(k) for k in ("symbol", "market", "tags", "above_ma20", "level_20d", "spread_pct",
                                                  "turnover_cr", "dollar_volume_m", "turnover")} for w in wl],
            "news_risk": {k: v for k, v in (st.get("news") or {}).items() if v},
            "log": _tail(d / "bot.log"),
        })
    return {"generated": datetime.now(timezone.utc).isoformat(), "bots": out}


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Paper Trading Dashboard</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--mute:#667085;--line:#e4e7ec;--up:#067647;--down:#b42318;--warn:#b54708;--chip:#eef2f6;--acc:#3b5bdb}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#181b21;--ink:#e6e8ec;--mute:#98a2b3;--line:#2a2f37;--up:#47cd89;--down:#f97066;--warn:#fdb022;--chip:#232830;--acc:#7c9cff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{padding:16px;display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);background:var(--card);position:sticky;top:0;z-index:2}
h1{font-size:18px;margin:0}.mute{color:var(--mute)}main{padding:16px;max-width:1400px;margin:auto}
.tabs{display:flex;gap:6px;flex-wrap:wrap}.tab{border:1px solid var(--line);background:var(--card);color:var(--ink);padding:6px 12px;border-radius:999px;cursor:pointer}
.tab.on{background:var(--acc);border-color:var(--acc);color:#fff}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin:12px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}.card b{display:block;font-size:20px;margin-top:2px}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;margin:12px 0}
h2{font-size:15px;margin:0 0 8px}.wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap;vertical-align:top}th{color:var(--mute);font-weight:600}
td.r{white-space:normal;min-width:240px}.up{color:var(--up)}.down{color:var(--down)}.warn{color:var(--warn)}
.chip{display:inline-block;background:var(--chip);border-radius:999px;padding:1px 8px;margin:1px;font-size:12px}
.BOUGHT{color:var(--up);font-weight:600}.SKIPPED{color:var(--mute)}pre{white-space:pre-wrap;font-size:12px;margin:0;color:var(--mute)}
.empty{color:var(--mute);padding:6px 0}
.bar{height:8px;background:var(--chip);border-radius:999px;overflow:hidden;margin:4px 0 6px}.bar span{display:block;height:100%;background:var(--acc)}.note{font-size:12px;color:var(--mute)}
</style></head><body>
<header><div><h1>Paper Trading Dashboard</h1><div class="mute" id="upd">loading…</div></div><div class="tabs" id="tabs"></div></header>
<main id="main"></main>
<script>
/*SIP_JS*/
let DATA=null, TAB=localStorage.getItem('tab')||'angelone';
const f=(v,d=2)=>v==null||v===''?'–':Number(v).toLocaleString(undefined,{maximumFractionDigits:d,minimumFractionDigits:Math.min(d,2)});
const px=v=>v==null?'–':(Math.abs(v)>=1000?f(v,2):Math.abs(v)>=1?f(v,4):Number(v).toPrecision(4));
const t=s=>{if(!s)return '–';const d=new Date(s);return isNaN(d)?s:d.toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})};
const cls=v=>v>0?'up':v<0?'down':'';const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function table(cols,rows,empty){if(!rows.length)return `<div class="empty">${empty}</div>`;
 return `<div class="wrap"><table><tr>${cols.map(c=>`<th>${c[0]}</th>`).join('')}</tr>${rows.map(r=>`<tr>${cols.map(c=>`<td class="${c[2]?c[2](r):''}">${c[1](r)}</td>`).join('')}</tr>`).join('')}</table></div>`}
function bot(b){const c=b.cur, S=b.paper_summary, H=b.shadow_summary;
 const recent=b.log_age_s!=null&&b.log_age_s<1800, alive=b.alive||recent;
 const unreal=b.positions.reduce((a,p)=>a+(p.last!=null?(p.last-p.entry)*p.qty:0),0);
 let h=`<div class="note">${esc(b.hours)} · ${!alive?'<span class="down">● stopped</span>':recent?'<span class="up">● running</span>':'<span class="up">● running</span> <span class="mute">(market closed, waiting for the next session)</span>'} ${b.started?'· '+esc(b.started):''}</div>
 <div class="cards">
  <div class="card">Paper cash<b>${c}${f(b.paper_cash)}</b></div>
  <div class="card">Realized P&L<b class="${cls(b.realized_pnl)}">${c}${f(b.realized_pnl)}</b></div>
  <div class="card">Open P&L<b class="${cls(unreal)}">${c}${f(unreal)}</b></div>
  <div class="card">Paper trades<b>${S.count||0}</b><span class="mute">${S.count?`${S.win_pct}% won · avg ${S.avg_r}R`:'none yet'}</span></div>
  <div class="card">Signals<b>${b.signals_summary.total}</b><span class="mute">${b.signals_summary.bought} bought</span></div>
  <div class="card">Skipped signals, followed anyway<b class="${cls(H.pnl)}">${H.count?c+f(H.pnl):'–'}</b><span class="mute">${H.count?`${H.count} closed · ${H.win_pct}% won · avg ${H.avg_r}R`:'none closed yet'}</span></div>
 </div>`;
 const crypto=b.id==='coindcx', K=b.capital||{}, used=K.used_pct??0, qn=crypto?'Units':'Shares';
 if(b.by_day){const D=b.by_day, tk=S.pnl||0, sk=H.pnl||0, n=H.count||0;
  const verdict=!n?'No skipped trades have closed yet.'
   :n<200?`Too early to judge: ${n} skipped trades closed so far; about 200 are needed.`
   :sk>tk?'<span class="warn">The skipped trades are doing better than the taken ones: the replay check may be too strict.</span>'
   :'<span class="up">The replay check is helping: the taken trades are doing better than the skipped ones.</span>';
  h+=`<section><h2>Taken vs skipped</h2><div class="note">Is the Time Machine check helping? Trades the bot took, against signals it skipped (followed on paper with the same sizing). ${verdict}</div>
  <div class="cards">
   <div class="card">Taken<b class="${cls(tk)}">${S.count?c+f(tk):'–'}</b><span class="mute">${S.count?`${S.count} trades · ${S.win_pct}% won · avg ${S.avg_r}R`:'none yet'}</span></div>
   <div class="card">Skipped<b class="${cls(sk)}">${n?c+f(sk):'–'}</b><span class="mute">${n?`${n} trades · ${H.win_pct}% won · avg ${H.avg_r}R`:'none yet'}</span></div>
   <div class="card">Skipping saved<b class="${cls(-sk)}">${n?c+f(-sk):'–'}</b><span class="mute">negative = skipping cost money</span></div>
  </div>${table([['Day',r=>esc(r.day)],['Taken',r=>r.taken||'–'],['Won',r=>r.taken?r.taken_won:'–'],
   ['Taken P&L',r=>r.taken?c+f(r.taken_pnl):'–',r=>cls(r.taken_pnl)],['Skipped',r=>r.skipped||'–'],['Won',r=>r.skipped?r.skipped_won:'–'],
   ['Skipped P&L',r=>r.skipped?c+f(r.skipped_pnl):'–',r=>cls(r.skipped_pnl)]],D,'No closed trades yet.')}</section>`;}
 h+=`<section><h2>Capital</h2>
 <div class="bar" title="${f(used,1)}% of budget in open positions"><span style="width:${Math.min(100,used)}%"></span></div>
 <div class="note">${c}${f(K.in_positions)} of ${c}${f(K.budget,0)} budget in open positions (${f(used,1)}%) · ${K.live?'<span class="down">LIVE money</span>':'paper money'}${K.leverage>1?` · leverage ${K.leverage}×`:''}</div>
 <div class="cards">
  <div class="card">Budget<b>${c}${f(K.budget,0)}</b><span class="mute">starting capital</span></div>
  <div class="card">Cash now<b>${c}${f(K.cash)}</b><span class="mute">${c}${f(K.buffer,0)} always kept aside</span></div>
  <div class="card">In open positions<b>${c}${f(K.in_positions)}</b><span class="mute">${c}${f(K.at_risk)} at risk to the stops</span></div>
  <div class="card">Free to trade<b>${c}${f(K.free)}</b><span class="mute">cash minus the reserve</span></div>
  <div class="card">Biggest single trade<b>${c}${f(Math.min(K.max_order,K.max_position),0)}</b><span class="mute">max order ${c}${f(K.max_order,0)} · cap ${c}${f(K.max_position,0)}</span></div>
  <div class="card">Max loss per trade<b>${c}${f(K.max_loss_trade,0)}</b><span class="mute">${K.budget?f(K.max_loss_trade/K.budget*100,1):'–'}% of budget</span></div>
  <div class="card">Today<b class="${cls(K.pnl_today)}">${c}${f(K.pnl_today)}</b><span class="mute">${K.entries_today}/${K.max_trades_day} trades · stops for the day at −${c}${f(K.max_loss_day,0)}</span></div>
  <div class="card">Total traded so far<b>${c}${f(K.total_traded,0)}</b><span class="mute">${K.trades_total} trades · ${K.max_open} open at most</span></div>
 </div></section>`;
 h+=`<section><h2>Per ${crypto?'coin':'stock'}: how much was bought and the result</h2><div class="note">${qn} bought and capital put in, for paper trades, open positions, and skipped signals followed on paper.</div>${table([
  [crypto?'Coin':'Stock',r=>esc(r.symbol)],['Paper trades',r=>r.trades||'–'],[qn+' bought',r=>r.trades?f(r.qty,crypto?6:0):'–'],
  ['Capital used',r=>r.trades?c+f(r.capital):'–'],['P&L',r=>r.trades?c+f(r.pnl):'–',r=>cls(r.pnl)],
  ['Open now',r=>r.open_qty?`${f(r.open_qty,crypto?6:0)} (${c}${f(r.open_value)})`:'–'],
  ['Skipped, followed',r=>r.shadow_trades||'–'],['Their capital',r=>r.shadow_trades?c+f(r.shadow_capital):'–'],
  ['Their P&L',r=>r.shadow_trades?c+f(r.shadow_pnl):'–',r=>cls(r.shadow_pnl)]],
  b.per_symbol||[],'Nothing bought yet, on paper or followed.')}</section>`;
 h+=`<section><h2>Open positions</h2>${table([
  ['Symbol',p=>esc(p.symbol||p.market)],['Setup',p=>esc(p.setup)],['Opened',p=>t(p.opened_at)],['Qty',p=>p.qty],
  ['Capital',p=>c+f(p.qty*p.entry)],['Entry',p=>px(p.entry)],['Stop loss',p=>px(p.stop)],['Target',p=>px(p.target)],['Now',p=>px(p.last)],
  ['P&L',p=>p.last!=null?c+f((p.last-p.entry)*p.qty):'–',p=>cls((p.last??p.entry)-p.entry)],
  ['R',p=>p.last!=null?f((p.last-p.entry)/(p.risk_per_share||p.risk_per_unit||1),2):'–',p=>cls((p.last??p.entry)-p.entry)],
  ['Max loss',p=>c+f(p.max_loss)]],b.positions,'No open positions.')}</section>`;
 h+=`<section><h2>Closed paper trades</h2>${table([
  ['Closed',r=>t(r.time)],['Symbol',r=>esc(r.symbol||r.market)],['Qty',r=>r.qty],['Capital',r=>c+f(r.qty*r.entry)],['Entry',r=>px(r.entry)],['Exit',r=>px(r.price)],
  ['P&L',r=>c+f(r.pnl),r=>cls(r.pnl)],['R',r=>f(r.r,2),r=>cls(r.r)],['Charges',r=>r.charges!=null?c+f(r.charges):(r.tds!=null?'TDS '+c+f(r.tds):'–')],
  ['Why it closed',r=>esc(r.reason)]],b.paper_trades,'No closed paper trades yet.')}</section>`;
 h+=`<section><h2>Signals: every breakout the bot saw, and what it did</h2>${table([
  ['Time',r=>t(r.time)],['Symbol',r=>esc(r.symbol)],['Setup',r=>esc(r.setup)],['Trigger',r=>px(r.trigger_close)],
  ['Entry',r=>px(r.entry)],['Stop loss',r=>px(r.stop)],['Target',r=>px(r.target)],['Risk',r=>r.risk_pct!=null?f(r.risk_pct,2)+'%':'–'],
  ['Time Machine',r=>r.time_machine?`${esc(r.time_machine.verdict)}<br><span class="mute">target ${r.time_machine.target_first_pct}% · stop ${r.time_machine.stop_first_pct}% · ${r.time_machine.avg_r_after_costs}R</span>`:'–'],
  ['Size',r=>r.qty!=null?`${r.qty} (${c}${f(r.value)})`:r.est?`<span class="mute">would be ${f(r.est.qty,crypto?6:0)} (${c}${f(r.est.value)})<br>set by ${esc(r.est.decided_by)}</span>`:'–'],
  ['Decision',r=>`<span class="${r.decision}">${r.decision}</span>`],['Why',r=>esc((r.stage?r.stage+': ':'')+(r.reason||'')),()=>'r']],
  b.signals,'No signals yet. The bot logs one each time a stock closes above its opening range during the entry window.')}</section>`;
 h+=`<section><h2>Skipped signals followed on paper (open)</h2><div class="note">Shows what would have happened without the Time Machine, budget or risk checks. Not counted in paper results.</div>${table([
  ['Opened',r=>t(r.opened_at)],['Symbol',r=>esc(r.key)],['Qty',r=>r.qty??'–'],['Capital',r=>r.qty?c+f(r.qty*r.entry):'–'],['Entry',r=>px(r.entry)],['Stop loss',r=>px(r.stop)],['Target',r=>px(r.target)],
  ['Now',r=>px(r.last)],['P&L',r=>c+f((r.last-r.entry)*r.qty),r=>cls(r.last-r.entry)],['Skipped because',r=>esc(r.skipped_because),()=>'r']],
  b.shadows_open,'None open.')}</section>`;
 h+=`<section><h2>Skipped signals followed on paper (closed)</h2>${table([
  ['Closed',r=>t(r.closed_at)],['Symbol',r=>esc(r.key)],['Entry',r=>px(r.entry)],['Stop loss',r=>px(r.stop)],['Target',r=>px(r.target)],
  ['Qty',r=>r.qty??'–'],['Capital',r=>r.qty?c+f(r.qty*r.entry):'–'],['Exit',r=>px(r.exit)],['Outcome',r=>esc(r.outcome)],['P&L',r=>c+f(r.pnl),r=>cls(r.pnl)],['R',r=>f(r.r,2),r=>cls(r.r)],
  ['Skipped because',r=>esc(r.skipped_because),()=>'r']],b.shadow_trades,'None closed yet.')}</section>`;
 h+=`<section><h2>Watchlist today</h2>${table([
  ['Symbol',w=>esc(w.symbol||w.market)],['Setups',w=>(w.tags||[]).map(x=>`<span class="chip">${esc(x)}</span>`).join('')||'–'],
  ['Uptrend',w=>w.above_ma20==null?'–':w.above_ma20?'<span class="up">yes</span>':'<span class="mute">no (skipped)</span>'],
  ['20-day high',w=>px(w.level_20d)],['News risk',w=>esc((b.news_risk[w.symbol]||[]).join(', '))||'–']],b.watchlist,'Scanner has not run yet today.')}</section>`;
 h+=`<section><h2>Recent decisions (bot.log)</h2><pre>${b.log.map(esc).join('\n')||'No log yet.'}</pre></section>`;
 return h}
function render(){if(!DATA)return;if(!DATA.bots.some(x=>x.id===TAB))TAB=DATA.bots[0].id;document.getElementById('tabs').innerHTML=DATA.bots.map(b=>`<button class="tab ${b.id===TAB?'on':''}" onclick="TAB='${b.id}';localStorage.setItem('tab',TAB);render()">${esc(b.name)}</button>`).join('');
 const b=DATA.bots.find(x=>x.id===TAB)||DATA.bots[0];document.getElementById('main').innerHTML=b.kind==='sip'?sipView(b):b.kind==='freqtrade'?ftView(b):b.kind==='expiry'?expiryView(b):b.kind==='swing'?swingView(b):b.kind==='trend'?trendView(b):bot(b);
 document.getElementById('upd').textContent='Paper mode · updated '+new Date(DATA.generated).toLocaleTimeString()+' · refreshes every 30 s · times in your local time'}
async function load(){try{const r=await fetch('/api/data');DATA=await r.json();render()}catch(e){document.getElementById('upd').textContent='Cannot reach the dashboard server: '+e}}
load();setInterval(load,30000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/data"):
            body, ctype = json.dumps(collect(), default=str).encode(), "application/json"
        elif self.path in ("/", "/index.html"):
            from sip_view import SIP_JS
            from ft_view import FT_JS
            from expiry_view import EXPIRY_JS
            from swing_view import SWING_JS
            from trend_view import TREND_JS
            body, ctype = PAGE.replace("/*SIP_JS*/", SIP_JS + FT_JS + EXPIRY_JS + SWING_JS + TREND_JS).encode(), "text/html; charset=utf-8"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"Dashboard on http://localhost:{PORT}  (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
