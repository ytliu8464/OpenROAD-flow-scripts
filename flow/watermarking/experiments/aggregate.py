# SPDX-License-Identifier: BSD-3-Clause
"""Aggregate per-bench results into the per-phase summary CSVs.

Currently implements:
  - phase1 summary_ref.csv (from on-disk reference runs)
  - phase1 capacity.csv (reads experiments/results/phase1/raw/capacity_*.json)
  - phase1 ppa_<platform>.csv (reads experiments/results/phase1/raw/ppa_*.json)
  - phase1 survival.csv (reads experiments/results/phase1/raw/survival_*.json)

Each raw artifact follows a small JSON schema documented inline so that
individual driver scripts can drop files into raw/ without touching this file.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Dict, List

from bench_matrix import ACTIVE_BENCHES
from lib.orfs import load_reference


HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"


def aggregate_ref() -> Path:
    out = RESULTS / "phase1"
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / "summary_ref.csv"
    fields = ["platform", "design", "variant", "paper_label",
              "n_std_cells", "n_macros", "n_nets", "tcp_ns",
              "wns_ns", "tns_ns", "rwl_um", "power_w", "runtime_s"]
    with open(csv_path, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=fields)
        wr.writeheader()
        for b in ACTIVE_BENCHES:
            # ORFS writes results/logs under DESIGN_NICKNAME, so resolve paths
            # with b.design_nickname; the CSV's "design" column keeps the
            # logical DESIGN_NAME so render_tex etc. match on b.design.
            ref = load_reference(b.platform, b.design_nickname, b.wm_flow_variant)
            row = ref.to_dict()
            row["design"] = b.design
            row["paper_label"] = b.paper_label
            wr.writerow({k: row.get(k, "") for k in fields})
    return csv_path


def _collect_json(prefix: str, phase: str = "phase1") -> List[dict]:  # noqa: E501
    raw = RESULTS / phase / "raw"
    if not raw.exists():
        return []
    out = []
    for p in sorted(raw.glob(f"{prefix}_*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except Exception:
            continue
    return out


def _as_float(v):
    if v in ("", None):
        return None
    try:
        out = float(v)
    except Exception:
        return None
    return out


def _mean(vals: List[float]):
    return (sum(vals) / len(vals)) if vals else ""


def _max(vals: List[float]):
    return max(vals) if vals else ""


def aggregate_capacity() -> Path:
    rows = _collect_json("capacity")
    out_csv = RESULTS / "phase1" / "capacity.csv"
    fields = ["stage", "platform", "design", "variant",
              "eligible", "selected", "detail_a", "detail_b"]
    with open(out_csv, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=fields)
        wr.writeheader()
        for r in rows:
            wr.writerow({k: r.get(k, "") for k in fields})
    return out_csv


def aggregate_ppa() -> List[Path]:
    rows = _collect_json("ppa")
    outs = []
    for plat in ("nangate45", "asap7"):
        out_csv = RESULTS / "phase1" / f"ppa_{plat}.csv"
        fields = ["platform", "design", "variant", "method",
                  "dWNS", "dTNS", "dRWL", "dPower", "dRuntime",
                  "wm_runtime_s", "Pc"]
        with open(out_csv, "w", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=fields)
            wr.writeheader()
            for r in rows:
                if r.get("platform") != plat:
                    continue
                wr.writerow({k: r.get(k, "") for k in fields})
        outs.append(out_csv)
    return outs


def aggregate_survival() -> Path:
    rows = _collect_json("survival")
    out_csv = RESULTS / "phase1" / "survival.csv"
    fields = ["platform", "design", "variant", "evidence", "stage", "value"]
    with open(out_csv, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=fields)
        wr.writeheader()
        for r in rows:
            if r.get("evidence") == "Z_R,p_R" and r.get("platform") != "nangate45":
                continue
            wr.writerow({k: r.get(k, "") for k in fields})
    return out_csv


def aggregate_wrong_key() -> List[Path]:
    rows = _collect_json("wrong_key", phase="phase2")
    out_csv = RESULTS / "phase2" / "wrong_key.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    # Per-design summary: preserve all fields needed to audit a row back to the
    # corresponding raw JSON and wrong-key distribution.
    fields = ["platform", "design", "variant", "num_keys", "fraction",
              "alpha_R", "routing_skipped",
              "true_r_P", "true_r_C", "true_Z_R", "true_p_R", "true_r_R",
              "true_Pc", "true_r_all",
              "wrong_key_r_all_mean", "wrong_key_r_all_max",
              "false_positive_rate_r_all", "false_positive_rate_pc",
              # Backward-compatible alias used by older renderers/scripts.
              "false_positive_rate", "dist_csv"]
    with open(out_csv, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=fields)
        wr.writeheader()
        for r in rows:
            out = {k: r.get(k, "") for k in fields}
            out["false_positive_rate"] = r.get(
                "false_positive_rate_r_all",
                r.get("false_positive_rate", ""),
            )
            wr.writerow(out)

    # Evidence-level summary: this directly feeds paper tab:wrong-key.
    wrong = {"r_P": [], "r_C": [], "r_all": [], "p_R": []}
    correct = {"r_P": [], "r_C": [], "r_all": [], "p_R": []}
    false_rall = total_rall = false_pc = total_pc = 0

    for r in rows:
        _append = lambda key, val: (
            correct[key].append(val) if val is not None else None
        )
        _append("r_P", _as_float(r.get("true_r_P")))
        _append("r_C", _as_float(r.get("true_r_C")))
        _append("r_all", _as_float(r.get("true_r_all")))
        _append("p_R", _as_float(r.get("true_p_R")))

        true_rall = _as_float(r.get("true_r_all"))
        true_pc = _as_float(r.get("true_Pc"))
        dist_raw = r.get("dist_csv") or ""
        if not dist_raw:
            continue
        dist_path = Path(dist_raw)
        if not dist_path.is_file():
            continue
        for dr in csv.DictReader(open(dist_path)):
            for key in wrong:
                v = _as_float(dr.get(key))
                if v is not None:
                    wrong[key].append(v)
            r_all = _as_float(dr.get("r_all"))
            if true_rall is not None and r_all is not None:
                total_rall += 1
                if r_all >= true_rall:
                    false_rall += 1
            pc = _as_float(dr.get("pc_all"))
            if true_pc is not None and pc is not None:
                total_pc += 1
                if pc <= true_pc:
                    false_pc += 1

    thresholds = {
        "r_P": r"$\tau_P=0.75$",
        "r_C": r"$\tau_C=0.75$",
        "r_all": r"$\tau_{\mathrm{all}}=0.80$",
        "p_R": r"$\alpha_R=0.05$",
    }
    notes = {
        "p_R": "routing only; ASAP7 skipped because wrong-way counts are zero",
    }
    table_csv = RESULTS / "phase2" / "wrong_key_table.csv"
    table_fields = ["evidence", "wrong_key_n", "wrong_key_mean",
                    "wrong_key_max", "correct_key_n", "correct_key_mean",
                    "threshold", "note"]
    with open(table_csv, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=table_fields)
        wr.writeheader()
        for key in ("r_P", "r_C", "r_all", "p_R"):
            wr.writerow({
                "evidence": key,
                "wrong_key_n": len(wrong[key]),
                "wrong_key_mean": _mean(wrong[key]),
                "wrong_key_max": _max(wrong[key]),
                "correct_key_n": len(correct[key]),
                "correct_key_mean": _mean(correct[key]),
                "threshold": thresholds[key],
                "note": notes.get(key, ""),
            })

    fpr_csv = RESULTS / "phase2" / "wrong_key_fpr.csv"
    fpr_fields = ["metric", "false_positives", "total_trials", "rate"]
    with open(fpr_csv, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=fpr_fields)
        wr.writeheader()
        wr.writerow({
            "metric": "r_all>=true",
            "false_positives": false_rall,
            "total_trials": total_rall,
            "rate": (false_rall / total_rall) if total_rall else "",
        })
        wr.writerow({
            "metric": "pc<=true",
            "false_positives": false_pc,
            "total_trials": total_pc,
            "rate": (false_pc / total_pc) if total_pc else "",
        })

    return [out_csv, table_csv, fpr_csv]


def aggregate_blind() -> Path:
    rows = _collect_json("blind", phase="phase3")
    out_csv = RESULTS / "phase3" / "blind.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fields = ["platform", "design", "stage", "q_s",
              "r_P", "r_C", "Z_R", "p_R", "note"]
    with open(out_csv, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=fields)
        wr.writeheader()
        for r in rows:
            wr.writerow({k: r.get(k, "") for k in fields})
    return out_csv


def aggregate_targeted() -> Path:
    """Roll the per-cell targeted_*.json files into results/phase3/targeted.csv.

    Schema mirrors blind.csv for the post-attack extraction-rate columns
    (r_P, r_C, Z_R, p_R, r_R, r_all, pass_*, accept) plus the targeted-
    specific classifier diagnostics (auc, precision_at_recall, recall_top_K,
    perturbed) and the path of the attacked ODB.  All of these are populated
    by run_targeted_attack.py:attack_one once the placement/CTS top-K mutators
    are wired (paper §7.2).
    """
    rows = _collect_json("targeted", phase="phase3")
    out_csv = RESULTS / "phase3" / "targeted.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fields = ["platform", "design", "stage", "q_s",
              "r_P", "r_C", "Z_R", "p_R",
              "r_R", "r_all",
              "pass_P", "pass_C", "pass_R", "num_pass", "pass_all", "accept",
              "perturbed", "auc", "precision_at_recall", "recall_top_K",
              "atk_odb", "note"]
    with open(out_csv, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=fields)
        wr.writeheader()
        for r in rows:
            wr.writerow({k: r.get(k, "") for k in fields})
    return out_csv


def aggregate_targeted_auc() -> Path:
    """Roll the cached per-(bench,stage) classifier diagnostics into
    results/phase3/targeted_auc.csv -- one row per (platform, design, stage)
    with the cross-validated AUC and precision-at-recall produced by
    attacks/targeted/classify.py.  Feeds tab:targeted_auc.
    """
    raw = RESULTS / "phase3" / "raw" / "datasets"
    rows = []
    if raw.exists():
        for p in sorted(raw.glob("diag_*.json")):
            # diag_<platform>_<design>_<stage>.json
            stem = p.stem[len("diag_"):]
            try:
                d = json.loads(p.read_text())
            except Exception:
                continue
            stage = stem.rsplit("_", 1)[-1]
            rest = stem[: -(len(stage) + 1)]
            platform = rest.split("_", 1)[0]
            design = rest.split("_", 1)[1] if "_" in rest else rest
            rows.append({
                "platform": platform, "design": design, "stage": stage,
                "auc": d.get("auc", ""),
                "precision_at_recall": d.get("precision_at_recall", ""),
                "n": d.get("n", ""), "n_pos": d.get("n_pos", ""),
                "note": d.get("note", ""),
            })
    out_csv = RESULTS / "phase3" / "targeted_auc.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fields = ["platform", "design", "stage", "auc",
              "precision_at_recall", "n", "n_pos", "note"]
    with open(out_csv, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=fields)
        wr.writeheader()
        for r in rows:
            wr.writerow({k: r.get(k, "") for k in fields})
    return out_csv


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--what", default="all",
                    choices=("all", "ref", "capacity", "ppa", "survival",
                             "wrong_key", "blind", "targeted"))
    args = ap.parse_args()
    if args.what in ("all", "ref"):
        p = aggregate_ref();           print(f"[aggregate] {p}")
    if args.what in ("all", "capacity"):
        p = aggregate_capacity();      print(f"[aggregate] {p}")
    if args.what in ("all", "ppa"):
        for p in aggregate_ppa():      print(f"[aggregate] {p}")
    if args.what in ("all", "survival"):
        p = aggregate_survival();      print(f"[aggregate] {p}")
    if args.what in ("all", "wrong_key"):
        for p in aggregate_wrong_key(): print(f"[aggregate] {p}")
    if args.what in ("all", "blind"):
        p = aggregate_blind();         print(f"[aggregate] {p}")
    if args.what in ("all", "targeted"):
        p = aggregate_targeted();      print(f"[aggregate] {p}")
        p = aggregate_targeted_auc();  print(f"[aggregate] {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
