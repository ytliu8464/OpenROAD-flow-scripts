#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Aggregate the baseline attack-robustness sweep into CSVs + paper-ready rows.

Reads results/phase3/baseline_attacks/<method>_<plat>_<design>_<attack>_qs<q>.json
and emits:
  results/phase3/baseline_attacks.csv          -- long form (one row per record)
  results/phase3/baseline_attack_auc.csv        -- per (method,design) AUC/precision
and prints LaTeX-ready rows for:
  (A) distinguishability: method x design -> AUC
  (B) post-attack extraction r vs q_s (blind / targeted), per method per design
"""
import csv
import glob
import json
import os
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
RAW = HERE / "results" / "phase3" / "baseline_attacks"

METHODS = ["kahng", "cell_scattering", "buffer_insertion", "icmarks", "automarks"]
METHOD_LABEL = {"kahng": "Kahng", "cell_scattering": "Cell-scattering",
                "buffer_insertion": "Buffer-insertion", "icmarks": "ICMarks",
                "automarks": "AutoMarks"}
ORDER = [("nangate45", "jpeg", "JPEG (NG45)"), ("nangate45", "swerv_wrapper", "SweRV (NG45)"),
         ("nangate45", "ariane136", "Ariane (NG45)"), ("nangate45", "bp_multi_top", "BP (NG45)"),
         ("asap7", "jpeg", "JPEG (ASAP7)"), ("asap7", "swerv_wrapper", "SweRV (ASAP7)"),
         ("asap7", "ariane", "Ariane (ASAP7)"), ("asap7", "cva6", "CVA6 (ASAP7)")]
QS = ["0.1", "0.2", "0.5", "0.8", "1.0"]


def _fmt(v, p=3):
    try:
        return f"{float(v):.{p}f}"
    except (TypeError, ValueError):
        return "--"


def main():
    recs = []
    for f in glob.glob(str(RAW / "*.json")):
        try:
            recs.append(json.loads(Path(f).read_text()))
        except Exception:
            pass
    if not recs:
        print("no records found in", RAW)
        return

    # long-form CSV
    fields = ["method", "platform", "design", "attack", "q_s", "r", "Pc",
              "auc", "precision_at_recall", "n_eligible", "n_pos", "recall_top_K"]
    out = HERE / "results" / "phase3" / "baseline_attacks.csv"
    with open(out, "w", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=fields)
        w.writeheader()
        for r in recs:
            w.writerow({k: r.get(k, "") for k in fields})
    print(f"[agg] wrote {out} ({len(recs)} records)")

    by = {(r["method"], r["platform"], r["design"], r["attack"], str(r["q_s"])): r
          for r in recs}
    auc = {(r["method"], r["platform"], r["design"]): r.get("auc") for r in recs}

    # AUC CSV
    aout = HERE / "results" / "phase3" / "baseline_attack_auc.csv"
    with open(aout, "w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["method", "platform", "design", "auc"])
        for (m, p, d), a in sorted(auc.items()):
            w.writerow([m, p, d, a])
    print(f"[agg] wrote {aout}")

    print("\n===== (A) Targeted distinguishability AUC (method x design) =====")
    print("Design".ljust(16) + "".join(METHOD_LABEL[m][:11].ljust(13) for m in METHODS))
    for p, d, lab in ORDER:
        row = lab.ljust(16)
        for m in METHODS:
            row += _fmt(auc.get((m, p, d)), 2).ljust(13)
        print(row)

    print("\n===== (B) Post-attack extraction r vs q_s =====")
    for m in METHODS:
        print(f"\n-- {METHOD_LABEL[m]} --   (blind / targeted), r per q in {QS}")
        for p, d, lab in ORDER:
            b = [_fmt(by.get((m, p, d, "blind", q), {}).get("r")) for q in QS]
            t = [_fmt(by.get((m, p, d, "targeted", q), {}).get("r")) for q in QS]
            print(f"  {lab:15s} blind:    " + " ".join(b))
            print(f"  {'':15s} targeted: " + " ".join(t))

    # mean AUC + mean targeted r@0.1 per method (quick comparison vs PDMarks)
    print("\n===== (C) Per-method summary (mean over designs) =====")
    print(f"{'method':16s} {'meanAUC':8s} {'tgt_r@0.1':10s} {'tgt_r@0.5':10s} {'blind_r@0.5':11s}")
    for m in METHODS:
        aucs = [v for (mm, p, d), v in auc.items() if mm == m and isinstance(v, (int, float))]
        def mean_r(attack, q):
            vs = [by.get((m, p, d, attack, q), {}).get("r") for p, d, _ in ORDER]
            vs = [v for v in vs if isinstance(v, (int, float))]
            return sum(vs) / len(vs) if vs else None
        print(f"{m:16s} {_fmt(sum(aucs)/len(aucs) if aucs else None,2):8s} "
              f"{_fmt(mean_r('targeted','0.1')):10s} {_fmt(mean_r('targeted','0.5')):10s} "
              f"{_fmt(mean_r('blind','0.5')):11s}")


if __name__ == "__main__":
    main()
