#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Unsupervised "targeted" classifier diagnostic.

Mirrors classify.py but trains an Isolation Forest **without** label access.
Labels are read from the input CSV only for evaluation (AUC / precision / recall
against ground truth) -- they are never passed to the fit() call.  This models a
realistic attacker who has the suspect layout, the public eligibility rules,
and the resulting feature vector, but no embed-CSV labels and no secret key.

Inputs:
  --features  feat_<...>_filtered.csv   (object_id, label, <features...>)
  --out-json  diag_<...>_unsup.json     written as classify.py's diag schema
  --out-ranking rank_<...>_unsup.csv    object_id, anomaly_score, label

Output schema (one line per design):
  auc                    AUC of anomaly score vs ground-truth label (label==1 = mark).
                         Random ranking gives 0.5.  Marked-pairs-as-bulk gives < 0.5.
  precision_at_recall    precision when recall >= 0.5 (same definition as classify.py).
  precision_at_K         precision when we take top-K most anomalous,
                         where K = |WM|.  Random baseline = |WM|/|E_C|.
  recall_at_K            recall when we take top-K most anomalous.
  n / n_pos / n_neg      population sizes.
  random_baseline_p      |WM|/|E_C|.

Algorithm: IsolationForest(n_estimators=400, contamination=K/|E_C|, seed=0).
Anomaly score = -clf.score_samples(X).  Higher score = more anomalous.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


def _load(path):
    rows = list(csv.reader(open(path)))
    header = rows[0]
    ids = [r[0] for r in rows[1:]]
    y = [int(r[1]) for r in rows[1:]]
    X = [[float(v) for v in r[2:]] for r in rows[1:]]
    return header, ids, X, y


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", required=True)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-ranking", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--n-estimators", type=int, default=400)
    args = ap.parse_args()

    header, ids, X, y = _load(args.features)
    n = len(y)
    n_pos = sum(1 for v in y if v == 1)
    n_neg = n - n_pos

    # q_s grid matches the blind / supervised attack tables in the paper.
    QS_GRID = (0.10, 0.20, 0.50, 0.80, 1.00)

    rec = {
        "auc": None,
        "precision_at_recall": None,
        "precision_at_K": None,
        "recall_at_K": None,
        "recall_at_qs": {f"{q:.2f}": None for q in QS_GRID},  # unsupervised attack
        "recall_at_qs_blind": {f"{q:.2f}": q for q in QS_GRID},  # = q_s for blind
        "n": n,
        "n_pos": n_pos,
        "n_neg": n_neg,
        "random_baseline_p": (n_pos / n) if n else 0.0,
        "note": "",
    }

    if n_pos < 1 or n < 8:
        rec["note"] = "degenerate (no positives or too few rows)"
    else:
        import numpy as np
        from sklearn.ensemble import IsolationForest
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import roc_auc_score, precision_recall_curve

        Xa = np.asarray(X, dtype=float)
        ya = np.asarray(y, dtype=int)

        # Standardize so features with bigger ranges (e.g., dist_dbu) do not
        # dominate the Isolation Forest's random splits.  Without scaling
        # IsolationForest is geometric in the raw feature space, which would
        # privilege whichever column happens to have the largest dynamic range.
        Xs = StandardScaler().fit_transform(Xa)

        contamination = max(1e-4, min(0.5, n_pos / max(n, 1)))
        clf = IsolationForest(
            n_estimators=args.n_estimators,
            contamination=contamination,
            random_state=args.seed,
            n_jobs=-1,
        )
        # IMPORTANT: ya is never passed to fit().  fit() sees only Xs.
        clf.fit(Xs)
        # score_samples is the "outlier score" -- higher = more *normal* in
        # IsolationForest's sign convention.  Flip so larger = more anomalous,
        # matching classify.py's "predict_proba[:, 1]" semantics (higher =
        # more likely to be a watermark target in attacker's hypothesis).
        anomaly = -clf.score_samples(Xs)

        try:
            rec["auc"] = float(roc_auc_score(ya, anomaly))
        except Exception:
            rec["auc"] = None
        try:
            p, r, _ = precision_recall_curve(ya, anomaly)
            idxs = [i for i, rv in enumerate(r) if rv >= 0.5]
            if idxs:
                rec["precision_at_recall"] = float(p[max(idxs)])
        except Exception:
            pass

        # Top-K precision / recall.  K = |WM|, the attack budget that recovers
        # all marks under a perfect classifier.  Used to compare directly with
        # the random baseline |WM|/|E_C|.
        K = n_pos
        order = np.argsort(-anomaly)
        top_K_idx = order[:K]
        hits = int(ya[top_K_idx].sum())
        rec["precision_at_K"] = hits / K
        rec["recall_at_K"] = hits / n_pos

        # Attack-budget recall: at the blind/targeted-attack budgets q_s in
        # {0.10, 0.20, 0.50, 0.80, 1.00}, what fraction of the watermark do
        # we recover by perturbing the top q_s * |E_C| anomalous pairs?
        # Blind attack baseline is recall = q_s (uniform random subset of E_C
        # hits q_s of the marks in expectation).  Any lift over q_s is what
        # the unsupervised feature ranking buys the attacker.
        for q in QS_GRID:
            k_atk = max(1, int(round(q * n)))
            top_atk = order[:k_atk]
            hits_q = int(ya[top_atk].sum())
            rec["recall_at_qs"][f"{q:.2f}"] = hits_q / n_pos

    # Ranking CSV: same shape as classify.py for downstream tools.
    import random as _random
    rng = _random.Random(args.seed)
    if rec["auc"] is None:
        scores = [0.0] * n
    else:
        scores = list(anomaly)
    jitter = [rng.random() for _ in range(n)]
    order_full = sorted(range(n), key=lambda i: (scores[i], jitter[i]), reverse=True)
    Path(args.out_ranking).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_ranking, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["object_id", "anomaly_score", "label"])
        for i in order_full:
            wr.writerow([ids[i], f"{scores[i]:.6g}", y[i]])

    Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_json, "w") as f:
        json.dump(rec, f, indent=2)

    auc_s = f"{rec['auc']:.4f}" if rec['auc'] is not None else "n/a"
    p_at_K = f"{rec['precision_at_K']:.4f}" if rec['precision_at_K'] is not None else "n/a"
    rand_p = f"{rec['random_baseline_p']:.4f}"
    sys.stderr.write(
        f"[classify_unsup] n={n} pos={n_pos} AUC={auc_s} prec@K={p_at_K} "
        f"(random_p={rand_p}) {rec['note']}\n")
    return 0


sys.exit(main())
