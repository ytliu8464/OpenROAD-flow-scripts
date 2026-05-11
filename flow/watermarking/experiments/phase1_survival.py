#!/usr/bin/env python3.11
# SPDX-License-Identifier: BSD-3-Clause
"""Phase 1.4 survival verification (tab:survival).

For the all-stage watermarked layout of each active bench, verify the
watermark at four checkpoints:

    post_place  : 3_place_order_wm_v2.odb  (embed_dir -- ref variant)
    post_cts    : 4_cts_wm.odb             (embed_dir -- ref variant)
    post_grt    : 5_1_grt.odb             (ppa_dir  -- place_ordering module results)
    post_drt    : 5_route.odb             (ppa_dir  -- place_ordering module results)

Evidence rows emitted per design:
    r_P    placement extraction rate  (1 - x_P / X_P)
    r_C    CTS extraction rate         (1 - x_C / X_C)
    Z_R,p_R routing Z-statistic / one-sided p-value
    r_all  combined evidence (product of per-stage P_c)

Artifact locations:
  embed_dir = flow/results/<platform>/<design>/<wm_flow_variant>/
              (embed/verify CSVs and watermarked ODBs 3_place*/4_cts*)
  ppa_dir   = flow/watermarking/place_ordering/results/<platform>/<design>/<latest>/
              (5_1_grt.odb, 5_route.odb produced by the PPA continuation run)
  route_dir = flow/watermarking/routing_wrong_way/results/<platform>/<design>/<latest>/
              (watermark_nets.txt, route_counts.csv)

Outputs results/phase1/raw/survival_<plat>_<design>_<stage>_<evidence>.json
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from bench_matrix import ACTIVE_BENCHES
from lib.orfs import (
    FLOW_HOME, flow_results,
    wm_module_results, find_latest_wm_variant,
)
from lib.pc import pc_stage, pc_total
from lib.route_stat import read_counts_csv, read_watermark_nets, \
    route_stat_from_counts


OUT_DIR = HERE / "results" / "phase1" / "raw"
OUT_DIR.mkdir(parents=True, exist_ok=True)
DUMP_SH = HERE / "tools" / "dump_route_counts.sh"

# Checkpoints: (stage_name, odb_name, which_dir)
# "embed"  -> embed_dir (flow/results/{wm_flow_variant})
# "ppa"    -> ppa_dir   (place_ordering module results, latest run)
CHECKPOINTS = (
    ("post_place", "3_place_order_wm_v2.odb", "embed"),
    ("post_cts",   "4_cts_wm.odb",            "embed"),
    ("post_grt",   "5_1_grt.odb",             "ppa"),
    ("post_drt",   "5_route.odb",             "ppa"),
)


def _placement_verify(embed_dir: Path, stage_odb: Path) -> tuple:
    """Return (X_P, x_P) by re-running place_ordering verify on stage_odb.

    embed_dir contains the embed CSV; the verify output CSV is written there too.
    """
    embed_csv = embed_dir / "wm_place_order_embed_v2.csv"
    verify_csv = embed_dir / f"wm_place_order_verify_{stage_odb.stem}.csv"
    if not embed_csv.exists() or not stage_odb.exists():
        return 0, 0
    sh = FLOW_HOME / "watermarking" / "place_ordering" / "place_wm.sh"
    env = {
        "WM_CELL_LIST":     str(embed_csv),
        "WM_VERIFY_STAGES": f"{stage_odb.stem}:{stage_odb}",
        "WM_STAGE_REPORT":  str(verify_csv),
    }
    try:
        subprocess.run(["bash", str(sh), "verify_stages"],
                       env={**os.environ, **env},
                       check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return 0, 0
    big = miss = 0
    if verify_csv.exists():
        for row in csv.DictReader(open(verify_csv)):
            big += 1
            if row.get("ok", "1") in ("0", "False", "false", "mismatch"):
                miss += 1
    return big, miss


def _cts_verify(embed_dir: Path, stage_odb: Path) -> tuple:
    """Return (X_C, x_C) via cts_v2 verify on stage_odb."""
    embed_csv = embed_dir / "wm_cts_pairs_embed.csv"
    if not embed_csv.exists() or not stage_odb.exists():
        return 0, 0
    sh = FLOW_HOME / "watermarking" / "cts_v2" / "cts_wm.sh"
    verify_csv = embed_dir / f"wm_cts_verify_{stage_odb.stem}.csv"
    env = {
        "WM_CTS_CELL_LIST":    str(embed_csv),
        "WM_CTS_VERIFY_INPUT": str(stage_odb),
        "WM_CTS_STAGE_REPORT": str(verify_csv),
    }
    try:
        subprocess.run(["bash", str(sh), "verify"],
                       env={**os.environ, **env},
                       check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return 0, 0
    big = miss = 0
    if verify_csv.exists():
        for row in csv.DictReader(open(verify_csv)):
            big += 1
            if row.get("ok", "1") in ("0", "False", "false", "mismatch"):
                miss += 1
    return big, miss


def _routing_stat(route_dir: Path, stage_odb: Path):
    """Dump route_counts for stage_odb (writing into route_dir), then compute stat."""
    if not stage_odb.exists():
        return None
    counts_csv = route_dir / f"route_counts_{stage_odb.stem}.csv"
    wm_nets = route_dir / "watermark_nets.txt"
    if not wm_nets.exists():
        return None
    if not counts_csv.exists():
        try:
            subprocess.run(["bash", str(DUMP_SH)],
                           env={**os.environ,
                                "WM_ODB": str(stage_odb),
                                "WM_COUNTS_CSV": str(counts_csv)},
                           check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            return None
    counts = read_counts_csv(counts_csv)
    wm_set = read_watermark_nets(wm_nets)
    return route_stat_from_counts(counts, wm_set)


def _resolve_dirs(b) -> tuple[Path, Optional[Path], Optional[Path]]:
    """Return (embed_dir, ppa_dir, route_dir) for bench b.

    embed_dir  -- always the reference flow results dir (guaranteed to exist if
                  embedding has been run).
    ppa_dir    -- place_ordering module results dir (None if not yet run).
    route_dir  -- routing_wrong_way module results dir (None if not yet run).
    """
    embed_dir = flow_results(b.platform, b.design, b.wm_flow_variant)

    ppa_var = find_latest_wm_variant("place_ordering", b.platform, b.design)
    ppa_dir: Optional[Path] = (
        wm_module_results("place_ordering", b.platform, b.design, ppa_var)
        if ppa_var else None
    )

    route_var = find_latest_wm_variant("routing_wrong_way", b.platform, b.design)
    route_dir: Optional[Path] = (
        wm_module_results("routing_wrong_way", b.platform, b.design, route_var)
        if route_var else None
    )

    return embed_dir, ppa_dir, route_dir


def emit(rec: dict, slug: str):
    (OUT_DIR / f"survival_{slug}.json").write_text(json.dumps(rec, indent=2))


def main():
    for b in ACTIVE_BENCHES:
        embed_dir, ppa_dir, route_dir = _resolve_dirs(b)
        variant_label = (
            f"embed:{b.wm_flow_variant}"
            + (f"; ppa:{ppa_dir.name}" if ppa_dir else "")
            + (f"; route:{route_dir.name}" if route_dir else "")
        )

        for stage, odb_name, which in CHECKPOINTS:
            # Resolve the ODB path
            if which == "embed":
                odb = embed_dir / odb_name
            else:  # "ppa"
                odb = (ppa_dir / odb_name) if ppa_dir else Path("/dev/null/missing")

            # Placement evidence
            X_P, x_P = _placement_verify(embed_dir, odb)
            r_P = (1 - x_P / X_P) if X_P > 0 else ""
            slug = f"{b.platform}_{b.design}_{stage}_rP"
            emit({"platform": b.platform, "design": b.design,
                  "variant": variant_label, "evidence": "r_P",
                  "stage": stage, "value": r_P, "X": X_P, "x": x_P}, slug)

            # CTS evidence (skip at post_place)
            X_C, x_C = (0, 0) if stage == "post_place" else _cts_verify(embed_dir, odb)
            r_C = (1 - x_C / X_C) if X_C > 0 else ""
            slug = f"{b.platform}_{b.design}_{stage}_rC"
            emit({"platform": b.platform, "design": b.design,
                  "variant": variant_label, "evidence": "r_C",
                  "stage": stage, "value": r_C, "X": X_C, "x": x_C}, slug)

            # Routing evidence (post_grt / post_drt only)
            zr_value = ""
            pR = None
            if stage in ("post_grt", "post_drt") and route_dir is not None:
                rs = _routing_stat(route_dir, odb)
                if rs is not None:
                    zr_value = f"Z={rs.Z_R:.3f}; p={rs.p_R:.3e}"
                    pR = rs.p_R
            slug = f"{b.platform}_{b.design}_{stage}_ZR"
            emit({"platform": b.platform, "design": b.design,
                  "variant": variant_label, "evidence": "Z_R,p_R",
                  "stage": stage, "value": zr_value}, slug)

            # Combined r_all
            pcs = []
            if X_P > 0: pcs.append(pc_stage(X_P, x_P, 0.5))
            if X_C > 0: pcs.append(pc_stage(X_C, x_C, 0.5))
            if pR is not None: pcs.append(pR)
            r_all = f"{pc_total(*pcs):.3e}" if pcs else ""
            slug = f"{b.platform}_{b.design}_{stage}_rall"
            emit({"platform": b.platform, "design": b.design,
                  "variant": variant_label, "evidence": "r_all",
                  "stage": stage, "value": r_all}, slug)

            print(f"[survival] {b.platform}/{b.design}/{stage}: "
                  f"r_P={r_P!r} r_C={r_C!r} Z_R/p_R={zr_value!r} r_all={r_all!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
