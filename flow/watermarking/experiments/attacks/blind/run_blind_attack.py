#!/usr/bin/env python3.11
# SPDX-License-Identifier: BSD-3-Clause
"""Run all blind attacks across all active benches and q_s values.

For each (design, stage, q_s) we:
- copy the all-stage variant routed ODB
- run the corresponding attacker (placement / cts on the ODB, routing on the
  route_counts CSV)
- verify with the *true* key and record r_P / r_C / Z_R / p_R post-attack.

Results saved to results/phase3/raw/blind_<plat>_<design>_<stage>_qs<q>.json.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE))
from bench_matrix import ACTIVE_BENCHES
from lib.keys import load_seed_hex
from lib.keyless_verify import placement_extraction_rate, cts_extraction_rate, \
    routing_wm_set
from lib.orfs import (
    FLOW_HOME, flow_results,
    wm_module_results, find_latest_wm_variant,
)
from lib.route_stat import read_counts_csv, route_stat_from_counts

OPENROAD_EXE = os.environ.get(
    "OPENROAD_EXE",
    "/home/fetzfs_projects/MISC-ytliu/watermarking/OR0415/OpenROAD/build/bin/openroad")
SIF = os.environ.get("SINGULARITY_SIF",
                     "/home/tool/singularity/images/ispd26.sif")


def or_python(script: Path, env: dict) -> int:
    cmd = ["singularity", "exec", "-B", "/home", SIF, OPENROAD_EXE,
           "-python", "-exit", str(script)]
    return subprocess.run(cmd, env={**os.environ, **env}).returncode


def _get_route_dir(b) -> "Path | None":
    """Return the routing_wrong_way module results dir (or None if not run yet)."""
    route_var = find_latest_wm_variant("routing_wrong_way", b.platform, b.design)
    if not route_var:
        return None
    return wm_module_results("routing_wrong_way", b.platform, b.design, route_var)


def attack_one(b, stage: str, q_s: float, out_root: Path):
    # embed/verify CSVs and watermarked ODBs live in the reference flow results
    embed_dir = flow_results(b.platform, b.design, b.wm_flow_variant)
    if not embed_dir.exists():
        return {"platform": b.platform, "design": b.design, "stage": stage,
                "q_s": q_s, "note": f"embed dir missing: {embed_dir}"}

    seeds_dir = FLOW_HOME / "watermarking" / "gen_key" / "out" / b.design
    sp = load_seed_hex(seeds_dir / "seed_placement.hex")
    sc = load_seed_hex(seeds_dir / "seed_cts.hex")
    sr = load_seed_hex(seeds_dir / "seed_routing.hex")

    rec = {"platform": b.platform, "design": b.design, "stage": stage,
           "q_s": q_s, "r_P": "", "r_C": "", "Z_R": "", "p_R": ""}

    if stage == "placement":
        in_odb = embed_dir / "3_place_order_wm_v2.odb"
        out_odb = out_root / f"atk_p_{b.platform}_{b.design}_qs{q_s}.odb"
        if not in_odb.exists():
            rec["note"] = "no placed odb"
            return rec
        or_python(HERE / "attacks" / "blind" / "attack_placement.py",
                  {"WM_ODB": str(in_odb), "WM_OUT_ODB": str(out_odb),
                   "ATK_QS": str(q_s)})
        v_csv = out_root / f"atk_p_{b.platform}_{b.design}_qs{q_s}_verify.csv"
        sh = FLOW_HOME / "watermarking" / "place_ordering" / "place_wm.sh"
        subprocess.run(["bash", str(sh), "verify_stages"],
                       env={**os.environ,
                            "WM_CELL_LIST": str(embed_dir / "wm_place_order_embed_v2.csv"),
                            "WM_VERIFY_STAGES": f"atk:{out_odb}",
                            "WM_STAGE_REPORT": str(v_csv)},
                       check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if v_csv.exists():
            X, x = placement_extraction_rate(
                embed_dir / "wm_place_order_embed_v2.csv", v_csv, sp)
            rec["r_P"] = (1 - x / X) if X > 0 else ""

    elif stage == "cts":
        in_odb = embed_dir / "4_cts_wm.odb"
        out_odb = out_root / f"atk_c_{b.platform}_{b.design}_qs{q_s}.odb"
        if not in_odb.exists():
            rec["note"] = "no cts_wm odb"
            return rec
        or_python(HERE / "attacks" / "blind" / "attack_cts.py",
                  {"WM_ODB": str(in_odb), "WM_OUT_ODB": str(out_odb),
                   "ATK_QS": str(q_s)})
        v_csv = out_root / f"atk_c_{b.platform}_{b.design}_qs{q_s}_verify.csv"
        sh = FLOW_HOME / "watermarking" / "cts_v2" / "cts_wm.sh"
        subprocess.run(["bash", str(sh), "verify"],
                       env={**os.environ,
                            "WM_CTS_CELL_LIST": str(embed_dir / "wm_cts_pairs_embed.csv"),
                            "WM_CTS_VERIFY_INPUT": str(out_odb),
                            "WM_CTS_STAGE_REPORT": str(v_csv)},
                       check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if v_csv.exists():
            X, x = cts_extraction_rate(
                embed_dir / "wm_cts_pairs_embed.csv", v_csv, sc)
            rec["r_C"] = (1 - x / X) if X > 0 else ""

    elif stage == "routing":
        # route_counts.csv and watermark_nets.txt live in the routing_wrong_way
        # module results directory, NOT in the reference flow results dir.
        route_dir = _get_route_dir(b)
        if route_dir is None:
            rec["note"] = "routing_wrong_way results dir not found"
            return rec
        rc_in = route_dir / "route_counts.csv"
        if not rc_in.exists():
            rec["note"] = f"no route_counts.csv in {route_dir}"
            return rec
        rc_out = out_root / f"atk_r_{b.platform}_{b.design}_qs{q_s}_counts.csv"
        subprocess.run(["python3.11", str(HERE / "attacks" / "blind"
                                          / "attack_routing.py")],
                       env={**os.environ,
                            "WM_ROUTE_COUNTS_IN":  str(rc_in),
                            "WM_ROUTE_COUNTS_OUT": str(rc_out),
                            "ATK_QS": str(q_s)},
                       check=True)
        counts = read_counts_csv(rc_out)
        wm = routing_wm_set(sr, counts.keys(),
                            float(os.environ.get("WATERMARK_FRACTION", "0.05")))
        st = route_stat_from_counts(counts, wm)
        rec["Z_R"] = st.Z_R
        rec["p_R"] = st.p_R

    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qs-list", default="0.01,0.05,0.10,0.20")
    ap.add_argument("--stages", default="placement,cts,routing")
    args = ap.parse_args()
    out_root = HERE / "results" / "phase3" / "raw"
    out_root.mkdir(parents=True, exist_ok=True)
    qs_list = [float(x) for x in args.qs_list.split(",") if x]
    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    for b in ACTIVE_BENCHES:
        for stg in stages:
            for q_s in qs_list:
                rec = attack_one(b, stg, q_s, out_root)
                slug = f"{b.platform}_{b.design}_{stg}_qs{q_s}"
                (out_root / f"blind_{slug}.json").write_text(json.dumps(rec, indent=2))
                print(f"[blind] {slug}: r_P={rec.get('r_P','')} "
                      f"r_C={rec.get('r_C','')} Z_R={rec.get('Z_R','')} "
                      f"p_R={rec.get('p_R','')} {rec.get('note','')}")


if __name__ == "__main__":
    main()
