#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Routing rows of the targeted-attack classifier diagnostics (tab:targeted_auc)
under the revised canonical wrong-way wirelength features.

For each NG45 design we build the eligible routed-net set E_R (tot_len>0) from
the canonical q_R CSV (tools/dump_route_qr.py), label nets in watermark_nets.txt
positive and the rest negative, and train a 5-fold cross-validated
RandomForest.  Features are the observable post-route quantities the paper
lists: net degree, bounding-box dimensions, routed (total planar) wirelength,
via count, canonical wrong-way wirelength, canonical per-net wrong-way fraction
q_R(n), and the per-layer wirelength distribution.

Reports cross-validated AUC, precision@K, and recall@K with K=|WM_R| (so
precision@K == recall@K, matching the placement/CTS rows).

Requires scikit-learn -> run inside the singularity image:
    singularity exec -B /home <sif> python3 route_v2/targeted_routing.py
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import roc_auc_score

EXP = Path(__file__).resolve().parents[1]
QR = EXP / "results" / "phase_route_v2"

DESIGNS = [
    ("JPEG",   "jpeg"),
    ("SweRV",  "swerv_wrapper"),
    ("Ariane", "ariane136"),
    ("BP",     "bp_multi"),
]


def load_features(csv_path: Path, wm_txt: Path):
    wm = set()
    if wm_txt.exists():
        wm = {l.strip() for l in wm_txt.read_text().splitlines() if l.strip()}
    names, X, y = [], [], []
    with open(csv_path) as f:
        rd = csv.DictReader(f)
        layer_cols = [c for c in rd.fieldnames if c.startswith("L_")]
        for row in rd:
            tot = float(row["tot_len"])
            if tot <= 0:
                continue
            ww = float(row["ww_len"])
            feat = [
                float(row["degree"]),
                float(row["bbox_w"]),
                float(row["bbox_h"]),
                tot,                       # routed (total planar) wirelength
                float(row["vias"]),
                ww,                        # canonical wrong-way wirelength
                ww / tot,                  # q_R(n)
            ]
            feat += [float(row[c]) for c in layer_cols]   # layer-usage dist
            names.append(row["net"])
            X.append(feat)
            y.append(1 if row["net"] in wm else 0)
    return names, np.asarray(X, float), np.asarray(y, int)


def cv_diagnostics(X, y, seed=0):
    """5-fold CV: out-of-fold P(WM); return AUC and precision/recall@K."""
    n_pos = int(y.sum())
    if n_pos < 5 or n_pos == len(y):
        return None, None, None, n_pos
    oof = np.zeros(len(y), float)
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
    for tr, te in skf.split(X, y):
        clf = RandomForestClassifier(n_estimators=400, class_weight="balanced",
                                     n_jobs=-1, random_state=seed)
        clf.fit(X[tr], y[tr])
        oof[te] = clf.predict_proba(X[te])[:, 1]
    auc = roc_auc_score(y, oof)
    K = n_pos
    top = np.argsort(-oof)[:K]
    tp = int(y[top].sum())
    prec = tp / K
    rec = tp / n_pos
    return auc, prec, rec, n_pos


def main():
    rows = []
    for label, nick in DESIGNS:
        # prefer the r-only routed layout (pure routing carrier); fall back to
        # all-stage.  Both have watermark_nets.txt at the same fraction.
        for var in ("pdmarks-r-only", "pdmarks-all-stage"):
            csv_path = QR / (f"ronly_{nick}.csv" if var == "pdmarks-r-only"
                             else f"allstage_{nick}.csv")
            wm_txt = EXP / "results" / "nangate45" / nick / var / "watermark_nets.txt"
            if csv_path.exists() and wm_txt.exists():
                break
        names, X, y = load_features(csv_path, wm_txt)
        auc, prec, rec, n_pos = cv_diagnostics(X, y)
        rand = n_pos / len(y) if len(y) else 0.0
        rows.append({"design": label, "nick": nick, "variant": var,
                     "N": len(y), "K": n_pos, "random": rand,
                     "AUC": auc, "prec@K": prec, "rec@K": rec})
        print(f"{label:8} {var:18} N={len(y):>7} K={n_pos:>5} "
              f"random={rand:.3f} AUC={auc:.3f} prec@K={prec:.3f} rec@K={rec:.3f}",
              flush=True)

    aucs = [r["AUC"] for r in rows if r["AUC"] is not None]
    precs = [r["prec@K"] for r in rows if r["prec@K"] is not None]
    recs = [r["rec@K"] for r in rows if r["rec@K"] is not None]
    mean = {"AUC": float(np.mean(aucs)) if aucs else None,
            "prec@K": float(np.mean(precs)) if precs else None,
            "rec@K": float(np.mean(recs)) if recs else None}
    print(f"{'mean':8} {'':18} {'':>7} {'':>5}          "
          f"AUC={mean['AUC']:.3f} prec@K={mean['prec@K']:.3f} "
          f"rec@K={mean['rec@K']:.3f}", flush=True)
    (QR / "route_v2_targeted_auc.json").write_text(
        json.dumps({"rows": rows, "mean": mean}, indent=2))


if __name__ == "__main__":
    main()
