#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""
Embed Kahng-style row-parity placement watermarks using OpenROAD Python API.

Row indices are defined by sorting physical row bottoms ascending (Y-up:
index 0 is the bottom-most row). Parity 0 = EVEN index, 1 = ODD index.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from typing import Dict, List, Optional, Sequence, Set, Tuple

from openroad import Design, Tech

import watermark_common as wc


def _nearest_row_index(y: int, row_bottoms: Sequence[int]) -> int:
    best_i = 0
    best_d = 1 << 62
    for i, rb in enumerate(row_bottoms):
        d = abs(y - rb)
        if d < best_d:
            best_d = d
            best_i = i
    return best_i


def _find_swap_partner(
    block,
    w_inst,
    target_row_bottom: int,
    wm_names: Set[str],
    pitch: int,
    w: int,
    h: int,
) -> Optional[object]:
    wx, _wy = wc.inst_bottom_left(w_inst)
    tol = max(50, pitch // 8)
    best = None
    best_d = 1 << 62
    for inst in block.getInsts():
        if inst == w_inst:
            continue
        if inst.getName() in wm_names:
            continue
        if not inst.isCore() or inst.isFixed() or not inst.isPlaced():
            continue
        iw, ih = wc.inst_size(inst)
        if iw != w or ih != h:
            continue
        ix, iy = wc.inst_bottom_left(inst)
        if abs(iy - target_row_bottom) > tol:
            continue
        d = abs(ix - wx)
        if d < best_d:
            best_d = d
            best = inst
    return best


def _candidate_target_rows(ri: int, want_parity: int, n_rows: int) -> List[int]:
    """Row indices with correct parity, preferring ri±1 then ri±2."""
    out: List[int] = []
    for delta in (1, -1, 2, -2, 3, -3, 4, -4):
        t = ri + delta
        if 0 <= t < n_rows and t % 2 == want_parity:
            out.append(t)
    if not out:
        for t in range(n_rows):
            if t % 2 == want_parity:
                out.append(t)
    out.sort(key=lambda t: abs(t - ri))
    return out


def _enforce_constraints(
    block,
    chosen: Sequence[object],
    parities: Sequence[int],
    row_bottoms: Sequence[int],
    pitch: int,
) -> Tuple[int, int, int, Dict[str, Tuple[int, int]]]:
    """Enforce row-parity constraints via pair-swaps.

    Returns:
        num_violations_before, num_swaps, num_direct_moves,
        orig_positions: {cell_name -> (orig_x, orig_y)} before any move.
    """
    wm_names = {c.getName() for c in chosen}
    wm_pairs = list(zip(chosen, parities))

    # Snapshot positions BEFORE any moves.
    orig_positions: Dict[str, Tuple[int, int]] = {
        inst.getName(): wc.inst_bottom_left(inst) for inst, _ in wm_pairs
    }

    def row_idx(inst):
        return _nearest_row_index(wc.inst_bottom_left(inst)[1], row_bottoms)

    before_viol = sum(1 for inst, par in wm_pairs if row_idx(inst) % 2 != par)

    swaps = 0
    directs = 0
    n_rows = len(row_bottoms)

    for inst, par in wm_pairs:
        ri = row_idx(inst)
        if ri % 2 == par:
            continue
        targets = _candidate_target_rows(ri, par, n_rows)
        w_w, w_h = wc.inst_size(inst)
        lx, ly = wc.inst_bottom_left(inst)
        moved = False
        for t_idx in targets:
            target_y = row_bottoms[t_idx]
            partner = _find_swap_partner(
                block, inst, target_y, wm_names, pitch, w_w, w_h
            )
            if partner is not None:
                px, py = wc.inst_bottom_left(partner)
                inst.setLocation(px, py)
                partner.setLocation(lx, ly)
                swaps += 1
                moved = True
                break
        if not moved:
            t_idx = targets[0]
            target_y = row_bottoms[t_idx]
            inst.setLocation(lx, target_y)
            directs += 1

    return before_viol, swaps, directs, orig_positions


def _write_cell_list(
    path: str,
    chosen: Sequence[object],
    parities: Sequence[int],
    orig_positions: Dict[str, Tuple[int, int]],
    row_bottoms: Sequence[int],
) -> None:
    """Write watermark cell list CSV.

    Columns:
        cell_name     - instance name
        master        - master cell name
        required_parity  - 0 (EVEN row index) or 1 (ODD row index)
        orig_x, orig_y   - bottom-left position before watermarking (DBU)
        wm_x, wm_y       - bottom-left position after watermarking + legalization (DBU)
        row_idx_before   - row index before watermarking
        row_idx_after    - row index after watermarking + legalization
        satisfied        - True if row_idx_after parity matches required_parity
    """
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "cell_name", "master",
            "required_parity",
            "orig_x", "orig_y",
            "wm_x", "wm_y",
            "row_idx_before", "row_idx_after",
            "satisfied",
        ])
        for inst, par in zip(chosen, parities):
            name = inst.getName()
            master = inst.getMaster().getName()
            ox, oy = orig_positions.get(name, (0, 0))
            wx, wy = wc.inst_bottom_left(inst)
            ri_before = _nearest_row_index(oy, row_bottoms)
            ri_after = _nearest_row_index(wy, row_bottoms)
            satisfied = ri_after % 2 == par
            writer.writerow([
                name, master,
                par,
                ox, oy,
                wx, wy,
                ri_before, ri_after,
                satisfied,
            ])


