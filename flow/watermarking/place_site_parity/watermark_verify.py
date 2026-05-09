#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Verify site-parity placement watermark.

Ground truth is preferentially read from the embed CSV (``WM_CELL_LIST``)
so that verification does not depend on re-running STA/tile selection on a
possibly different netlist. If no CSV is given, verification falls back to
re-running the full selection (requires matching --seed-hex / grid / k%).
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple

from openroad import Design, Tech

import watermark_common as wc


def _read_cell_list_csv(path: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("cell_name", "").strip():
                out.append(row)
    if not out:
        raise ValueError(f"No watermark cells found in {path}")
    return out


def _build_row_xmin_by_y(block) -> Dict[int, int]:
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


def _write_cell_list(
    path: str,
    cells_info: Sequence[Tuple[object, int, Tuple[int, int]]],
    row_xmin_by_y: Dict[int, int],
    row_bottoms: Sequence[int],
    site_width: int,
) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "cell_name", "master", "tile_tx", "tile_ty", "target_residue",
            "x", "y", "site_col", "residue", "satisfied",
        ])
        for inst, tgt, (tx, ty) in cells_info:
            name = inst.getName()
            master = inst.getMaster().getName()
            x, y = wc.inst_bottom_left(inst)
            ry = _snap_row_y(y, row_bottoms)
            rxmin = row_xmin_by_y.get(ry, 0)
            col = int(round((x - rxmin) / float(site_width)))
            res = col % wc.MODULUS
            w.writerow([name, master, tx, ty, tgt, x, y, col, res, res == tgt])


def main() -> int:
    p = argparse.ArgumentParser(description="Verify site-parity placement watermark")
    p.add_argument("--input", default=os.environ.get("WM_VERIFY_INPUT"))
    p.add_argument(
        "--seed-hex", default=os.environ.get("WM_SEED_HEX"),
        help="Path to seed_placement.hex (required only when --cell-list is absent)",
    )
    p.add_argument(
        "--cell-list", default=os.environ.get("WM_CELL_LIST"),
        help="Embed CSV (ground truth). If given, --seed-hex is not required.",
    )
    p.add_argument("--grid-nx", type=int, default=int(os.environ.get("WM_GRID_NX", "8")))
    p.add_argument("--grid-ny", type=int, default=int(os.environ.get("WM_GRID_NY", "8")))
    p.add_argument(
        "--k-percent", type=float,
        default=float(os.environ.get("WM_K_PERCENT", "5")),
    )
    p.add_argument(
        "--slack-threshold-ns", type=float,
        default=float(os.environ.get("WM_SLACK_THRESHOLD_NS", "0.1")),
    )
    p.add_argument("--sdc", default=os.environ.get("WM_SDC", ""))
    p.add_argument(
        "--output-cell-list", default=os.environ.get("WM_VERIFY_CELL_LIST"),
    )
    args = p.parse_args(wc.argv_after_openroad_driver())

    if not args.input:
        p.error("--input (or WM_VERIFY_INPUT) is required")
    if not args.cell_list and not args.seed_hex:
        p.error("provide --cell-list (WM_CELL_LIST) or --seed-hex (WM_SEED_HEX)")

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
    row_xmin_by_y = _build_row_xmin_by_y(block)

    cells_info: List[Tuple[object, int, Tuple[int, int]]] = []
    missing: List[str] = []

    if args.cell_list:
        print(f"[wm_verify] cell-list <- {args.cell_list}")
        rows_csv = _read_cell_list_csv(args.cell_list)
        inst_map = {inst.getName(): inst for inst in block.getInsts()}
        for r in rows_csv:
            name = r["cell_name"].strip()
            inst = inst_map.get(name)
            try:
                tgt = int(r.get("target_residue", r.get("required_parity", "0")).strip())
            except (ValueError, AttributeError):
                tgt = 0
            try:
                tx = int(r.get("tile_tx", "0"))
                ty = int(r.get("tile_ty", "0"))
            except ValueError:
                tx, ty = 0, 0
            if inst is None:
                missing.append(name)
                continue
            cells_info.append((inst, tgt, (tx, ty)))
    else:
        seed = wc.load_seed_hex(args.seed_hex)
        print(f"[wm_verify] seed <- {args.seed_hex} (len={len(seed)} bytes)")
        cells_all = wc.collect_movable_core_cells(block)
        cells = [c for c in cells_all if wc.inst_size(c)[1] == sh]
        kept, sta_info = wc.filter_by_slack(
            design, cells, args.slack_threshold_ns * 1e-9,
            sdc_path=(args.sdc or None),
        )
        print(
            f"[wm_verify] slack filter: mode={sta_info.get('mode')} "
            f"kept={sta_info.get('kept')}"
        )
        bbox, tw, th = wc.make_tile_grid(block, args.grid_nx, args.grid_ny)
        nx = max(1, int(args.grid_nx))
        ny = max(1, int(args.grid_ny))
        by_tile: Dict[Tuple[int, int], List[object]] = {}
        for inst in kept:
            tid = wc.tile_of(inst, bbox, tw, th, nx, ny)
            by_tile.setdefault(tid, []).append(inst)
        k_frac = max(0.0, args.k_percent / 100.0)
        for tid in sorted(by_tile.keys()):
            pool = sorted(by_tile[tid], key=lambda c: c.getName())
            n_pick = int(math.ceil(len(pool) * k_frac)) if pool else 0
            if n_pick <= 0:
                continue
            n_pick = min(n_pick, len(pool))
            rng = wc.tile_rng(seed, tid)
            picks = rng.sample(pool, n_pick)
            for inst in picks:
                tgt = wc.target_residue(seed, tid, inst.getName())
                cells_info.append((inst, tgt, tid))

    n = len(cells_info)
    satisfied = 0
    failures: List[str] = []
    for inst, tgt, _ in cells_info:
        x, y = wc.inst_bottom_left(inst)
        ry = _snap_row_y(y, row_bottoms)
        rxmin = row_xmin_by_y.get(ry, 0)
        col = int(round((x - rxmin) / float(sw)))
        res = col % wc.MODULUS
        if res == tgt:
            satisfied += 1
        else:
            failures.append(
                f"{inst.getName()} col={col} residue={res} want={tgt} y={y}"
            )

    total_expected = n + len(missing)
    num_fail = n - satisfied
    pc = wc.binomial_pc(n, num_fail)

    print(
        f"[wm_verify] constraints={n} satisfied={satisfied} failed_parity={num_fail} "
        f"missing={len(missing)} Pc(p=1/{wc.MODULUS})<={pc:.6e}"
    )
    if failures and len(failures) <= 20:
        for line in failures:
            print(f"  FAIL: {line}")
    elif failures:
        print(f"  ({len(failures)} failing cells; suppressing details)")
    if missing and len(missing) <= 10:
        for name in missing:
            print(f"  MISSING: {name}")
    elif missing:
        print(f"  ({len(missing)} missing cells in target ODB)")

    if args.output_cell_list:
        _write_cell_list(
            args.output_cell_list, cells_info, row_xmin_by_y, row_bottoms, sw
        )
        print(f"[wm_verify] cell list -> {args.output_cell_list}")

    return 0 if (num_fail == 0 and not missing) else 2


if __name__ == "__main__":
    raise SystemExit(main())
