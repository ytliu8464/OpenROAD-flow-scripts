#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Kahng et al. row-parity placement watermark (KahngMMP98/KahngLMM01) -- embed.

The Kahng paper proposes two postprocessing watermarks: row-parity for
placement and wrong-way limit for routing. The placement variant ("Row
Parity") is the form cited as the Kahng baseline by ICMarks and AutoMarks,
and is what populates the single 'Kahng' row in the PDMarks PPA tables.

Algorithm
---------
1. Load 3_place.odb (post-detailed-placement).
2. K = capacity_for(platform, design, variant), matched to PDMarks P-only.
3. Eligible cells: movable, non-macro, non-fixed, single-row-height, not a
   clock/hold buffer (same gate as cell_scattering for an apples-to-apples
   comparison with PDMarks).
4. Select K cells deterministically: HMAC-SHA256(seed_placement,
   b"kahng_row\0" + inst_name) -> u32; keep top-K by ascending score.
5. Target row-index parity per cell: HMAC(..., b"kahng_row\0bit\0" + name)[0] & 1.
6. For each selected cell whose current row index has the wrong parity,
   shift its y-origin by +1 row pitch (then -1, +3, -3, ...) until it
   lands on a row with the target parity. Original x is preserved.
7. Run detailed_placement to legalize. Re-read row parity to confirm.
8. Write kahng_embed.csv and 3_place_kahng.odb.

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
import sys
import time
from pathlib import Path
from typing import List

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))          # baselines/
sys.path.insert(0, str(_HERE.parent.parent))   # experiments/

from _common import (  # noqa: E402
    capacity_for,
    load_seed_hex,
    select_top_k,
    target_bit,
    pc_stage,
)

_T0 = time.time()


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')} +{time.time()-_T0:6.1f}s] [kahng:embed] {msg}", flush=True)


def _is_buffer_or_clock(inst) -> bool:
    name = inst.getMaster().getName().upper()
    return any(t in name for t in ("CLKBUF", "CLKINV", "TIEH", "TIEL", "FILL"))


def _is_eligible(inst) -> bool:
    # FIRM/LOCKED status is already covered by isFixed() in this OpenROAD
    # build (odb does not export dbPlacementStatus to Python).
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


def _sorted_row_bottoms(block) -> List[int]:
    rows = list(block.getRows())
    if not rows:
        raise RuntimeError("no rows in block")
    return sorted({r.getBBox().yMin() for r in rows})


def _row_index(y: int, row_bottoms: List[int]) -> int:
    """Return the index (in y-sorted order) of the row whose bottom equals y.
    For values that don't fall on a row bottom (legalization slop), snap to
    the nearest row."""
    # Binary search would be marginally faster; linear is fine at ~10^3 rows.
    best = 0
    best_d = abs(row_bottoms[0] - y)
    for i, rb in enumerate(row_bottoms[1:], start=1):
        d = abs(rb - y)
        if d < best_d:
            best = i
            best_d = d
    return best


def _parse_args(argv: List[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Kahng row-parity embedder")
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

    from openroad import Design, Tech
    tech = Tech()
    design = Design(tech)
    _log(f"loading {args.odb}")
    design.readDb(args.odb)
    block = design.getBlock()

    seed = load_seed_hex(args.seed_hex)
    K = args.k if args.k > 0 else capacity_for(args.platform, args.design, args.variant)
    _log(f"K={K}  seed={seed[:4].hex()}...")

    row_bottoms = _sorted_row_bottoms(block)
    n_rows = len(row_bottoms)
    _log(f"rows: {n_rows}  y in [{row_bottoms[0]}, {row_bottoms[-1]}]")
    row_pitch = row_bottoms[1] - row_bottoms[0] if n_rows >= 2 else 0
    if row_pitch <= 0:
        raise RuntimeError("could not infer row pitch")

    eligible: List[str] = []
    for inst in block.getInsts():
        if _is_eligible(inst):
            eligible.append(inst.getName())
    _log(f"eligible cells: {len(eligible)}")
    if len(eligible) < K:
        _log(f"WARNING: only {len(eligible)} eligible cells; reducing K to {len(eligible)}")
        K = len(eligible)

    selected = select_top_k(seed, b"kahng_row", eligible, K)
    _log(f"selected {len(selected)} cells")

    DOMAIN_BIT = b"kahng_row"
    rows: List[dict] = []
    miss = 0

    name_to_inst = {inst.getName(): inst for inst in block.getInsts()}

    for inst_name in sorted(selected):
        inst = name_to_inst.get(inst_name)
        if inst is None:
            continue
        tbit = target_bit(seed, DOMAIN_BIT, inst_name)
        ox, oy = inst.getOrigin()
        ridx_before = _row_index(oy, row_bottoms)
        cur_bit = ridx_before & 1

        satisfied = False
        skipped = ""
        new_y = oy
        new_ridx = ridx_before

        if cur_bit == tbit:
            satisfied = True
        else:
            # Try +1 row, -1 row, +3, -3, ... (parity flips with every odd step).
            for step in (1, -1, 3, -3, 5, -5):
                cand = ridx_before + step
                if 0 <= cand < n_rows:
                    new_ridx = cand
                    new_y = row_bottoms[cand]
                    satisfied = True
                    break
            if not satisfied:
                skipped = "no_room"
                miss += 1

        if new_y != oy:
            inst.setOrigin(ox, new_y)

        rows.append({
            "inst":         inst_name,
            "target_bit":   tbit,
            "before_row":   ridx_before,
            "after_row":    new_ridx,
            "satisfied":    satisfied,
            "skipped":      skipped,
        })

    _log(f"pre-legalization: satisfied={len(rows)-miss}/{K}  miss={miss}")

    _log("running detailed_placement to legalize row shifts...")
    try:
        design.getOpendp().detailedPlacement(0, 0, "", False)
    except Exception as e:
        _log(f"WARNING: detailed_placement raised: {e}")

    # Re-read row parities after legalization
    missed_post = 0
    for row in rows:
        if not row["satisfied"]:
            missed_post += 1
            continue
        inst = name_to_inst.get(row["inst"])
        if inst is None:
            row["satisfied"] = False
            missed_post += 1
            continue
        _, oy_post = inst.getOrigin()
        ridx_post = _row_index(oy_post, row_bottoms)
        row["after_row"] = ridx_post
        if (ridx_post & 1) != row["target_bit"]:
            row["satisfied"] = False
            missed_post += 1

    x = K - missed_post
    pc = pc_stage(K, K - x, 0.5)
    _log(f"post-legalization: accepted={x}/{K}  Pc={pc:.2e}")

    out_dir = Path(args.odb).parent
    csv_path = args.out_csv or str(out_dir / "kahng_embed.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["inst", "target_bit", "before_row", "after_row", "satisfied", "skipped"])
        w.writeheader()
        w.writerows(rows)
    _log(f"embed CSV -> {csv_path}")

    odb_out = args.out_odb or str(out_dir / "3_place_kahng.odb")
    design.writeDb(odb_out)
    _log(f"watermarked ODB -> {odb_out}")

    print(f"KAHNG_EMBED: K={K} accepted={x} Pc={pc:.4e}", flush=True)


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    main(argv)
