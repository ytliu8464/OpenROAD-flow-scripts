#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Embed site-parity (mod-5 along X) placement watermarks using OpenROAD Python.

Flow
----
1. Load post-DP ODB.
2. Collect movable single-row-height core cells.
3. Filter out timing-critical cells (worst pin slack < WM_SLACK_THRESHOLD_NS).
4. Build an NX x NY tile grid over the core bbox.
5. For each tile, pseudo-randomly (HMAC(seed_placement, tile)) sample k% of its
   cells, and for each chosen cell derive ``target_residue`` in {0..4}.
6. Enforce each target via row-local swap with a same-master cell having the
   correct current residue, or nudge along X by the signed mod-5 delta.
7. Run incremental detailed placement; report satisfied count and Pc = binom
   tail with p = 1/5.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from typing import Dict, List, Optional, Sequence, Set, Tuple

from openroad import Design, Tech

import watermark_common as wc


def _build_row_xmin_by_y(block) -> Dict[int, int]:
    """Map each row's yMin -> minimum xMin across rows at that Y."""
    out: Dict[int, int] = {}
    for r in block.getRows():
        y = r.getBBox().yMin()
        x = r.getBBox().xMin()
        if y not in out or x < out[y]:
            out[y] = x
    return out


def _snap_row_y(y: int, row_bottoms: Sequence[int]) -> int:
    best = row_bottoms[0]
    bd = abs(y - best)
    for rb in row_bottoms[1:]:
        d = abs(y - rb)
        if d < bd:
            best = rb
            bd = d
    return best


def _find_swap_partner_for_residue(
    row_to_insts: Dict[int, List[object]],
    w_inst,
    row_y: int,
    wm_names: Set[str],
    w_width: int,
    w_height: int,
    target_residue: int,
    row_xmin: int,
    site_width: int,
) -> Optional[object]:
    """Find a non-watermarked cell in the same row whose current residue ==
    target_residue, of identical master width/height. Prefer minimal-x-delta
    to limit HPWL impact."""
    wx, _ = wc.inst_bottom_left(w_inst)
    candidates = row_to_insts.get(row_y, [])
    best = None
    best_d = 1 << 62
    for inst in candidates:
        if inst is w_inst:
            continue
        if inst.getName() in wm_names:
            continue
        try:
            if inst.isFixed() or not inst.isPlaced() or not inst.isCore():
                continue
        except Exception:
            pass
        iw, ih = wc.inst_size(inst)
        if iw != w_width or ih != w_height:
            continue
        res = wc.residue_of(inst, row_xmin, site_width)
        if res != target_residue:
            continue
        ix, _ = wc.inst_bottom_left(inst)
        d = abs(ix - wx)
        if d < best_d:
            best = inst
            best_d = d
    return best


def _enforce_constraints(
    block,
    chosen: Sequence[object],
    targets: Sequence[int],
    tile_ids: Sequence[Tuple[int, int]],
    rows_map: Dict[int, List[object]],
    row_xmin_by_y: Dict[int, int],
    row_bottoms: Sequence[int],
    site_width: int,
) -> Tuple[int, int, int, Dict[str, Tuple[int, int]]]:
    """Try per-chosen-cell: swap within row with same-master cell whose current
    residue matches target; else nudge X by signed mod-5 delta. Returns
    (before_violations, swaps, directs, orig_positions)."""
    wm_names = {c.getName() for c in chosen}
    orig_positions: Dict[str, Tuple[int, int]] = {}

    # Build row_y -> movable insts (once). Cell positions may change during
    # enforcement; swap_partner search tolerates that because it compares
    # current positions on the fly.
    row_to_insts: Dict[int, List[object]] = {y: [] for y in row_bottoms}
    for inst in block.getInsts():
        try:
            if not inst.isCore() or inst.isFixed() or not inst.isPlaced():
                continue
        except Exception:
            continue
        _, y = wc.inst_bottom_left(inst)
        ry = _snap_row_y(y, row_bottoms)
        row_to_insts.setdefault(ry, []).append(inst)

    before_viol = 0
    swaps = 0
    directs = 0

    for inst, tgt in zip(chosen, targets):
        x, y = wc.inst_bottom_left(inst)
        orig_positions[inst.getName()] = (x, y)
        ry = _snap_row_y(y, row_bottoms)
        rxmin = row_xmin_by_y.get(ry, 0)
        cur = wc.residue_of(inst, rxmin, site_width)
        if cur == tgt:
            continue
        before_viol += 1

        w_w, w_h = wc.inst_size(inst)
        partner = _find_swap_partner_for_residue(
            row_to_insts, inst, ry, wm_names,
            w_w, w_h, tgt, rxmin, site_width,
        )
        if partner is not None:
            px, py = wc.inst_bottom_left(partner)
            inst.setLocation(px, py)
            partner.setLocation(x, y)
            swaps += 1
            continue

        d_sites = wc.signed_delta_mod(tgt, cur)
        new_x = x + d_sites * site_width
        inst.setLocation(new_x, y)
        directs += 1

    return before_viol, swaps, directs, orig_positions