def main() -> int:
    p = argparse.ArgumentParser(description="Embed row-parity placement watermark")
    p.add_argument(
        "--input",
        default=os.environ.get("WM_INPUT"),
        help="Input .odb (post detailed placement). Env: WM_INPUT",
    )
    p.add_argument(
        "--output-odb",
        default=os.environ.get("WM_OUTPUT_ODB"),
        help="Output watermarked .odb. Env: WM_OUTPUT_ODB",
    )
    p.add_argument(
        "--output-def",
        default=os.environ.get("WM_OUTPUT_DEF"),
        help="Optional output DEF. Env: WM_OUTPUT_DEF",
    )
    p.add_argument(
        "--output-cell-list",
        default=os.environ.get("WM_OUTPUT_CELL_LIST"),
        help="Optional output CSV of watermark cells. Env: WM_OUTPUT_CELL_LIST",
    )
    p.add_argument("--message", default=os.environ.get("WM_MESSAGE", ""))
    p.add_argument("--key", default=os.environ.get("WM_KEY", ""))
    p.add_argument(
        "--num-cells",
        type=int,
        default=int(os.environ.get("WM_NUM_CELLS", "100")),
    )
    p.add_argument(
        "--max-disp-micron",
        type=float,
        nargs=2,
        metavar=("X", "Y"),
        default=None,
        help="Max displacement for incremental DPL (microns). Default 50 50.",
    )
    args = p.parse_args(wc.argv_after_openroad_driver())

    if not args.input or not args.output_odb:
        p.error("--input and --output-odb (or WM_INPUT / WM_OUTPUT_ODB) are required")
    if not args.message or not args.key:
        p.error("--message and --key (or WM_MESSAGE / WM_KEY) are required")

    tech = Tech()
    design = Design(tech)
    design.readDb(args.input)
    block = design.getBlock()
    if block is None:
        print("No block in database", file=sys.stderr)
        return 1

    rows = list(block.getRows())
    if not rows:
        print("No rows in design", file=sys.stderr)
        return 1
    site = rows[0].getSite()
    site_h = site.getHeight()
    row_bottoms = wc.sorted_row_bottoms(block)
    pitch = wc.infer_row_pitch(row_bottoms, site_h)

    cells = wc.collect_movable_core_cells(block)
    cells = [c for c in cells if wc.inst_size(c)[1] == site_h]
    chosen, parities, _rng = wc.watermark_selection(
        args.key, args.message, cells, args.num_cells
    )

    before_v, swaps, directs, orig_positions = _enforce_constraints(
        block, chosen, parities, row_bottoms, pitch
    )

    dpl = design.getOpendp()
    if args.max_disp_micron is None:
        mx = float(os.environ.get("WM_MAX_DISP_X", "50"))
        my = float(os.environ.get("WM_MAX_DISP_Y", "50"))
    else:
        mx, my = float(args.max_disp_micron[0]), float(args.max_disp_micron[1])
    max_disp_x = int(design.micronToDBU(mx) / site.getWidth())
    max_disp_y = int(design.micronToDBU(my) / site.getHeight())
    dpl.detailedPlacement(max_disp_x, max_disp_y, "", True)
    dpl.checkPlacement(False)

    # Post-legalization stats.
    sat = 0
    for inst, par in zip(chosen, parities):
        ri = _nearest_row_index(wc.inst_bottom_left(inst)[1], row_bottoms)
        if ri % 2 == par:
            sat += 1
    fail = len(chosen) - sat
    pc = wc.binomial_pc(len(chosen), fail)

    design.writeDb(args.output_odb)
    if args.output_def:
        design.writeDef(args.output_def)

    if args.output_cell_list:
        _write_cell_list(
            args.output_cell_list, chosen, parities, orig_positions, row_bottoms
        )
        print(f"[watermark_embed] cell list written -> {args.output_cell_list}")

    print(
        f"[watermark_embed] constraints={len(chosen)} pre_swap_viol={before_v} "
        f"swaps={swaps} direct_y={directs} post_dpl_sat={sat} fail={fail} Pc<={pc:.3e}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
