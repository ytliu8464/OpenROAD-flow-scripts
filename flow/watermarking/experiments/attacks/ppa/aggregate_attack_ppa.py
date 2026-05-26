#!/usr/bin/env python3.11
# SPDX-License-Identifier: BSD-3-Clause
"""Aggregate delta-PPA after blind-attack PPA continuation.

After ``run_attack_ppa.py`` finishes, each (design, stage, q_s) combo has its
own ORFS run under experiment FLOW_VARIANT ``atk-<initial>-<design>-qs<q>``,
producing a 6_report.json with post-attack timing / area / power / runtime.

This script reads those 6_report.json files and computes deltas against two
reference points:

  * ref       -- the original (un-watermarked) reference flow under
                 b.wm_flow_variant.  Captures total PPA cost of watermark + attack.
  * unattacked watermark -- the watermark-only run (pdmarks-p-only or
                 pdmarks-c-only), auto-discovered with find_latest_experiment_variant.
                 Captures the incremental PPA cost of the attack alone.

Output: results/phase3/blind_ppa.csv with columns
  platform, design, stage, q_s, attack_variant,
  dWNS_vs_ref, dTNS_vs_ref, dRWL_vs_ref, dPower_vs_ref, dRuntime_vs_ref,
  dWNS_vs_wm,  dTNS_vs_wm,  dRWL_vs_wm,  dPower_vs_wm,  dRuntime_vs_wm,
  note
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parents[2]   # .../watermarking/experiments
sys.path.insert(0, str(HERE))
from bench_matrix import ACTIVE_BENCHES, Bench
from lib.orfs import (
    experiment_logs, load_experiment_metrics, load_reference,
    find_latest_experiment_variant,
)

OUT_CSV = HERE / "results" / "phase3" / "blind_ppa.csv"

# atk-{tp,tc,tr,p,c,r,all}-<design>-qs<q>
#   p  = blind placement      (continued via place_ordering/run_ppa.sh)
#   c  = blind cts            (continued via cts_v2/run_ppa.sh)
#   r  = blind routing        (already complete: run_attack_route.sh runs ORFS)
#   tp = targeted placement   (same back-end as p; ranked top-K mutation)
#   tc = targeted cts         (same back-end as c; ranked top-K mutation)
#   tr = targeted routing     (run_attack_route.sh on classifier-ranked nets)
#   all = blind all-stage     (each per-stage attack writes its own ODB; the
#                              final 6_report.json lives under the route step)
# Longer alternatives come first so the regex doesn't shadow "tp"/"tc"/"tr"
# with the leading "p"/"c"/"r".
_VAR_RX = re.compile(
    r"^atk-(?P<initial>tp|tc|tr|p|c|r|all)-(?P<design>.+)-qs(?P<qs>[0-9.eE+-]+)$"
)

_INITIAL_TO_STAGE = {
    "p":   "placement",
    "c":   "cts",
    "r":   "routing",
    "tp":  "targeted_placement",
    "tc":  "targeted_cts",
    "tr":  "targeted_routing",
    "all": "all_stage",
}

# Map attack initial -> wm-only baseline variant prefix for the *_vs_wm delta.
_INITIAL_TO_WM = {
    "p":   "pdmarks-p-only",
    "c":   "pdmarks-c-only",
    "r":   "pdmarks-r-only",
    "tp":  "pdmarks-p-only",
    "tc":  "pdmarks-c-only",
    "tr":  "pdmarks-r-only",
    "all": "pdmarks-all-stage",
}


def _delta(ref, cur):
    if ref is None or cur is None:
        return ""
    if ref == 0:
        return cur - ref
    return (cur - ref) / max(abs(ref), 1e-12)


def _wm_only_variant_for(b: Bench, initial: str) -> Optional[str]:
    """Return the latest unattacked-watermarked variant for this bench."""
    prefix = _INITIAL_TO_WM.get(initial)
    if prefix is None:
        return None
    return find_latest_experiment_variant(b.platform, b.design_nickname, prefix)


def _scan_attack_variants(b: Bench):
    """Yield (stage, q_s, variant_name, initial) for every atk-*-<design>-qs*
    run with a 6_report.json under the experiment logs dir for this bench.

    Note: ``experiment_logs(plat, nick, "")`` already returns the design dir
    (Python pathlib drops the empty trailing component), so we use it as-is.
    Earlier .parent here mistakenly stepped UP to the platform dir, which
    is why no variants were ever discovered.
    """
    log_base = experiment_logs(b.platform, b.design_nickname, "")
    if not log_base.is_dir():
        return
    for d in sorted(log_base.iterdir()):
        if not d.is_dir():
            continue
        m = _VAR_RX.match(d.name)
        if not m:
            continue
        # Both b.design and b.design_nickname are acceptable in the FLOW_VARIANT
        if m.group("design") not in (b.design, b.design_nickname):
            continue
        if not (d / "6_report.json").exists():
            continue
        initial = m.group("initial")
        stage = _INITIAL_TO_STAGE[initial]
        yield stage, m.group("qs"), d.name, initial


def aggregate() -> Path:
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "platform", "design", "stage", "q_s", "attack_variant",
        "dWNS_vs_ref",  "dTNS_vs_ref",  "dRWL_vs_ref",
        "dPower_vs_ref","dRuntime_vs_ref",
        "dWNS_vs_wm",   "dTNS_vs_wm",   "dRWL_vs_wm",
        "dPower_vs_wm", "dRuntime_vs_wm",
        "wm_baseline_variant", "note",
    ]
    rows = []
    for b in ACTIVE_BENCHES:
        ref = load_reference(b.platform, b.design_nickname, b.wm_flow_variant)
        for stage, q_s, variant, initial in _scan_attack_variants(b):
            atk = load_experiment_metrics(b.platform, b.design_nickname, variant)
            wm_var = _wm_only_variant_for(b, initial)
            wm = (load_experiment_metrics(b.platform, b.design_nickname, wm_var)
                  if wm_var else None)

            row = {
                "platform": b.platform, "design": b.design,
                "stage": stage, "q_s": q_s, "attack_variant": variant,
                "wm_baseline_variant": wm_var or "",
                "note": "",
            }
            if atk is None:
                row["note"] = "no metrics in 6_report.json"
            for col, ref_v, cur_v in (
                ("dWNS_vs_ref",     ref.wns_ns,    atk.wns_ns    if atk else None),
                ("dTNS_vs_ref",     ref.tns_ns,    atk.tns_ns    if atk else None),
                ("dRWL_vs_ref",     ref.rwl_um,    atk.rwl_um    if atk else None),
                ("dPower_vs_ref",   ref.power_w,   atk.power_w   if atk else None),
                ("dRuntime_vs_ref", ref.runtime_s, atk.runtime_s if atk else None),
            ):
                row[col] = _delta(ref_v, cur_v)
            for col, ref_v, cur_v in (
                ("dWNS_vs_wm",     wm.wns_ns    if wm else None, atk.wns_ns    if atk else None),
                ("dTNS_vs_wm",     wm.tns_ns    if wm else None, atk.tns_ns    if atk else None),
                ("dRWL_vs_wm",     wm.rwl_um    if wm else None, atk.rwl_um    if atk else None),
                ("dPower_vs_wm",   wm.power_w   if wm else None, atk.power_w   if atk else None),
                ("dRuntime_vs_wm", wm.runtime_s if wm else None, atk.runtime_s if atk else None),
            ):
                row[col] = _delta(ref_v, cur_v)
            rows.append(row)

    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})

    print(f"[aggregate_attack_ppa] {len(rows)} rows -> {OUT_CSV}")
    return OUT_CSV


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.parse_args()
    aggregate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
