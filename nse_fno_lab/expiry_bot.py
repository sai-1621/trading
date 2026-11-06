r"""Expiry-day paper bot (strategy 2). Runs all the time; places NO orders.

Every trading morning it checks whether today is a weekly expiry:
  NIFTY  (Tuesday; holiday-shifted)  -> runs scripts/log_expiry_chain.py --symbol NIFTY --loop
  SENSEX (Thursday; holiday-shifted) -> same for SENSEX, only if Dhan / Angel / Kite keys are in .env
The tracker locks real-price paper iron flies at 09:45 and 10:15 and follows them to 15:15 (see paper_fly.py).
If the tracker crashes it is restarted (its state file makes it resume). Results: logs/paper_fly_results.csv.
Status for the dashboard: logs/expiry_bot_status.json. Log: logs/expiry_bot.log.

    .venv\Scripts\python expiry_bot.py            (normally started hidden by run_expiry_bot.ps1)
    .venv\Scripts\python expiry_bot.py --status
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import config as C
from nse_calendar import IST, expiries, is_expiry_day, is_trading_day

LOGS = C.BASE_DIR / "logs"
LOGS.mkdir(exist_ok=True)
STATUS = LOGS / "expiry_bot_status.json"
LOG = LOGS / "expiry_bot.log"
PID = C.BASE_DIR / "expiry_bot.pid"
PY = C.BASE_DIR / ".venv" / "Scripts" / "python.exe"
SCRIPT = C.BASE_DIR / "scripts" / "log_expiry_chain.py"
START, END = (9, 30), (15, 30)          # wake at 09:30; the tracker itself starts marking at 09:40


def log(msg: str):
    line = f"{datetime.now(IST):%Y-%m-%d %H:%M:%S} {msg}"
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def keys_for_bse() -> bool:
    return bool((os.getenv("DHAN_CLIENT_ID") and os.getenv("DHAN_ACCESS_TOKEN"))
                or (os.getenv("KITE_API_KEY") and os.getenv("KITE_ACCESS_TOKEN"))
                or (C.ANGEL_API_KEY and C.ANGEL_CLIENT_CODE and C.ANGEL_PIN and C.ANGEL_TOTP_SECRET))


def symbols_today(d) -> list[str]:
    out = []
    if is_expiry_day("NIFTY", d):
        out.append("NIFTY")
    if is_expiry_day("SENSEX", d):
        out.append("SENSEX" if keys_for_bse() else "SENSEX (skipped: needs Dhan/Angel/Kite keys)")
    return out


def next_expiries(d) -> dict:
    return {s: expiries(s, d, 1)[0].isoformat() for s in ("NIFTY", "SENSEX")}


def write_status(state: str, **kw):
    st = {"pid": os.getpid(), "state": state, "updated": datetime.now(IST).isoformat(timespec="seconds"),
          "next_expiry": next_expiries(datetime.now(IST).date()), "bse_keys": keys_for_bse(), **kw}
    STATUS.write_text(json.dumps(st, indent=1))


def already_running() -> bool:
    if not PID.exists():
        return False
    try:
        pid = int(PID.read_text().split()[0])
    except (ValueError, IndexError):
        return False
    if pid == os.getpid():
        return False
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
    k32.CloseHandle(h)
    return bool(ok) and code.value == 259


def run_tracker(sym: str) -> int:
    """Runs the full-day tracker; restarts it after a crash until 15:25. Returns the last exit code."""
    code = 0
    while True:
        now = datetime.now(IST)
        if now.hour * 60 + now.minute > 15 * 60 + 25:
            return code
        write_status("tracking", symbol=sym, since=now.isoformat(timespec="seconds"))
        log(f"{sym}: expiry day, starting the paper tracker")
        with LOG.open("a", encoding="utf-8") as out:
            p = subprocess.run([str(PY), "-u", str(SCRIPT), "--symbol", sym, "--loop"], cwd=str(C.BASE_DIR),
                               stdout=out, stderr=subprocess.STDOUT)
        code = p.returncode
        state_f = LOGS / f"paper_fly_{sym}_{now:%Y%m%d}.json"
        done = state_f.exists() and json.loads(state_f.read_text()).get("done")
        if code == 0 and (done or datetime.now(IST).hour * 60 + datetime.now(IST).minute >= 15 * 60 + 15):
            log(f"{sym}: tracker finished")
            return 0
        log(f"{sym}: tracker exited with code {code}; restarting in 30 s (state resumes)")
        time.sleep(30)


def sleep_until(t: datetime, why: str):
    write_status("sleeping", wake_at=t.isoformat(timespec="minutes"), reason=why)
    while datetime.now(IST) < t:
        time.sleep(min(300, max(5, (t - datetime.now(IST)).total_seconds())))
        write_status("sleeping", wake_at=t.isoformat(timespec="minutes"), reason=why)


def main():
    if "--status" in sys.argv:
        print(STATUS.read_text() if STATUS.exists() else "no status yet")
        return
    if already_running():
        print("expiry bot already running")
        sys.exit(3)
    PID.write_text(f"{os.getpid()} since {datetime.now(IST):%Y-%m-%d %H:%M}")
    log(f"expiry bot started (pid {os.getpid()}); next expiries {next_expiries(datetime.now(IST).date())}; "
        f"BSE keys {'yes' if keys_for_bse() else 'no'}")
    done_today = set()
    while True:
        now = datetime.now(IST)
        d = now.date()
        m = now.hour * 60 + now.minute
        todo = [s for s in symbols_today(d) if is_trading_day(d) and s not in done_today]
        runnable = [s for s in todo if "skipped" not in s]
        for s in todo:
            if "skipped" in s and s not in done_today:
                log(s)
                done_today.add(s)
        if runnable and START[0] * 60 + START[1] <= m <= 15 * 60 + 25:
            for s in runnable:
                run_tracker(s)
                done_today.add(s)
            continue
        # sleep until the next 09:30 on a trading day
        nxt = datetime(d.year, d.month, d.day, *START, tzinfo=IST)
        if now >= nxt:
            nxt += timedelta(days=1)
            done_today = set()
        while not is_trading_day(nxt.date()):
            nxt += timedelta(days=1)
        exp = symbols_today(nxt.date())
        sleep_until(nxt, f"next check {nxt:%a %d %b %H:%M}" + (f" (expiry: {', '.join(exp)})" if exp else " (no expiry that day)"))
        if nxt.date() != d:
            done_today = set()


if __name__ == "__main__":
    main()
