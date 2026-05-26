#!/usr/bin/env python3.11
# SPDX-License-Identifier: BSD-3-Clause
"""Per-cell verifier for the parameter-sensitivity sweep.

After ``run_sensitivity.sh`` has produced an embed + PPA continuation for
every ``sens-<stage>-<knob>-<value>`` variant, this script walks those
output dirs and computes the per-stage extraction rate (``r_P`` / ``r_C``
for placement / CTS, ``Z_R`` / ``p_R`` for routing).  Routing dumps the
per-net wrong-way counts via ``tools/dump_route_counts.sh`` and runs the
two-proportion z-test using the design's owner key.

Outputs ``results/phase2/raw/sens_<stage>_<knob>_<value>_<plat>_<design>.json``
with: platform, design, stage, knob, value, variant, r_P, r_C, Z_R, p_R, note.

Aggregator then joins those JSONs into ``sensitivity.csv`` together with
the eligible/selected counts (already extracted from embed logs) and the
ΔPPA columns (from each variant's ``6_report.json``).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parents[1]   # experiments/
sys.path.insert(0, str(HERE))

from bench_matrix import SENSITIVITY_BENCHES
from lib.keys import load_seed_hex
from lib.keyless_verify import routing_wm_set
from lib.orfs import FLOW_HOME, experiment_results
from lib.route_stat import read_counts_csv, route_stat_from_counts
# Reuse the existing verify-CSV parsers from the blind-attack driver.
from attacks.blind.run_blind_attack import (
    _parse_placement_verify, _parse_cts_verify,
)


OPENROAD_EXE = os.environ.get(
    "OPENROAD_EXE",
    "/home/fetzfs_projects/MISC-ytliu/watermarking/OR0415/OpenROAD/build/bin/openroad")
SIF = os.environ.get("SINGULARITY_SIF",
                     "/home/tool/singularity/images/ispd26.sif")

RAW_OUT = HERE / "results" / "phase2" / "raw"
RAW_OUT.mkdir(parents=True, exist_ok=True)

# Variant layout written by run_sensitivity.sh:
#   sens-<stage_initial>-<label>-<value>      e.g. sens-p-D_pair-1.0
_VAR_RX = re.compile(
    r"^sens-(?P<stage>p|c|r)-(?P<knob>[^-]+)-(?P<value>[^-]+)$"
)

_STAGE_NAME = {"p": "placement", "c": "cts", "r": "routing"}


# ---------------------------------------------------------------------------
# Per-stage verify helpers
# ---------------------------------------------------------------------------

def _verify_placement(b, variant: str) -> dict:
    rdir = experiment_results(b.platform, b.design_nickname, variant)
    # The embed step in run_p_only writes wm_place_order_embed.csv + .odb
    # under FLOW_VARIANT.  place_wm.sh verify_stages reads the CSV (the
    # constraints list) and the post-stage ODB, then writes its own
    # verify CSV that _parse_placement_verify consumes.
    embed_csv = rdir / "wm_place_order_embed.csv"
    embed_odb = rdir / "3_place_order_wm.odb"
    if not embed_csv.exists():
        # Variant ran the all-stage CSV instead.
        embed_csv = rdir / "wm_place_order_embed_all_stage.csv"
    if not embed_csv.exists() or not embed_odb.exists():
        return {"r_P": None,
                "note": f"missing embed artifacts under {rdir}"}
    v_csv = RAW_OUT / f"verify_p_{b.platform}_{b.design}_{variant}.csv"
    sh = FLOW_HOME / "watermarking" / "place_ordering" / "place_wm.sh"
    log = RAW_OUT / f"verify_p_{b.platform}_{b.design}_{variant}.log"
    rc = subprocess.run(
        ["bash", str(sh), "verify_stages"],
        env={**os.environ,
             "WM_CELL_LIST":     str(embed_csv),
             "WM_VERIFY_STAGES": f"post_embed:{embed_odb}",
             "WM_STAGE_REPORT":  str(v_csv)},
        stdout=open(log, "w"), stderr=subprocess.STDOUT).returncode
    r_P = _parse_placement_verify(v_csv)
    note = "" if r_P is not None else f"verify produced no usable CSV (log={log})"
    if rc != 0 and r_P is None:
        note = (note + "; " if note else "") + f"verify_stages rc={rc}"
    return {"r_P": r_P, "note": note}


def _verify_cts(b, variant: str) -> dict:
    rdir = experiment_results(b.platform, b.design_nickname, variant)
    embed_csv = rdir / "wm_cts_pairs_embed.csv"
    embed_odb = rdir / "4_cts_wm.odb"
    if not embed_csv.exists():
        embed_csv = rdir / "wm_cts_pairs_embed_all_stage.csv"
    if not embed_csv.exists() or not embed_odb.exists():
        return {"r_C": None,
                "note": f"missing embed artifacts under {rdir}"}
    v_csv = RAW_OUT / f"verify_c_{b.platform}_{b.design}_{variant}.csv"
    sh = FLOW_HOME / "watermarking" / "cts_v2" / "cts_wm.sh"
    log = RAW_OUT / f"verify_c_{b.platform}_{b.design}_{variant}.log"
    rc = subprocess.run(
        ["bash", str(sh), "verify"],
        env={**os.environ,
             "WM_CELL_LIST":        str(embed_csv),
             "WM_CTS_VERIFY_INPUT": str(embed_odb),
             "WM_CTS_VERIFY_CSV":   str(v_csv)},
        stdout=open(log, "w"), stderr=subprocess.STDOUT).returncode
    r_C = _parse_cts_verify(v_csv)
    note = "" if r_C is not None else f"verify produced no usable CSV (log={log})"
    if rc != 0 and r_C is None:
        note = (note + "; " if note else "") + f"cts verify rc={rc}"
    return {"r_C": r_C, "note": note}


def _verify_routing(b, variant: str, value: str, knob: str) -> dict:
    """Dump per-net counts on the routed ODB and run the two-proportion
    z-test against the keyed WM-net set."""
    rdir = experiment_results(b.platform, b.design_nickname, variant)
    routed = rdir / "5_route.odb"
    if not routed.exists():
        return {"Z_R": None, "p_R": None,
                "note": f"no 5_route.odb under {rdir}"}
    counts_csv = RAW_OUT / f"counts_r_{b.platform}_{b.design}_{variant}.csv"
    dump_log   = RAW_OUT / f"counts_r_{b.platform}_{b.design}_{variant}.log"
    sh = HERE / "tools" / "dump_route_counts.sh"
    subprocess.run([str(sh)],
                   env={**os.environ,
                        "WM_ODB":        str(routed),
                        "WM_COUNTS_CSV": str(counts_csv)},
                   stdout=open(dump_log, "w"),
                   stderr=subprocess.STDOUT, check=False)
    if not counts_csv.exists():
        return {"Z_R": None, "p_R": None,
                "note": f"dump_route_counts produced no CSV (log={dump_log})"}
    seed_path = FLOW_HOME / "watermarking" / "gen_key" / "out" / b.design / "seed_routing.hex"
    if not seed_path.exists():
        return {"Z_R": None, "p_R": None,
                "note": f"missing seed_routing.hex at {seed_path}"}
    sr = load_seed_hex(seed_path)
    counts = read_counts_csv(counts_csv)
    # The owner verifier uses the *attack's* watermark fraction value for
    # the WM-net reconstruction.  For the f-sweep we want the sweep value;
    # for the lambda_wm sweep we keep the default fraction (matches embed).
    try:
        fraction = float(value) if knob == "f" else 0.05
    except ValueError:
        fraction = 0.05
    wm = routing_wm_set(sr, counts.keys(), fraction)
    st = route_stat_from_counts(counts, wm)
    return {"Z_R": st.Z_R, "p_R": st.p_R, "note": ""}


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def _iter_variants_for(b):
    """Yield every sens-* FLOW_VARIANT directory that exists for this bench
    under experiments/results/<plat>/<nick>/.

    Note: experiment_results(plat, nick, "") returns ``.../<plat>/<nick>``
    (pathlib drops the empty trailing component), which is already the
    design dir.  Earlier code applied ``.parent`` here by mistake, which
    walked up to the platform dir and never found any sens-* entries.
    """
    base = experiment_results(b.platform, b.design_nickname, "")
    if not base.is_dir():
        return
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        m = _VAR_RX.match(d.name)
        if not m:
            continue
        yield d.name, m.group("stage"), m.group("knob"), m.group("value")


def main() -> int:
    for b in SENSITIVITY_BENCHES:
        for variant, stage_initial, knob, value in _iter_variants_for(b):
            stage = _STAGE_NAME[stage_initial]
            rec = {"platform": b.platform, "design": b.design,
                   "stage": stage, "knob": knob, "value": value,
                   "variant": variant,
                   "r_P": None, "r_C": None,
                   "Z_R": None, "p_R": None,
                   "note": ""}
            if stage == "placement":
                rec.update(_verify_placement(b, variant))
            elif stage == "cts":
                rec.update(_verify_cts(b, variant))
            elif stage == "routing":
                rec.update(_verify_routing(b, variant, value, knob))

            out = RAW_OUT / f"sens_{stage_initial}_{knob}_{value}_{b.platform}_{b.design}.json"
            out.write_text(json.dumps(rec, indent=2, default=str))
            print(f"[verify_sweep] {variant} {b.platform}/{b.design}: "
                  f"r_P={rec.get('r_P')}  r_C={rec.get('r_C')}  "
                  f"Z_R={rec.get('Z_R')}  p_R={rec.get('p_R')}  "
                  f"{rec.get('note','')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