def _write_cell_list(
    path: str,
    chosen: Sequence[object],
    targets: Sequence[int],
    tile_ids: Sequence[Tuple[int, int]],
    orig_positions: Dict[str, Tuple[int, int]],
    row_xmin_by_y: Dict[int, int],
    row_bottoms: Sequence[int],
    site_width: int,
) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "cell_name", "master",
            "tile_tx", "tile_ty",
            "target_residue",
            "orig_x", "orig_y",
            "wm_x", "wm_y",
            "site_col_before", "site_col_after",
            "residue_before", "residue_after",
            "satisfied",
        ])
        for inst, tgt, (tx, ty) in zip(chosen, targets, tile_ids):
            name = inst.getName()
            master = inst.getMaster().getName()
            ox, oy = orig_positions.get(name, (0, 0))
            wx, wy = wc.inst_bottom_left(inst)
            ry_before = _snap_row_y(oy, row_bottoms)
            rxmin_before = row_xmin_by_y.get(ry_before, 0)
            ry_after = _snap_row_y(wy, row_bottoms)
            rxmin_after = row_xmin_by_y.get(ry_after, 0)
            col_before = int(round((ox - rxmin_before) / float(site_width)))
            col_after = int(round((wx - rxmin_after) / float(site_width)))
            res_before = col_before % wc.MODULUS
            res_after = col_after % wc.MODULUS
            ok = res_after == tgt
            w.writerow([
                name, master,
                tx, ty,
                tgt,
                ox, oy,
                wx, wy,
                col_before, col_after,
                res_before, res_after,
                ok,
            ])


