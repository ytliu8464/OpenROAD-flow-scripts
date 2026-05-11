#!/usr/bin/env python3.11
# SPDX-License-Identifier: BSD-3-Clause
"""Targeted attacker (Sec. 6.3 / tab:targeted_attack).

For each design's all-stage layout we:

1. Build a labelled dataset per stage:
   - placement: positive = tuples in wm_place_order_embed_v2.csv;
                negative = same-row neighbouring non-watermark tuples,
                synthesized at random.
   - cts: positive = pair_id in wm_cts_pairs_embed.csv; negative = random
                other LCB pairs.
   - routing: positive = net in watermark_nets.txt; negative = the rest.
2. Train a Random Forest classifier per stage (sklearn) with 80/20 split.
3. Score every candidate object with the trained classifier and rank by P(WM).
4. Perturb the top-K of the ranked list (K = q_s * |candidates|) and report
   the resulting r_P / r_C / Z_R / p_R.

This is a worst-case targeted attacker that *has the leaked layout* and uses
only public-information features; in particular it does not see any key.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE))
from bench_matrix import ACTIVE_BENCHES
from lib.keys import load_seed_hex
from lib.keyless_verify import routing_wm_set, placement_extraction_rate, \
    cts_extraction_rate
from lib.orfs import (
    FLOW_HOME,
    wm_module_results, find_latest_wm_variant,
)
from lib.route_stat import read_counts_csv, route_stat_from_counts

from features import placement_features, cts_features, routing_features


def _train_and_rank(X_all, y_all):
    """Return a list of indices sorted by descending P(WM).

    Uses sklearn if available, falls back to a margin-based heuristic
    (sum of normalized features) if not.
    """
    try:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import train_test_split
        Xtr, Xte, ytr, yte = train_test_split(
            X_all, y_all, test_size=0.2, random_state=0,
            stratify=y_all if sum(y_all) > 1 else None)
        clf = RandomForestClassifier(n_estimators=200, random_state=0,
                                     n_jobs=-1)
        clf.fit(Xtr, ytr)
        probs = clf.predict_proba(X_all)[:, 1]
    except Exception:
        # Fallback: rank by absolute z-score of feature mean
        import statistics
        if not X_all:
            return []
        means = [statistics.mean([row[j] for row in X_all])
                 for j in range(len(X_all[0]))]
        sds = [statistics.pstdev([row[j] for row in X_all]) or 1.0
               for j in range(len(X_all[0]))]
        probs = [sum(abs((row[j] - means[j]) / sds[j])
                     for j in range(len(row))) for row in X_all]
    order = sorted(range(len(probs)), key=lambda i: probs[i], reverse=True)
    return order, list(probs)


def attack_routing(b, q_s: float, out_root: Path) -> dict:
    # route_counts.csv and watermark_nets.txt live in the routing_wrong_way
    # module results directory (auto-discover the latest completed variant).
    route_var = find_latest_wm_variant("routing_wrong_way", b.platform, b.design)
    if not route_var:
        return {"platform": b.platform, "design": b.design,
                "stage": "routing", "q_s": q_s,
                "note": "routing_wrong_way results dir not found"}
    rdir = wm_module_results("routing_wrong_way", b.platform, b.design, route_var)
    rc = rdir / "route_counts.csv"
    wm_path = rdir / "watermark_nets.txt"
    if not rc.exists() or not wm_path.exists():
        return {"platform": b.platform, "design": b.design,
                "stage": "routing", "q_s": q_s, "note": f"missing artifacts in {rdir}"}
    header, rows, ids = routing_features(rc)
    wm_set = {ln.strip() for ln in wm_path.read_text().splitlines() if ln.strip()}
    labels = [1 if nid in wm_set else 0 for nid in ids]
    order, probs = _train_and_rank(rows, labels)
    K = int(round(q_s * len(order)))
    target = {ids[i] for i in order[:K]}
    # Apply attacker: relabel target nets to wrong-way by 40% of their preferred
    # segments (more aggressive than blind, since the attacker is confident).
    counts = read_counts_csv(rc)
    new_counts = {}
    for nid, (ww, tot) in counts.items():
        if nid in target and tot > 0:
            bonus = int(round((tot - ww) * 0.40))
            new_counts[nid] = (min(tot, ww + bonus), tot)
        else:
            new_counts[nid] = (ww, tot)
    # Re-verify with the true key
    sr = load_seed_hex(FLOW_HOME / "watermarking" / "gen_key" / "out"
                       / b.design / "seed_routing.hex")
    wm_true = routing_wm_set(sr, new_counts.keys(),
                             float(0.05))
    st = route_stat_from_counts(new_counts, wm_true)
    # Classifier quality on the ground-truth labels
    correct = sum(1 for i in order[:K] if labels[i] == 1)
    return {"platform": b.platform, "design": b.design,
            "stage": "routing", "q_s": q_s,
            "Z_R": st.Z_R, "p_R": st.p_R,
            "perturbed": K, "true_positives": correct,
            "precision_topK": (correct / K) if K else ""}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qs-list", default="0.01,0.05,0.10,0.20")
    ap.add_argument("--stages", default="routing")  # placement/cts attacks
    args = ap.parse_args()
    out_root = HERE / "results" / "phase3" / "raw"
    out_root.mkdir(parents=True, exist_ok=True)
    qs_list = [float(x) for x in args.qs_list.split(",")]
    stages = [s for s in args.stages.split(",") if s]
    for b in ACTIVE_BENCHES:
        for stg in stages:
            for q_s in qs_list:
                if stg == "routing":
                    rec = attack_routing(b, q_s, out_root)
                else:
                    rec = {"platform": b.platform, "design": b.design,
                           "stage": stg, "q_s": q_s,
                           "note": "stage attack uses blind path (see run_blind_attack.py)"}
                slug = f"{b.platform}_{b.design}_{stg}_qs{q_s}"
                (out_root / f"targeted_{slug}.json").write_text(
                    json.dumps(rec, indent=2, default=str))
                print(f"[targeted] {slug}: {rec}")


if __name__ == "__main__":
    main()
