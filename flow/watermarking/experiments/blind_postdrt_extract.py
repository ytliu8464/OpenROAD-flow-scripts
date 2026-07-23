#!/usr/bin/env python3.11
# SPDX-License-Identifier: BSD-3-Clause
"""Re-measure blind-attack placement/CTS extraction on the POST-DRT layout.

The original run_blind_attack.py measures r_P / r_C on the immediately-perturbed
stage ODB (placement: post-swap+legalize; cts: post-sink-move) WITHOUT re-running
the downstream flow.  run_attack_ppa.py separately re-ran the downstream flow on
those attacked ODBs for PPA, leaving full post-DRT layouts on disk at
  results/<plat>/<nick>/atk-{p,c}-<design>-qs<q>/5_route.odb

This script re-runs the SAME placement/CTS verifiers (place_wm.sh verify_stages /
cts_wm.sh verify) on those post-DRT ODBs, so extraction reflects the attacked
design after re-running CTS+route+finish.  Read-only; no flow re-runs.

Writes results/phase3/blind_postdrt.csv.
"""
from __future__ import annotations
import csv, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "attacks" / "blind"))
from bench_matrix import ACTIVE_BENCHES
from lib.orfs import FLOW_HOME, experiment_results
from run_blind_attack import (_pick_embed_dir, _parse_placement_verify,
                              _parse_cts_verify)

QS = ["0.1", "0.2", "0.5", "0.8", "1.0"]
OUT = HERE / "results" / "phase3" / "blind_postdrt.csv"
PLACE_SH = FLOW_HOME / "watermarking" / "place_ordering" / "place_wm.sh"
CTS_SH = FLOW_HOME / "watermarking" / "cts_v2" / "cts_wm.sh"
# 8 paper designs (skip AES, not in tab:blind_attack)
PAPER = {("nangate45", "jpeg"), ("nangate45", "swerv_wrapper"),
         ("nangate45", "ariane136"), ("nangate45", "bp_multi_top"),
         ("asap7", "jpeg"), ("asap7", "swerv_wrapper"),
         ("asap7", "cva6"), ("asap7", "ariane")}


def _postdrt_odb(b, st, q):
    variant = f"atk-{st}-{b.design}-qs{q}"
    return experiment_results(b.platform, b.design_nickname, variant) / "5_route.odb"


def main():
    rows = []
    for b in ACTIVE_BENCHES:
        if (b.platform, b.design) not in PAPER:
            continue
        embed_dir, p_odb, p_csv, c_odb, c_csv = _pick_embed_dir(
            b.platform, b.design_nickname, b.wm_flow_variant)
        for q in QS:
            # ---- placement r_P on post-DRT ----
            podb = _postdrt_odb(b, "p", q)
            rP = note_p = ""
            if podb.exists() and p_csv.exists():
                vcsv = HERE / "results" / "phase3" / "raw" / \
                    f"blind_postdrt_p_{b.platform}_{b.design}_qs{q}.csv"
                log = vcsv.with_suffix(".log")
                with open(log, "w") as lf:
                    subprocess.run(["bash", str(PLACE_SH), "verify_stages"],
                        env={**_env(), "WM_CELL_LIST": str(p_csv),
                             "WM_VERIFY_STAGES": f"drt:{podb}",
                             "WM_STAGE_REPORT": str(vcsv)},
                        stdout=lf, stderr=subprocess.STDOUT, timeout=1800)
                v = _parse_placement_verify(vcsv)
                rP = v if v is not None else ""
                note_p = "" if v is not None else f"parse_fail(log={log})"
            else:
                note_p = "missing_odb_or_csv"
            rows.append({"platform": b.platform, "design": b.design,
                         "stage": "placement", "q_s": q, "r": rP, "note": note_p})
            print(f"[postdrt] {b.platform}/{b.design} placement q={q} r_P={rP} {note_p}", flush=True)

            # ---- cts r_C on post-DRT ----
            codb = _postdrt_odb(b, "c", q)
            rC = note_c = ""
            if codb.exists() and c_csv.exists():
                vcsv = HERE / "results" / "phase3" / "raw" / \
                    f"blind_postdrt_c_{b.platform}_{b.design}_qs{q}.csv"
                log = vcsv.with_suffix(".log")
                with open(log, "w") as lf:
                    subprocess.run(["bash", str(CTS_SH), "verify"],
                        env={**_env(), "WM_CELL_LIST": str(c_csv),
                             "WM_CTS_VERIFY_INPUT": str(codb),
                             "WM_CTS_VERIFY_CSV": str(vcsv)},
                        stdout=lf, stderr=subprocess.STDOUT, timeout=1800)
                v = _parse_cts_verify(vcsv)
                rC = v if v is not None else ""
                note_c = "" if v is not None else f"parse_fail(log={log})"
            else:
                note_c = "missing_odb_or_csv"
            rows.append({"platform": b.platform, "design": b.design,
                         "stage": "cts", "q_s": q, "r": rC, "note": note_c})
            print(f"[postdrt] {b.platform}/{b.design} cts       q={q} r_C={rC} {note_c}", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["platform", "design", "stage", "q_s", "r", "note"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"[postdrt] wrote {OUT}")


def _env():
    import os
    return dict(os.environ)


if __name__ == "__main__":
    raise SystemExit(main())