def main() -> int:
    p = argparse.ArgumentParser(description="Embed site-parity placement watermark")
    p.add_argument("--input", default=os.environ.get("WM_INPUT"))
    p.add_argument("--output-odb", default=os.environ.get("WM_OUTPUT_ODB"))
    p.add_argument("--output-def", default=os.environ.get("WM_OUTPUT_DEF"))
    p.add_argument(
        "--output-cell-list", default=os.environ.get("WM_OUTPUT_CELL_LIST")
    )
    p.add_argument(
        "--seed-hex", default=os.environ.get("WM_SEED_HEX"),
        help="Path to seed_placement.hex produced by gen_key/",
    )
    p.add_argument(
        "--grid-nx", type=int,
        default=int(os.environ.get("WM_GRID_NX", "8")),
    )
    p.add_argument(
        "--grid-ny", type=int,
        default=int(os.environ.get("WM_GRID_NY", "8")),
    )
    p.add_argument(
        "--k-percent", type=float,
        default=float(os.environ.get("WM_K_PERCENT", "5")),
    )
    p.add_argument(
        "--slack-threshold-ns", type=float,
        default=float(os.environ.get("WM_SLACK_THRESHOLD_NS", "0.1")),
    )
    p.add_argument(
        "--sdc", default=os.environ.get("WM_SDC", ""),
    )
    p.add_argument(
        "--max-disp-micron", type=float, nargs=2, metavar=("X", "Y"),
        default=None,
    )
    p.add_argument(
        "--message", default=os.environ.get("WM_MESSAGE", ""),
        help="Optional human-readable tag (not used in selection).",
    )
    args = p.parse_args(wc.argv_after_openroad_driver())

    if not args.input or not args.output_odb:
        p.error("--input and --output-odb (or WM_INPUT / WM_OUTPUT_ODB) required")
    if not args.seed_hex:
        p.error("--seed-hex (or WM_SEED_HEX) is required")

    seed = wc.load_seed_hex(args.seed_hex)
    print(f"[wm_embed] seed  <- {args.seed_hex}  (len={len(seed)} bytes)")
    if args.message:
        print(f"[wm_embed] note   : message tag = {args.message!r}")

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
    sw = site.getWidth()
    sh = site.getHeight()
    row_bottoms = wc.sorted_row_bottoms(block)
    rows_map = wc.rows_by_bottom(block)
    row_xmin_by_y = _build_row_xmin_by_y(block)

    cells_all = wc.collect_movable_core_cells(block)
    cells = [c for c in cells_all if wc.inst_size(c)[1] == sh]
    print(
        f"[wm_embed] movable core cells total={len(cells_all)} "
        f"single-row-height={len(cells)}"
    )

    kept, sta_info = wc.filter_by_slack(
        design, cells,
        slack_threshold_s=args.slack_threshold_ns * 1e-9,
        sdc_path=(args.sdc or None),
    )
    print(
        f"[wm_embed] slack filter: mode={sta_info.get('mode')} "
        f"kept={sta_info.get('kept')} dropped={sta_info.get('dropped')}"
    )
    for k, v in sta_info.items():
        if k.endswith("error"):
            print(f"[wm_embed]   {k}: {v}")

    bbox, tw, th = wc.make_tile_grid(block, args.grid_nx, args.grid_ny)
    nx = max(1, int(args.grid_nx))
    ny = max(1, int(args.grid_ny))
    print(
        f"[wm_embed] tile grid {nx}x{ny} over core bbox "
        f"({bbox[0]},{bbox[1]})-({bbox[2]},{bbox[3]}); tile={tw:.1f}x{th:.1f} DBU"
    )

    by_tile: Dict[Tuple[int, int], List[object]] = {}
    for inst in kept:
        tid = wc.tile_of(inst, bbox, tw, th, nx, ny)
        by_tile.setdefault(tid, []).append(inst)

    k_frac = max(0.0, args.k_percent / 100.0)
    chosen: List[object] = []
    targets: List[int] = []
    tile_ids: List[Tuple[int, int]] = []

    for tid in sorted(by_tile.keys()):
        pool = sorted(by_tile[tid], key=lambda c: c.getName())
        n_pick = int(math.ceil(len(pool) * k_frac)) if pool else 0
        if n_pick <= 0:
            continue
        n_pick = min(n_pick, len(pool))
        rng = wc.tile_rng(seed, tid)
        picks = rng.sample(pool, n_pick)
        for inst in picks:
            chosen.append(inst)
            targets.append(wc.target_residue(seed, tid, inst.getName()))
            tile_ids.append(tid)

    print(
        f"[wm_embed] candidates chosen={len(chosen)} across "
        f"{len(by_tile)} non-empty tiles (k%={args.k_percent})"
    )
    if not chosen:
        print("[wm_embed] no cells chosen; nothing to do", file=sys.stderr)
        return 1

    before_viol, swaps, directs, orig_positions = _enforce_constraints(
        block, chosen, targets, tile_ids,
        rows_map, row_xmin_by_y, row_bottoms, sw,
    )

    # Incremental DPL.
    dpl = design.getOpendp()
    if args.max_disp_micron is None:
        mx = float(os.environ.get("WM_MAX_DISP_X", "50"))
        my = float(os.environ.get("WM_MAX_DISP_Y", "50"))
    else:
        mx, my = float(args.max_disp_micron[0]), float(args.max_disp_micron[1])
    max_disp_x = int(design.micronToDBU(mx) / sw)
    max_disp_y = int(design.micronToDBU(my) / sh)
    dpl.detailedPlacement(max_disp_x, max_disp_y, "", True)
    try:
        dpl.checkPlacement(False)
    except Exception as e:
        print(f"[wm_embed] checkPlacement warning: {e}")

    sat = 0
    for inst, tgt in zip(chosen, targets):
        _, y = wc.inst_bottom_left(inst)
        ry = _snap_row_y(y, row_bottoms)
        rxmin = row_xmin_by_y.get(ry, 0)
        if wc.residue_of(inst, rxmin, sw) == tgt:
            sat += 1
    fail = len(chosen) - sat
    pc = wc.binomial_pc(len(chosen), fail)

    design.writeDb(args.output_odb)
    if args.output_def:
        design.writeDef(args.output_def)

    if args.output_cell_list:
        _write_cell_list(
            args.output_cell_list, chosen, targets, tile_ids,
            orig_positions, row_xmin_by_y, row_bottoms, sw,
        )
        print(f"[wm_embed] cell list -> {args.output_cell_list}")

    print(
        f"[wm_embed] constraints={len(chosen)} pre_viol={before_viol} "
        f"swaps={swaps} direct_x={directs} post_dpl_sat={sat} fail={fail} "
        f"Pc(p=1/{wc.MODULUS})<={pc:.3e}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
