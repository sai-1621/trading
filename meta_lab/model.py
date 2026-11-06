"""Purged walk-forward meta-labeler (LightGBM).

For every quarter Q in the test range:
  train = all trades whose EXIT happened before Q's start minus the embargo (purge: no label overlaps the test quarter)
  threshold = picked on the last 25% of that training window by an inner model trained on the first 75% (also purged),
              maximising the total net return of the kept trades (grid config.THRESHOLDS)
  model = refit on the whole training window, scored on Q's trades, trades kept if p >= threshold
No K-fold, no shuffling: every score is from a model that never saw the test quarter or anything after it."""
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.metrics import roc_auc_score

import config as C


LGB_OVERRIDE: dict = {}                     # per-iteration settings (run.py fills it from the iteration config)


def _fit(X, y):
    m = LGBMClassifier(**{**C.LGB, **LGB_OVERRIDE})
    m.fit(X, y)
    return m


def _pick_threshold(tr: pd.DataFrame, feats, ycol, mode="max_total"):
    tr = tr.sort_values("t_in")
    cut = tr["t_in"].iloc[int(len(tr) * 0.75)]
    inner = tr[tr["t_out"] < cut - C.EMBARGO]
    val = tr[tr["t_in"] >= cut]
    if len(inner) < C.MIN_TRAIN // 2 or len(val) < C.MIN_TRAIN // 5:
        return 0.5, None
    m = _fit(inner[feats], inner[ycol])
    p = m.predict_proba(val[feats])[:, 1]
    if mode.startswith("top"):                 # "top30": keep the best 30% (quantile of out-of-sample val scores)
        return float(np.quantile(p, 1 - int(mode[3:]) / 100)), None
    best, best_th = -np.inf, C.THRESHOLDS[0]
    for th in C.THRESHOLDS:
        k = p >= th
        if k.sum() < 30:
            continue
        tot = val["net"].values[k].sum()
        if tot > best:
            best, best_th = tot, th
    return best_th, best


def walk_forward(trades: pd.DataFrame, feats, start, end, ycol="y", th_mode="max_total"):
    """Returns the test trades with columns p (probability), th (threshold), keep (bool), fold."""
    folds = pd.date_range(start, end, freq=C.RETRAIN, tz="UTC")
    if folds[0] > start:
        folds = folds.insert(0, start)
    if folds[-1] < end:
        folds = folds.append(pd.DatetimeIndex([end]))
    outs, log = [], []
    for a, b in zip(folds[:-1], folds[1:]):
        test = trades[(trades["t_sig"] >= a) & (trades["t_sig"] < b)].copy()
        train = trades[trades["t_out"] < a - C.EMBARGO]
        if test.empty or len(train) < C.MIN_TRAIN:
            continue
        th, _ = _pick_threshold(train, feats, ycol, th_mode)
        m = _fit(train[feats], train[ycol])
        test["p"] = m.predict_proba(test[feats])[:, 1]
        test["th"] = th
        test["keep"] = test["p"] >= th
        test["fold"] = a.strftime("%Y-%m")
        try:
            auc = roc_auc_score(test[ycol], test["p"])
        except ValueError:
            auc = np.nan
        log.append(dict(fold=a.strftime("%Y-%m"), n_train=len(train), base_rate=round(train[ycol].mean(), 3),
                        n_test=len(test), th=round(th, 3), kept=int(test["keep"].sum()), auc=round(auc, 3),
                        mean_net_all=round(test["net"].mean() * 100, 3),
                        mean_net_kept=round(test.loc[test["keep"], "net"].mean() * 100, 3) if test["keep"].any() else np.nan))
        imp = pd.Series(m.feature_importances_, index=feats)
        outs.append((test, imp))
    res = pd.concat([o[0] for o in outs]) if outs else pd.DataFrame()
    imp = pd.concat([o[1] for o in outs], axis=1).mean(axis=1).sort_values(ascending=False) if outs else None
    return res, pd.DataFrame(log), imp
