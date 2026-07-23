#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Targeted-attack classifier diagnostics + ranking (paper §7.2).

Reads a features+labels CSV (object_id,label,<features...>) produced by
``dump_features.py`` (placement/CTS) or written by the driver (routing), trains
a RandomForest, and reports **cross-validated** distinguishability metrics:

  * AUC                  -- area under the ROC curve, out-of-fold
  * precision_at_recall  -- precision at recall >= 0.5, out-of-fold
  * a per-object ranking by out-of-fold P(WM)

Why out-of-fold:  the previous diagnostics trained a RandomForest on the whole
set and scored AUC on the *same* rows.  An unbounded-depth forest memorizes the
training set, so that AUC was ~1.0 by construction -- it measured memorization,
not distinguishability.  We instead use ``cross_val_predict`` so every object is
scored by a model that never saw it; the resulting probabilities give both an
honest AUC and a leakage-free ranking for the removal attack (the strongest
targeted attacker still cannot rank an object using a model trained on that
same object's label).

This runs under the interpreter that actually has scikit-learn (python3 / 3.6
in this environment), invoked as a subprocess by the python3.11 driver.

Outputs:
  --out-json     {"auc":..,"precision_at_recall":..,"n":..,"n_pos":..,"note":..}
  --out-ranking  CSV: object_id,proba,label  (sorted by proba desc)
"""
import argparse
import csv
import json
import sys


def _load(path):
    rows = list(csv.reader(open(path)))
    header = rows[0]
    ids, X, y = [], [], []
    for r in rows[1:]:
        if len(r) < 2:
            continue
        ids.append(r[0])
        y.append(int(float(r[1])))
        X.append([float(v) for v in r[2:]])
    return header[2:], ids, X, y


def _margin_rank(X):
    """Z-score-magnitude sum; fallback ordering when a classifier can't train."""
    import statistics
    if not X or not X[0]:
        return [0.0] * len(X)
    cols = list(zip(*X))
    means = [statistics.mean(c) for c in cols]
    sds = [statistics.pstdev(c) or 1.0 for c in cols]
    return [sum(abs((row[j] - means[j]) / sds[j]) for j in range(len(row)))
            for row in X]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", required=True)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-ranking", required=True)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--trees", type=int, default=400)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    header, ids, X, y = _load(args.features)
    n = len(y)
    n_pos = sum(1 for v in y if v == 1)
    n_neg = n - n_pos
    rec = {"auc": None, "precision_at_recall": None,
           "n": n, "n_pos": n_pos, "n_neg": n_neg, "note": ""}
    proba = [0.0] * n

    if n_pos < 2 or n_neg < 2:
        rec["note"] = "single/degenerate class; margin-ranked, AUC unavailable"
        proba = _margin_rank(X)
    else:
        import numpy as np
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import StratifiedKFold, cross_val_predict
        from sklearn.metrics import roc_auc_score, precision_recall_curve

        Xa = np.asarray(X, dtype=float)
        ya = np.asarray(y, dtype=int)
        folds = max(2, min(args.folds, n_pos, n_neg))
        clf = RandomForestClassifier(
            n_estimators=args.trees, random_state=args.seed,
            n_jobs=-1, class_weight="balanced")
        skf = StratifiedKFold(n_splits=folds, shuffle=True,
                              random_state=args.seed)
        try:
            proba = cross_val_predict(
                clf, Xa, ya, cv=skf, method="predict_proba",
                n_jobs=-1)[:, 1].tolist()
            rec["auc"] = float(roc_auc_score(ya, proba))
            p, r, _ = precision_recall_curve(ya, proba)
            idxs = [i for i, rv in enumerate(r) if rv >= 0.5]
            if idxs:
                rec["precision_at_recall"] = float(p[max(idxs)])
            rec["folds"] = folds
        except Exception as e:
            rec["note"] = "cv failed: %s; margin-ranked" % e
            proba = _margin_rank(X)

    # Break ties RANDOMLY, not by input order.  When the classifier cannot
    # distinguish objects (e.g. a RandomForest assigns proba 0 to most marked
    # CTS pairs, tied with thousands of negatives) a stable sort would fall back
    # to the feature-CSV order -- which is the embedder's pool-traversal order,
    # and the embedder commits the *earliest* feasible objects, so marked
    # objects would sort to the top of every tie group.  That leaks the
    # embedder's traversal order (a side channel the keyless attacker does not
    # have) and makes the ranked attack look far stronger than the AUC (which is
    # tie-aware) justifies.  A seeded random tiebreak models an attacker who, on
    # indistinguishable objects, can do no better than chance.
    import random as _random
    _rng = _random.Random(args.seed)
    jitter = [_rng.random() for _ in range(n)]
    order = sorted(range(n), key=lambda i: (proba[i], jitter[i]), reverse=True)
    with open(args.out_ranking, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["object_id", "proba", "label"])
        for i in order:
            wr.writerow([ids[i], "%.6g" % proba[i], y[i]])
    with open(args.out_json, "w") as f:
        json.dump(rec, f, indent=2)
    sys.stderr.write(
        "[classify] n=%d pos=%d auc=%s prec@rec=%s %s\n"
        % (n, n_pos, rec["auc"], rec["precision_at_recall"], rec["note"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
