"""Monthly retrain of the PumpML model (option 1, chosen by the user on 7 Nov 2026). Run by hand, about the 1st of each
month, when the user asks; no scheduled task. It does NOT restart the bot: the bot loads the model only at start-up.

    .venv\\Scripts\\python user_data\\retrain_model.py            refresh data, back up, retrain, check
    .venv\\Scripts\\python user_data\\retrain_model.py --no-data  skip the data refresh

Steps:
 1. refresh 1-hour Binance futures candles (freqtrade download-data, which throttles its own requests);
 2. copy models/pumpml.joblib + .json to models/archive/pumpml-<date>.* (to roll back, copy them back);
 3. run ml_pumpdump.py --save: a fresh walk-forward report (ml_results.json) and a model trained on all events;
 4. check the new model loads, uses the same features and exits, and saw more events than the old one.
    If the check fails, the old model is restored.
Same model settings, features, exits and 0.5% bar every month: only the data grows. No tuning.
"""
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib

UD = Path(__file__).resolve().parent
ROOT = UD.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"
FT = ROOT / ".venv" / "Scripts" / "freqtrade.exe"
MODEL = UD / "models" / "pumpml.joblib"
INFO = MODEL.with_suffix(".json")
ARCH = UD / "models" / "archive"


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    old = joblib.load(MODEL)
    print(f"current model: trained {old['trained_at'][:16]}, {old['events']} events up to {old['data_to'][:16]}")

    if "--no-data" not in sys.argv:
        print("1/4 refreshing 1h candles ...", flush=True)
        r = subprocess.run([str(FT), "download-data", "-c", "user_data/config-pump.json", "--timeframes", "1h",
                            "--userdir", "user_data", "--trading-mode", "futures"], cwd=ROOT)
        if r.returncode != 0:
            sys.exit("data refresh failed; model unchanged")

    print("2/4 backing up the current model ...", flush=True)
    ARCH.mkdir(parents=True, exist_ok=True)
    bak = ARCH / f"pumpml-{stamp}.joblib"
    shutil.copy2(MODEL, bak)
    shutil.copy2(INFO, bak.with_suffix(".json"))

    print("3/4 retraining (walk-forward report + final model) ...", flush=True)
    r = subprocess.run([str(PY), "user_data/ml_pumpdump.py", "--save"], cwd=ROOT)

    print("4/4 checking the new model ...", flush=True)
    problems = []
    try:
        new = joblib.load(MODEL)
        if r.returncode != 0:
            problems.append("training script failed")
        if new["features"] != old["features"]:
            problems.append("features changed")
        if new["exits"] != old["exits"] or new["min_pred"] != old["min_pred"]:
            problems.append("exits or bar changed")
        if new["events"] <= old["events"]:
            problems.append(f"no new events ({new['events']} vs {old['events']})")
        X = [[0.0] * len(new["features"])]
        new["long"].predict(X), new["short"].predict(X)
    except Exception as e:  # noqa: BLE001
        problems.append(f"cannot load: {e}")
    if problems:
        shutil.copy2(bak, MODEL)
        shutil.copy2(bak.with_suffix(".json"), INFO)
        sys.exit("NEW MODEL REJECTED, old one restored: " + "; ".join(problems))
    wf = json.loads(INFO.read_text()).get("walk_forward", {})
    print(f"OK: new model trained on {new['events']} events up to {new['data_to'][:16]} "
          f"(was {old['events']} up to {old['data_to'][:16]}). Walk-forward: {wf.get('trades')} trades, "
          f"avg {wf.get('avg_net_pct')}%, P&L {wf.get('pnl_usdt')} USDT. Backup: {bak.name}")
    print("Restart the ML bot to load it (it reads the model only at start-up).")


if __name__ == "__main__":
    main()
