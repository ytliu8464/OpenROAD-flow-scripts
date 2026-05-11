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
            ref = load_reference(b.platform, b.design, b.wm_flow_variant)
            row = ref.to_dict()
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
                  "dWNS", "dTNS", "dRWL", "dPower", "dRuntime", "Pc"]
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
            wr.writerow({k: r.get(k, "") for k in fields})
    return out_csv


def aggregate_wrong_key() -> Path:
    rows = _collect_json("wrong_key", phase="phase2")
    out_csv = RESULTS / "phase2" / "wrong_key.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fields = ["platform", "design", "variant", "num_keys", "fraction",
              "true_r_P", "true_r_C", "true_Z_R", "true_p_R", "true_Pc",
              "false_positive_rate"]
    with open(out_csv, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=fields)
        wr.writeheader()
        for r in rows:
            wr.writerow({k: r.get(k, "") for k in fields})
    return out_csv


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
    rows = _collect_json("targeted", phase="phase3")
    out_csv = RESULTS / "phase3" / "targeted.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fields = ["platform", "design", "stage", "q_s",
              "Z_R", "p_R", "precision_topK", "perturbed", "note"]
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
        p = aggregate_wrong_key();     print(f"[aggregate] {p}")
    if args.what in ("all", "blind"):
        p = aggregate_blind();         print(f"[aggregate] {p}")
    if args.what in ("all", "targeted"):
        p = aggregate_targeted();      print(f"[aggregate] {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
