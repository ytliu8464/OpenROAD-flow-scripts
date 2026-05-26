#!/usr/bin/env python3.11
# SPDX-License-Identifier: BSD-3-Clause
"""Phase 1.4 survival verification (tab:survival).

For the all-stage watermarked layout of each active bench, verify the
watermark at four checkpoints:

    post_place  : 3_place_order_wm.odb  (embed_dir  = pdmarks-all-stage)
    post_cts    : 4_cts_wm.odb          (embed_dir  = pdmarks-all-stage)
    post_grt    : 5_1_grt.odb           (ppa_dir    = pdmarks-all-stage-routed)
    post_drt    : 5_route.odb           (ppa_dir    = pdmarks-all-stage-routed)

Evidence rows emitted per design:
    r_P    placement extraction rate  (1 - x_P / X_P)
    r_C    CTS extraction rate         (1 - x_C / X_C)
    Z_R,p_R routing Z-statistic / one-sided p-value
    r_all  combined extraction rate across available evidence channels

Artifact locations:
  embed_dir = experiments/results/<platform>/<design>/pdmarks-all-stage/
              (embed/verify CSVs and watermarked ODBs 3_place_order_wm.odb,
               4_cts_wm.odb)
  ppa_dir   = experiments/results/<platform>/<design>/pdmarks-all-stage-routed/
              (5_1_grt.odb, 5_route.odb, watermark_nets.txt)
              Auto-discovered as the most-recent pdmarks-all-stage* dir that
              contains 5_route.odb.  Override with PDMARKS_SURVIVAL_ROUTED_VARIANT.

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
    FLOW_HOME, experiment_results,
)
from lib.route_stat import read_counts_csv, read_watermark_nets, \
    route_stat_from_counts


OUT_DIR = HERE / "results" / "phase1" / "raw"
OUT_DIR.mkdir(parents=True, exist_ok=True)
DUMP_SH = HERE / "tools" / "dump_route_counts.sh"
ALPHA_R = 0.05

# Checkpoints: (stage_name, odb_name, which_dir)
# "embed"  -> embed_dir  (pdmarks-all-stage)
# "ppa"    -> ppa_dir    (pdmarks-all-stage-routed, auto-discovered)
CHECKPOINTS = (
    ("post_place", "3_place_order_wm.odb",    "embed"),
    ("post_cts",   "4_cts_wm.odb",            "embed"),
    ("post_grt",   "5_1_grt.odb",             "ppa"),
    ("post_drt",   "5_route.odb",             "ppa"),
)


def _is_false_flag(value: str) -> bool:
    return value in ("0", "False", "false", "mismatch")


def _is_true_flag(value: str) -> bool:
    return value in ("1", "True", "true", "missing")


def _read_place_verify_csv(verify_csv: Path) -> tuple[int, int]:
    """Return (total constraints, missing constraints) from place verifier CSV.

    Older verifier outputs are row-wise and expose an ``ok`` column.  The
    current place_ordering verifier writes one summary row with
    ``constraints_ok`` and ``constraints_total``.  Accept both formats so the
    survival table can be regenerated from cached verifier artifacts.
    """
    if not verify_csv.exists():
        return 0, 0
    big = miss = 0
    for row in csv.DictReader(open(verify_csv)):
        if "constraints_ok" in row and "constraints_total" in row:
            total = int(float(row.get("constraints_total") or 0))
            ok = int(float(row.get("constraints_ok") or 0))
            big += total
            miss += max(total - ok, 0)
            continue
        big += 1
        if _is_false_flag(row.get("ok", "1")):
            miss += 1
    return big, miss


def _read_cts_verify_csv(verify_csv: Path) -> tuple[int, int]:
    """Return (total pairs, missing/unsatisfied pairs) from CTS verifier CSV."""
    if not verify_csv.exists():
        return 0, 0
    big = miss = 0
    for row in csv.DictReader(open(verify_csv)):
        if "pairs_ok" in row and "pairs_total" in row:
            total = int(float(row.get("pairs_total") or 0))
            ok = int(float(row.get("pairs_ok") or 0))
            big += total
            miss += max(total - ok, 0)
            continue
        big += 1
        if _is_false_flag(row.get("ok", "1")):
            miss += 1
        elif _is_false_flag(row.get("satisfied", "True")):
            miss += 1
        elif _is_true_flag(row.get("missing", "False")):
            miss += 1
    return big, miss


def _placement_verify(embed_dir: Path, stage_odb: Path) -> tuple:
    """Return (X_P, x_P) by re-running place_ordering verify on stage_odb.

    embed_dir contains the embed CSV; the verify output CSV is written there too.
    """
    embed_csv = embed_dir / "wm_place_order_embed_all_stage.csv"
    if not embed_csv.exists():
        embed_csv = embed_dir / "wm_place_order_embed.csv"
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
        pass
    return _read_place_verify_csv(verify_csv)


def _cts_verify(embed_dir: Path, stage_odb: Path) -> tuple:
    """Return (X_C, x_C) via cts_v2 verify on stage_odb."""
    embed_csv = embed_dir / "wm_cts_pairs_embed_all_stage.csv"
    if not embed_csv.exists():
        embed_csv = embed_dir / "wm_cts_pairs_embed.csv"
    if not embed_csv.exists() or not stage_odb.exists():
        return 0, 0
    sh = FLOW_HOME / "watermarking" / "cts_v2" / "cts_wm.sh"
    verify_csv = embed_dir / f"wm_cts_verify_{stage_odb.stem}.csv"
    env = {
        "WM_CELL_LIST":        str(embed_csv),
        "WM_CTS_VERIFY_INPUT": str(stage_odb),
        "WM_CTS_VERIFY_CSV":   str(verify_csv),
    }
    try:
        subprocess.run(["bash", str(sh), "verify"],
                       env={**os.environ, **env},
                       check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    return _read_cts_verify_csv(verify_csv)


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


def _find_routed_dir(platform: str, design: str, fallback: Path) -> Path:
    """Return the most-recent pdmarks-all-stage* results dir with 5_route.odb.

    The all-stage flow splits across two variants: the embed step writes into
    pdmarks-all-stage (placement/CTS ODBs + embed CSVs), while the routing +
    finishing step writes into pdmarks-all-stage-routed (5_*.odb, watermark_nets.txt).
    This helper finds the routed variant automatically so callers do not need to
    hard-code it.  Falls back to ``fallback`` when no routed variant is found.
    """
    base = experiment_results(platform, design, "")  # .../results/<plat>/<design>
    best: Optional[Path] = None
    best_mtime = -1.0
    for d in (base.iterdir() if base.is_dir() else []):
        if not d.is_dir() or not d.name.startswith("pdmarks-all-stage"):
            continue
        if (d / "5_route.odb").exists():
            mtime = d.stat().st_mtime
            if mtime > best_mtime:
                best_mtime = mtime
                best = d
    return best if best is not None else fallback


def _resolve_dirs(b) -> tuple[Path, Optional[Path], Optional[Path]]:
    """Return (embed_dir, ppa_dir, route_dir) for bench b.

    embed_dir  -- pdmarks-all-stage results dir (embed CSVs, early ODBs).
    ppa_dir    -- pdmarks-all-stage-routed results dir (routing ODBs,
                  watermark_nets.txt).  Auto-discovered; None if not found.
    route_dir  -- same as ppa_dir when watermark_nets.txt is present.
    """
    embed_variant = os.environ.get("PDMARKS_SURVIVAL_EMBED_VARIANT", "pdmarks-all-stage")
    # Paths under experiments/results/ mirror the ORFS layout and use
    # DESIGN_NICKNAME, not DESIGN_NAME.
    nickname = b.design_nickname
    embed_dir = experiment_results(b.platform, nickname, embed_variant)

    _routed_override = os.environ.get("PDMARKS_SURVIVAL_ROUTED_VARIANT", "")
    if _routed_override:
        ppa_dir: Optional[Path] = experiment_results(b.platform, nickname, _routed_override)
        if not ppa_dir.exists():
            ppa_dir = None
    else:
        # Auto-discover: find the most recent pdmarks-all-stage* dir that has
        # 5_route.odb (written by the routing + finishing step).
        found = _find_routed_dir(b.platform, nickname, embed_dir)
        ppa_dir = found if found != embed_dir else None
        # If discovery fell back to embed_dir (no routed variant), keep ppa_dir
        # as embed_dir so post_grt/post_drt can still attempt verification when
        # the entire flow ran in a single variant.
        if ppa_dir is None and (embed_dir / "5_route.odb").exists():
            ppa_dir = embed_dir

    route_dir: Optional[Path] = (
        ppa_dir if ppa_dir and (ppa_dir / "watermark_nets.txt").exists() else None
    )
    return embed_dir, ppa_dir, route_dir


def emit(rec: dict, slug: str):
    (OUT_DIR / f"survival_{slug}.json").write_text(json.dumps(rec, indent=2))


def main():
    for b in ACTIVE_BENCHES:
        embed_dir, ppa_dir, route_dir = _resolve_dirs(b)
        variant_label = (
            f"embed:{embed_dir.name}"
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
            r_R = None
            if (b.platform == "nangate45" and stage in ("post_grt", "post_drt")
                    and route_dir is not None):
                rs = _routing_stat(route_dir, odb)
                if rs is not None:
                    zr_value = f"Z={rs.Z_R:.3f}; p={rs.p_R:.3e}"
                    pR = rs.p_R
                    r_R = 1.0 if rs.p_R <= ALPHA_R else 0.0
            slug = f"{b.platform}_{b.design}_{stage}_ZR"
            emit({"platform": b.platform, "design": b.design,
                  "variant": variant_label, "evidence": "Z_R,p_R",
                  "stage": stage, "value": zr_value,
                  "p_R": "" if pR is None else pR,
                  "r_R": "" if r_R is None else r_R,
                  "alpha_R": ALPHA_R}, slug)

            # Combined r_all.  Routing watermarking is applied during detailed
            # routing, so post-GRT r_all only combines placement and CTS.
            r_parts = []
            if X_P > 0:
                r_parts.append(r_P)
            if X_C > 0:
                r_parts.append(r_C)
            if stage == "post_drt" and r_R is not None:
                r_parts.append(r_R)
            r_all = (sum(r_parts) / len(r_parts)) if r_parts else ""
            slug = f"{b.platform}_{b.design}_{stage}_rall"
            emit({"platform": b.platform, "design": b.design,
                  "variant": variant_label, "evidence": "r_all",
                  "stage": stage, "value": r_all,
                  "r_P": r_P, "r_C": r_C,
                  "r_R": "" if r_R is None else r_R,
                  "alpha_R": ALPHA_R}, slug)

            print(f"[survival] {b.platform}/{b.design}/{stage}: "
                  f"r_P={r_P!r} r_C={r_C!r} Z_R/p_R={zr_value!r} r_all={r_all!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
