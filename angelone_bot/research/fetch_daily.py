"""Download ~2000 days of Angel One daily bars for the swing research into cache/angel_d1_<SYM>_2000.json.
Read-only market data, no orders. Run after market hours (the live bot shares the rate limit)."""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import angel_client  # noqa: E402
import config  # noqa: E402
from angel_client import AngelClient  # noqa: E402


def symbols():
    syms = set(json.loads((config.CACHE_DIR / "NIFTY100.json").read_text()))
    syms |= {f.name[len("angel_bt_"):-len("_730.json")] for f in config.CACHE_DIR.glob("angel_bt_*_730.json")}
    return sorted(syms) + ["^NSEI"]


def main():
    angel_client.HIST_GAP = 1.2
    c = AngelClient()
    todo = symbols()
    fails = []
    for i, s in enumerate(todo, 1):
        f = config.CACHE_DIR / f"angel_d1_{s.replace('^', '_')}_2000.json"
        if f.exists() and time.time() - f.stat().st_mtime < 7 * 86400:
            continue
        try:
            bars = c.candles(s, "ONE_DAY", 1990)
            f.write_text(json.dumps(bars, default=lambda o: o.isoformat()))
            print(f"{i}/{len(todo)} {s} {len(bars)} bars", flush=True)
        except Exception as e:  # noqa: BLE001
            fails.append(s)
            print(f"{i}/{len(todo)} {s} FAILED {e}", flush=True)
    print("failed:", fails)


if __name__ == "__main__":
    main()
