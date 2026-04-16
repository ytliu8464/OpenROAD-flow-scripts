#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Verify row-parity placement watermark (must use same --key, --message, --num-cells)."""

from __future__ import annotations

import argparse
import csv
import os
import sys
from typing import List, Sequence

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


def _write_cell_list(
    path: str,
    chosen: Sequence[object],
    parities: Sequence[int],
    row_bottoms: Sequence[int],
) -> None:
    """Write watermark cell verification CSV.

    Columns:
        cell_name        - instance name
        master           - master cell name
        required_parity  - 0 (EVEN row index) or 1 (ODD row index)
        x, y             - bottom-left position in the verified design (DBU)
        row_idx          - row index in the verified design
        satisfied        - True if row_idx parity matches required_parity
    """
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "cell_name", "master",
            "required_parity",
            "x", "y",
            "row_idx",
            "satisfied",
        ])
        for inst, par in zip(chosen, parities):
            name = inst.getName()
            master = inst.getMaster().getName()
            x, y = wc.inst_bottom_left(inst)
            ri = _nearest_row_index(y, row_bottoms)
            satisfied = ri % 2 == par
            writer.writerow([name, master, par, x, y, ri, satisfied])


def main() -> int:
    p = argparse.ArgumentParser(description="Verify row-parity watermark")
    p.add_argument(
        "--input",
        default=os.environ.get("WM_VERIFY_INPUT"),
        help="Watermarked .odb. Env: WM_VERIFY_INPUT",
    )
    p.add_argument("--message", default=os.environ.get("WM_MESSAGE", ""))
    p.add_argument("--key", default=os.environ.get("WM_KEY", ""))
    p.add_argument(
        "--num-cells",
        type=int,
        default=int(os.environ.get("WM_NUM_CELLS", "100")),
    )
    p.add_argument(
        "--output-cell-list",
        default=os.environ.get("WM_VERIFY_CELL_LIST"),
        help="Optional output CSV of watermark cell verification results. Env: WM_VERIFY_CELL_LIST",
    )
    args = p.parse_args(wc.argv_after_openroad_driver())

    if not args.input:
        p.error("--input (or WM_VERIFY_INPUT) is required")
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

    cells = wc.collect_movable_core_cells(block)
    cells = [c for c in cells if wc.inst_size(c)[1] == site_h]
    chosen, parities, _rng = wc.watermark_selection(
        args.key, args.message, cells, args.num_cells
    )

    satisfied = 0
    failures: List[str] = []
    for inst, par in zip(chosen, parities):
        ri = _nearest_row_index(wc.inst_bottom_left(inst)[1], row_bottoms)
        ok = ri % 2 == par
        if ok:
            satisfied += 1
        else:
            x, y = wc.inst_bottom_left(inst)
            failures.append(
                f"{inst.getName()} row_idx={ri} parity_need={par} y={y}"
            )

    num_fail = len(chosen) - satisfied
    pc = wc.binomial_pc(len(chosen), num_fail)

    print(
        f"[watermark_verify] constraints={len(chosen)} satisfied={satisfied} "
        f"failed={num_fail} Pc(coincidence)<={pc:.6e}"
    )
    if failures and len(failures) <= 20:
        for line in failures:
            print(f"  FAIL: {line}")
    elif failures:
        print(f"  ({len(failures)} failing cells; suppressing details)")

    if args.output_cell_list:
        _write_cell_list(args.output_cell_list, chosen, parities, row_bottoms)
        print(f"[watermark_verify] cell list written -> {args.output_cell_list}")

    return 0 if num_fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
