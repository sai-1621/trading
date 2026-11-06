"""The Freqtrade crypto tab, shared by the local dashboard (dashboard.py) and the shareable page (build_share.py).

Reads the bot's trade database read-only and its public config (never config-private.json), and fetches
public Binance prices (no login). It never changes anything in the bot's folder.
"""
import json
import re
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone

_CACHE = {}


def _get(url, ttl):
    hit = _CACHE.get(url)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            data = json.loads(r.read())
    except Exception:
        return hit[1] if hit else None
    _CACHE[url] = (time.time(), data)
    return data


def _closes(pair, n=260):
    sym = pair.replace("/", "")
    k = _get(f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=1d&limit={n}", 600) or []
    # each row: open time, open, high, low, close, ... ; the last row is today's unfinished candle
    return [(int(r[0]), float(r[2]), float(r[3]), float(r[4])) for r in k]


def _setups(pairs):
    """Where each coin stands against the strategy's rule, from finished daily candles."""
    btc = _closes("BTC/USDT")[:-1]
    btc_ok = None
    if len(btc) >= 200:
        sma = sum(c for *_, c in btc[-200:]) / 200
        btc_ok = {"close": btc[-1][3], "sma200": round(sma, 2), "ok": btc[-1][3] > sma}
    rows = []
    for p in pairs:
        k = _closes(p)
        if len(k) < 60:
            rows.append({"pair": p})
            continue
        done, last = k[:-1], k[-1][3]
        hi55 = max(h for _, h, _, _ in done[-55:])   # level today's close must beat
        lo20 = min(lo for _, _, lo, _ in done[-20:])
        rows.append({"pair": p, "price": last, "close": done[-1][3], "hi55": hi55, "lo20": lo20,
                     "to_buy_pct": round((hi55 / last - 1) * 100, 1), "above_exit_pct": round((last / lo20 - 1) * 100, 1)})
    return btc_ok, rows


def _stoploss(strategy_file):
    try:
        m = re.search(r"^\s*stoploss\s*=\s*(-?[\d.]+)", strategy_file.read_text(encoding="utf-8"), re.M)
        return float(m.group(1)) if m else None
    except OSError:
        return None


def _rule(strategy_file, strat):
    """JUMP / VOLX / MAX_HOURS / trailing settings of a PumpRider class (a variant overrides the base)."""
    try:
        src = strategy_file.read_text(encoding="utf-8")
    except OSError:
        return {}
    blocks = re.split(r"^class ", src, flags=re.M)
    base = next((x for x in blocks if x.startswith("PumpRider(")), "")
    own = next((x for x in blocks if x.startswith(strat + "(")), base)
    pats = {"jump": [r"\bJUMP = ([\d.]+)", r"JUMP, VOLX = ([\d.]+),"],
            "volx": [r"\bVOLX = ([\d.]+)", r"JUMP, VOLX = [\d.]+, ([\d.]+)"],
            "max_hours": [r"MAX_HOURS = (\d+)"],
            "trail": [r"trailing_stop_positive = ([\d.]+)", r"trailing_stop_positive, trailing_stop_positive_offset = ([\d.]+),"],
            "trail_start": [r"trailing_stop_positive_offset = ([\d.]+)", r"trailing_stop_positive, trailing_stop_positive_offset = [\d.]+, ([\d.]+)"]}
    out = {}
    for key, plist in pats.items():
        for blk in (own, base):
            m = next((re.search(pt, blk) for pt in plist if re.search(pt, blk)), None)
            if m:
                out[key] = float(m.group(1))
                break
    return out


def _movers(rule, n=12):
    """Biggest 24h gainers on Binance USDT futures, and whether their last closed hour passes the buy rule."""
    tick = _get("https://fapi.binance.com/fapi/v1/ticker/24hr", 60) or []
    tick = [t for t in tick if t.get("symbol", "").endswith("USDT") and float(t.get("quoteVolume") or 0) >= 2e6]
    tick.sort(key=lambda t: -abs(float(t["priceChangePercent"])) if rule.get("abs") else -float(t["priceChangePercent"]))
    out = []
    for t in tick[:n]:
        k = _get(f"https://fapi.binance.com/fapi/v1/klines?symbol={t['symbol']}&interval=1h&limit=52", 300) or []
        row = {"pair": t["symbol"][:-4] + "/USDT", "price": float(t["lastPrice"]),
               "chg24": float(t["priceChangePercent"]), "qv24_m": round(float(t["quoteVolume"]) / 1e6, 1)}
        if len(k) >= 51:
            done = k[:-1]
            o, h, lo, c = (float(done[-1][i]) for i in (1, 2, 3, 4))
            qv = [float(x[7]) for x in done]
            avg = sum(qv[-49:-1]) / 48
            hi24 = max(float(x[2]) for x in done[-25:-1])
            row.update({"last_hour": time.strftime("%H:%M", time.localtime(done[-1][0] / 1000)),
                        "jump": round((c / o - 1) * 100, 1), "vol_x": round(qv[-1] / avg, 1) if avg else None,
                        "above_hi24": c > hi24, "closepos": round((c - lo) / (h - lo), 2) if h > lo else None})
            if rule.get("abs"):
                row["passes"] = bool(abs(row["jump"]) >= rule["jump"] * 100 and (row["vol_x"] or 0) >= rule["volx"])
            else:
                row["passes"] = bool(rule and row["jump"] >= rule.get("jump", 1) * 100
                                     and (row["vol_x"] or 0) >= rule.get("volx", 99)
                                     and row["above_hi24"] and (row["closepos"] or 0) >= 0.6)
        out.append(row)
    return out


def collect_ft(b):
    d = b["dir"]
    ud = d / "user_data"
    mode = b.get("mode", "trend")
    try:
        cfg = json.loads((ud / b.get("config", "config.json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    if b.get("overlay"):
        try:
            cfg = {**cfg, **json.loads((ud / b["overlay"]).read_text(encoding="utf-8"))}
        except (OSError, ValueError):
            pass
    pairs = (cfg.get("exchange") or {}).get("pair_whitelist") or []
    wallet0 = float(cfg.get("dry_run_wallet") or 0)
    strat = cfg.get("strategy", "")
    trades = []
    db = ud / b.get("db", "tradesv3.dryrun.sqlite")
    if db.exists():
        try:
            con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)
            con.row_factory = sqlite3.Row
            trades = [dict(r) for r in con.execute(
                "select id, pair, is_open, open_rate, close_rate, amount, stake_amount, open_date, close_date, "
                "close_profit, close_profit_abs, exit_reason, stop_loss, enter_tag, fee_open_cost, fee_close_cost, is_short, "
                "coalesce(funding_fees, 0) as funding_fees "
                "from trades order by id desc")]
            con.close()
        except sqlite3.Error:
            trades = []
    prices = {}
    fut = cfg.get("trading_mode") == "futures"
    tick = _get("https://fapi.binance.com/fapi/v1/ticker/price" if fut else "https://api.binance.com/api/v3/ticker/price", 30) or []
    # open trades too: coins from the auto-refreshing list are not in the config's fixed list
    want = {p.split(":")[0].replace("/", ""): p for p in list(pairs) + [t["pair"] for t in trades if t["is_open"]]}
    for t in tick:
        if t.get("symbol") in want:
            prices[want[t["symbol"]]] = float(t["price"])
    open_t, closed = [], []
    for t in trades:
        if t["is_open"]:
            px = prices.get(t["pair"])
            t["price"] = px
            t["value"] = round(px * t["amount"], 2) if px else None
            sign = -1 if t.get("is_short") else 1
            # price move, minus the entry fee, plus funding received (negative = paid); futures charge funding
            # every few hours, so it can outweigh the price move on crowded coins
            fund = t.get("funding_fees") or 0.0
            t["funding"] = round(fund, 2)
            if px:
                pnl = sign * (px - t["open_rate"]) * t["amount"] - (t.get("fee_open_cost") or 0) + fund
                t["pnl"] = round(pnl, 2)
                t["pnl_pct"] = round(pnl / t["stake_amount"] * 100, 2) if t.get("stake_amount") else None
            else:
                t["pnl"] = t["pnl_pct"] = None
            open_t.append(t)
        else:
            closed.append(t)
    realized = round(sum(t["close_profit_abs"] or 0 for t in closed), 2)
    in_trades = round(sum(t["stake_amount"] or 0 for t in open_t), 2)
    unreal = round(sum(t["pnl"] or 0 for t in open_t), 2)
    wins = sum((t["close_profit_abs"] or 0) > 0 for t in closed)
    log = ud / "logs" / b.get("log", "freqtrade.log")
    log_age = int(time.time() - log.stat().st_mtime) if log.exists() else None
    lines = []
    if log.exists():
        try:
            with open(log, encoding="utf-8", errors="replace") as fh:
                lines = [ln.rstrip() for ln in fh.readlines()[-3000:]
                         if not re.search(r"heartbeat|uvicorn|numexpr|Sending rpc message: \{'type': (status|startup)", ln)]
        except OSError:
            pass
    sfile = ud / "strategies" / b.get("strategy_file", f"{strat}.py")
    fast = mode in ("pump", "ml")
    rule = _rule(sfile, strat) if mode == "pump" else ({"jump": 0.05, "volx": 3.0, "abs": True} if mode == "ml" else {})
    btc, setups = _setups(pairs) if not fast else (None, [])
    movers = _movers(rule) if fast else []
    model = {}
    if mode == "ml":
        try:
            model = json.loads((ud / "models" / "pumpml.json").read_text(encoding="utf-8"))
            model.pop("features", None)
        except (OSError, ValueError):
            model = {}
        if b.get("min_pred") is not None:
            model["min_pred"] = b["min_pred"]
    return {"id": b["id"], "name": b["name"], "cur": b["cur"], "hours": b["hours"], "kind": "freqtrade", "mode": mode,
            "rule": rule, "movers": movers, "model": model, "pairs_count": len(pairs), "pairs_label": b.get("pairs_label"),
            "exists": d.exists(), "running": log_age is not None and log_age < 180, "log_age": log_age,
            "settings": {"strategy": strat, "exchange": (cfg.get("exchange") or {}).get("name"),
                         "dry_run": cfg.get("dry_run", True), "timeframe": cfg.get("timeframe"),
                         "max_open_trades": cfg.get("max_open_trades"), "stake": cfg.get("stake_currency"),
                         "tradable_ratio": cfg.get("tradable_balance_ratio"),
                         "stoploss": _stoploss(sfile), "pairs": pairs if len(pairs) <= 30 else []},
            "wallet_start": wallet0, "realized": realized, "unrealized": unreal, "in_trades": in_trades,
            "equity": round(wallet0 + realized + unreal, 2), "cash": round(wallet0 + realized - in_trades, 2),
            "trades_closed": len(closed), "wins": wins, "open": open_t, "closed": closed[:200],
            "btc": btc, "setups": setups, "log": lines[-60:],
            "checked": datetime.now(timezone.utc).isoformat()}


FT_JS = r"""
function ftView(b) {
  const c = b.cur, S = b.settings || {};
  const E = s => String(s ?? '').replace(/[&<>"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch]));
  const n = (v, d = 2) => v == null ? '–' : Number(v).toLocaleString(undefined, {maximumFractionDigits: d, minimumFractionDigits: Math.min(d, 2)});
  const p = v => v == null ? '–' : Math.abs(v) >= 100 ? n(v, 2) : Math.abs(v) >= 1 ? n(v, 4) : Number(v).toPrecision(4);
  const m = v => v == null ? '–' : (v < 0 ? '−' : '') + n(Math.abs(v), 2) + ' ' + c;
  const cl = v => v > 0 ? 'up' : v < 0 ? 'down' : '';
  const dt = s => { if (!s) return '–'; const d = new Date(String(s).replace(' ', 'T') + (/[zZ+]/.test(s) ? '' : 'Z')); return isNaN(d) ? s : d.toLocaleString(undefined, {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'}); };
  const tbl = (head, rows, empty) => rows.length ? `<div class="tbl wrap"><table><tr>${head.map(h => `<th>${h}</th>`).join('')}</tr>${rows.map(r => `<tr>${r.map(x => `<td>${x}</td>`).join('')}</tr>`).join('')}</table></div>` : `<div class="empty">${empty}</div>`;
  const used = b.equity ? Math.min(100, b.in_trades / b.equity * 100) : 0;
  const status = b.running ? '<span class="up">running</span>' : `<span class="down">not running</span>${b.log_age != null ? ' (last log ' + Math.round(b.log_age / 60) + ' min ago)' : ''}`;
  let h = `<p class="note">${E(b.hours)} ${S.dry_run === false ? '<b class="down">LIVE: real orders.</b>' : 'Paper mode (dry-run): no real orders.'} Bot ${status}. Strategy ${E(S.strategy)} on ${E(S.exchange)} ${E(S.timeframe)} candles.</p>`;
  const T = (k, v, sub, cls = '') => `<div class="tile card"><div class="k">${k}</div><div class="v"><b class="${cls}">${v}</b></div>${sub ? `<div class="s note">${sub}</div>` : ''}</div>`;
  const bar = `<div style="height:8px;background:var(--line);border-radius:999px;overflow:hidden;margin:4px 0"><span style="display:block;height:100%;width:${used.toFixed(0)}%;background:var(--up)"></span></div>`;
  h += `<div class="tiles cards">` +
    T('Wallet now', m(b.equity), 'started ' + m(b.wallet_start)) +
    T('Profit, closed trades', m(b.realized), `${b.trades_closed} trades, ${b.wins} won`, cl(b.realized)) +
    T('Profit, open trades', m(b.unrealized), '', cl(b.unrealized)) +
    T('In trades', m(b.in_trades), bar + `${used.toFixed(0)}% of wallet · cash ${m(b.cash)}`) +
    T('Open trades', `${b.open.length} / ${S.max_open_trades ?? '–'}`, 'each about ' + m(S.max_open_trades ? (b.equity * (S.tradable_ratio || 1)) / S.max_open_trades : null)) +
    (b.mode === 'ml'
      ? T('Exit rules', 'long +8% / −8%', 'short +6% / −10% · out after 24 h')
      : b.mode === 'pump'
      ? T('Exit rules', `stop ${S.stoploss != null ? (S.stoploss * 100).toFixed(0) + '%' : '–'}`, `trail ${n(((b.rule || {}).trail || 0) * 100, 0)}% once up ${n(((b.rule || {}).trail_start || 0) * 100, 0)}% · sell after ${(b.rule || {}).max_hours ?? '–'} h`)
      : T('Disaster stop', S.stoploss != null ? (S.stoploss * 100).toFixed(0) + '%' : '–', 'plus the 20-day-low exit')) +
    `</div>`;
  if (b.mode === 'pump') {
    const R = b.rule || {};
    h += `<section><h2>Hot movers now (Binance futures, biggest 24h gainers)</h2><p class="note">Buy rule, checked on every closed hour across ${b.pairs_label ? E(b.pairs_label) : b.pairs_count + " coins"}: up at least ${n((R.jump || 0) * 100, 0)}% in the hour, volume at least ${n(R.volx, 0)}× normal, closes above the 24h high near the top of the candle, coin trades at least $2M a day, BTC not down 3% in 4 h.</p>${tbl(['Coin', 'Price', '24h', 'Volume 24h', 'Last hour', 'Hour move', 'Volume ×', 'Above 24h high', 'Buy signal'],
      (b.movers || []).map(x => [E(x.pair), p(x.price), `<span class="${cl(x.chg24)}">${n(x.chg24, 1)}%</span>`, '$' + n(x.qv24_m, 1) + 'M', E(x.last_hour || '–'),
        x.jump == null ? '–' : `<span class="${cl(x.jump)}">${n(x.jump, 1)}%</span>`, x.vol_x == null ? '–' : n(x.vol_x, 1) + '×', x.above_hi24 ? 'yes' : 'no',
        x.passes ? '<b class="up">YES</b>' : '<span class="note">no</span>']), 'Could not load futures prices.')}<p class="note">Most big gainers fail the rule: the jump has to happen within one closed hour, with a volume spike. The bot buys at the next hour's open.</p></section>`;
  }
  if (b.mode === 'ml') {
    const M = b.model || {}, W = M.walk_forward || {}, Y = W.by_year || {};
    h += `<section><h2>Biggest movers now (Binance futures, up or down in 24h)</h2><p class="note">A coin becomes a candidate when a closed hour moves at least 5% up or down on at least 3× normal volume (and it trades at least $2M a day). The model then predicts the profit of going long and of going short after the move, and trades the better side only if it expects at least ${n((M.min_pred || 0.005) * 100, 1)}% after fees. Checked across ${b.pairs_label ? E(b.pairs_label) : b.pairs_count + ' coins'}.</p>${tbl(['Coin', 'Price', '24h', 'Volume 24h', 'Last hour', 'Hour move', 'Volume ×', 'Candidate'],
      (b.movers || []).map(x => [E(x.pair), p(x.price), `<span class="${cl(x.chg24)}">${n(x.chg24, 1)}%</span>`, '$' + n(x.qv24_m, 1) + 'M', E(x.last_hour || '–'),
        x.jump == null ? '–' : `<span class="${cl(x.jump)}">${n(x.jump, 1)}%</span>`, x.vol_x == null ? '–' : n(x.vol_x, 1) + '×',
        x.passes ? '<b>yes, model decides</b>' : '<span class="note">no</span>']), 'Could not load futures prices.')}</section>`;
    h += `<section><h2>The model</h2><p>Trained ${M.trained_at ? dt(M.trained_at) : '–'} on ${n(M.events, 0)} past pumps and dumps (${E(String(M.data_from || '').slice(0, 10))} to ${E(String(M.data_to || '').slice(0, 10))}).</p>
      <p>Month-by-month test on unseen data: ${n(W.trades, 0)} trades, ${n(W.win_pct, 1)}% won, average ${n(W.avg_net_pct, 2)}% per trade, ${m(W.pnl_usdt)} on 1,000 USDT, ${E(W.months_positive)} months positive.
      ${Object.entries(Y).map(([y, v]) => `${y}: ${m(v.pnl_usdt)}`).join(' · ')}</p>
      <p class="note">Experiment only. The edge in testing was about 0.04% per trade, smaller than extra slippage or funding could cost, so expect results near zero. Retrain monthly with fresh data.</p></section>`;
  }
  const B = b.btc;
  if (b.mode !== 'pump' && b.mode !== 'ml') h += `<section><h2>Market filter</h2>${B ? `<p>BTC closed at ${n(B.close, 0)} ${c}; 200-day average ${n(B.sma200, 0)}. ${B.ok ? '<b class="up">Uptrend: new buys allowed.</b>' : '<b class="down">Downtrend: no new buys</b> (exits still work).'}</p>` : '<div class="empty">Could not load BTC prices.</div>'}</section>`;
  const side = t => t.is_short ? '<b class="down">short</b>' : '<b class="up">long</b>';
  const fut = b.open.some(t => t.funding);
  h += `<section><h2>Open trades</h2>${tbl(['Coin', 'Side', 'Opened', 'Qty', 'Entry price', 'Now', 'Capital', 'Value'].concat(fut ? ['Funding'] : [], ['Profit', 'Stop']),
    b.open.map(t => [E(t.pair), side(t), dt(t.open_date), n(t.amount, 6), p(t.open_rate), p(t.price), m(t.stake_amount), m(t.value)].concat(
      fut ? [`<span class="${cl(t.funding)}">${m(t.funding)}</span>`] : [],
      [`<span class="${cl(t.pnl)}">${m(t.pnl)} (${t.pnl_pct == null ? '–' : n(t.pnl_pct) + '%'})</span>`, p(t.stop_loss)])),
    'No open trades.')}${fut ? '<p class="note">Profit includes the entry fee and the funding paid or received so far. Futures prices can differ from the spot price in the normal Binance app.</p>' : ''}</section>`;
  if (b.mode !== 'pump' && b.mode !== 'ml') h += `<section><h2>Watchlist: distance to the buy and sell levels</h2><p class="note">Buys when a daily candle closes above the 55-day high (and BTC is in an uptrend). Sells when a held coin closes below the 20-day low.</p>${tbl(['Coin', 'Price', '55-day high (buy above)', 'Needs to rise', '20-day low (sell below)', 'Above it by', 'Held'],
    b.setups.map(s => { const held = b.open.some(t => t.pair === s.pair);
      return [E(s.pair), p(s.price), p(s.hi55), s.to_buy_pct == null ? '–' : (s.to_buy_pct <= 0 ? '<b class="up">at/above</b>' : '+' + n(s.to_buy_pct, 1) + '%'),
        p(s.lo20), s.above_exit_pct == null ? '–' : n(s.above_exit_pct, 1) + '%', held ? '<b>yes</b>' : '']; }),
    'Could not load prices.')}</section>`;
  h += `<section><h2>Closed trades</h2>${tbl(['Coin', 'Side', 'Opened', 'Closed', 'Qty', 'Entry', 'Exit', 'Capital', 'Profit', 'Why'],
    b.closed.map(t => [E(t.pair), side(t), dt(t.open_date), dt(t.close_date), n(t.amount, 6), p(t.open_rate), p(t.close_rate), m(t.stake_amount),
      `<span class="${cl(t.close_profit_abs)}">${m(t.close_profit_abs)} (${t.close_profit == null ? '–' : n(t.close_profit * 100) + '%'})</span>`, E(t.exit_reason)]),
    b.mode === 'pump' || b.mode === 'ml' ? 'No closed trades yet. Trades last at most a day.' : 'No closed trades yet. This strategy holds for weeks, so trades are rare.')}</section>`;
  h += `<section><h2>Recent bot log</h2><pre>${(b.log || []).map(E).join('\n') || 'No log yet.'}</pre></section>`;
  return h;
}
"""
