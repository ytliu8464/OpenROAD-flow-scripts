#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Cell-scattering baseline (Cai et al. ISIC'07) -- embed phase.

Algorithm
---------
1. Load 3_place.odb (post-detailed-placement).
2. K = capacity_for(platform, design, variant) from PDMarks P-only.
3. Eligible cells: movable, non-macro, non-fixed, single-row-height, not a
   clock/hold buffer.
4. Select K cells deterministically: HMAC-SHA256(seed_placement,
   b"cellscatter\0" + inst_name) -> u32; keep top-K by ascending score.
5. Target column parity per cell: HMAC(..., b"cellscatter\0bit\0" + name)[0] & 1.
6. If current (origin_x - core_x_min) / site_w parity != target, shift by
   +1 or -1 site (pick direction that keeps cell inside core bounds).
7. Run detailed_placement to re-legalize.
8. Write cell_scattering_embed.csv and 3_place_cellscatter.odb.

Run as:
  openroad -python -exit embed.py \\
      --odb /path/to/3_place.odb \\
      --seed-hex /path/to/seed_placement.hex \\
      --platform nangate45 --design aes --variant watermarking-test1 \\
      [--k INT]
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from pathlib import Path
from typing import List

# ------------------------------------------------------------------
# Bootstrap: ensure baselines/ and experiments/ are on sys.path so we
# can import _common regardless of where openroad spawns us.
# ------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))          # baselines/
sys.path.insert(0, str(_HERE.parent.parent))   # experiments/

from _common import (  # noqa: E402
    capacity_for,
    load_seed_hex,
    select_top_k,
    target_bit,
    pc_stage,
    FLOW_HOME,
)

_T0 = time.time()


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')} +{time.time()-_T0:6.1f}s] [cellscatter:embed] {msg}", flush=True)


# ---------------------------------------------------------------------------
# ODB helpers
# ---------------------------------------------------------------------------

def _is_buffer_or_clock(inst) -> bool:
    """Heuristic: reject obvious hold/clock buffers by master name."""
    name = inst.getMaster().getName().upper()
    return any(t in name for t in ("CLKBUF", "CLKINV", "TIEH", "TIEL", "FILL"))


def _is_eligible(inst) -> bool:
    from odb import dbPlacementStatus
    status = inst.getPlacementStatus()
    if status == dbPlacementStatus.FIRM or status == dbPlacementStatus.LOCKED:
        return False
    if inst.isFixed():
        return False
    if not inst.isPlaced():
        return False
    master = inst.getMaster()
    if master.isBlock():
        return False
    if _is_buffer_or_clock(inst):
        return False
    return True


def _core_bbox(block):
    bb = block.getCoreArea()
    return bb.xMin(), bb.yMin(), bb.xMax(), bb.yMax()


def _site_width(block) -> int:
    for row in block.getRows():
        site = row.getSite()
        if site is not None:
            return site.getWidth()
    raise RuntimeError("no rows found in block")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _parse_args(argv: List[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Cell-scattering baseline embedder")
    ap.add_argument("--odb",       required=True)
    ap.add_argument("--out-odb",   default="")
    ap.add_argument("--seed-hex",  required=True, help="path to seed_placement.hex")
    ap.add_argument("--platform",  required=True)
    ap.add_argument("--design",    required=True)
    ap.add_argument("--variant",   required=True)
    ap.add_argument("--k",         type=int, default=0)
    ap.add_argument("--out-csv",   default="")
    return ap.parse_args(argv)


def main(argv: List[str]) -> None:
    args = _parse_args(argv)

    # ----- load design -----
    from openroad import Design, Tech
    tech = Tech()
    design = Design(tech)
    _log(f"loading {args.odb}")
    design.readDb(args.odb)
    block = design.getBlock()

    # ----- parameters -----
    seed = load_seed_hex(args.seed_hex)
    K = args.k if args.k > 0 else capacity_for(args.platform, args.design, args.variant)
    _log(f"K={K}  seed={seed[:4].hex()}...")

    site_w = _site_width(block)
    cx_min, _, cx_max, _ = _core_bbox(block)
    _log(f"site_w={site_w}  core_x=[{cx_min}, {cx_max}]")

    # ----- collect eligible instances -----
    eligible: List[str] = []
    for inst in block.getInsts():
        if _is_eligible(inst):
            eligible.append(inst.getName())

    _log(f"eligible cells: {len(eligible)}")
    if len(eligible) < K:
        _log(f"WARNING: only {len(eligible)} eligible cells; reducing K to {len(eligible)}")
        K = len(eligible)

    selected = select_top_k(seed, b"cellscatter", eligible, K)
    _log(f"selected {len(selected)} cells")

    # ----- apply parity shifts -----
    DOMAIN_BIT = b"cellscatter"
    rows: List[dict] = []
    miss = 0

    name_to_inst = {inst.getName(): inst for inst in block.getInsts()}

    for inst_name in sorted(selected):
        inst = name_to_inst.get(inst_name)
        if inst is None:
            continue
        tbit = target_bit(seed, DOMAIN_BIT, inst_name)
        ox, oy = inst.getOrigin()
        col_before = (ox - cx_min) // site_w
        cur_bit = col_before & 1

        satisfied = False
        skipped = ""
        new_x = ox

        if cur_bit == tbit:
            satisfied = True
        else:
            # Try +1 site, then -1 site
            for delta in (site_w, -site_w):
                candidate = ox + delta
                if cx_min <= candidate and (candidate + inst.getMaster().getWidth()) <= cx_max:
                    new_x = candidate
                    satisfied = True
                    break
            if not satisfied:
                skipped = "no_room"
                miss += 1

        if new_x != ox:
            inst.setOrigin(new_x, oy)

        col_after = (new_x - cx_min) // site_w
        rows.append({
            "inst":         inst_name,
            "target_bit":   tbit,
            "before_col":   col_before,
            "after_col":    col_after,
            "satisfied":    satisfied,
            "skipped":      skipped,
        })

    _log(f"satisfied={len(rows)-miss}/{K}  miss={miss}")

    # ----- legalize -----
    _log("running detailed_placement to legalize shifts...")
    from openroad import Design as _D
    try:
        design.getOpendp().detailedPlacement(0, 0, "", False)
    except Exception as e:
        _log(f"WARNING: detailed_placement raised: {e}")

    # ----- re-read column parities after legalization -----
    missed_post = 0
    for row in rows:
        if not row["satisfied"]:
            missed_post += 1
            continue
        inst = name_to_inst.get(row["inst"])
        if inst is None:
            continue
        ox_post, _ = inst.getOrigin()
        col_post = (ox_post - cx_min) // site_w
        row["after_col"] = col_post
        if (col_post & 1) != row["target_bit"]:
            row["satisfied"] = False
            missed_post += 1

    x = K - missed_post
    pc = pc_stage(K, K - x, 0.5)
    _log(f"post-legalization: accepted={x}/{K}  Pc={pc:.2e}")

    # ----- write CSV -----
    out_dir = Path(args.odb).parent
    csv_path = args.out_csv or str(out_dir / "cell_scattering_embed.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["inst", "target_bit", "before_col", "after_col", "satisfied", "skipped"])
        w.writeheader()
        w.writerows(rows)
    _log(f"embed CSV -> {csv_path}")

    # ----- write ODB -----
    odb_out = args.out_odb or str(out_dir / "3_place_cellscatter.odb")
    design.writeDb(odb_out)
    _log(f"watermarked ODB -> {odb_out}")

    # summary line for phase1_capacity.py
    print(f"CELLSCATTER_EMBED: K={K} accepted={x} Pc={pc:.4e}", flush=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Strip openroad driver args: openroad -python -exit embed.py [-- args]
    argv = sys.argv[1:]
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    main(argv)
