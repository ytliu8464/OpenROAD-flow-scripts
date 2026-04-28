#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Verify pairwise / group ordering placement watermark from CSV ground truth."""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from typing import Dict, List, Sequence, Tuple

from openroad import Design, Tech

import watermark_common as wc


_T0 = time.time()


def _elapsed() -> str:
    return f"{time.time() - _T0:8.2f}s"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [wm_verify +{_elapsed()}] {msg}", flush=True)


def _should_verify_row(skip_reason: str) -> bool:
    s = (skip_reason or "").strip()
    return s in ("", "already_satisfied")


def pair_bit_from_inst(a, b) -> int:
    ax, _ = wc.inst_bottom_left(a)
    bx, _ = wc.inst_bottom_left(b)
    return 0 if ax < bx else 1


def _read_csv(path: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    with open(path, newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            rows.append(dict(row))
    return rows


def verify_from_csv(block, rows: Sequence[Dict[str, str]]) -> Tuple[int, int, int, int, List[str]]:
    """Returns (pairs_checked, pairs_ok, groups_checked, groups_ok, failures)."""
    inst_map: Dict[str, object] = {i.getName(): i for i in block.getInsts()}
    failures: List[str] = []
    pc, pok, gc, gok = 0, 0, 0, 0

    for row in rows:
        kind = row.get("kind", "").strip()
        skip = row.get("skipped_reason", "").strip()
        if not _should_verify_row(skip):
            continue

        if kind == "pair":
            na, nb = row.get("A_name", "").strip(), row.get("B_name", "").strip()
            if not na or not nb:
                continue
            ia, ib = inst_map.get(na), inst_map.get(nb)
            if ia is None or ib is None:
                failures.append(f"pair {na}|{nb}: instance missing")
                pc += 1
                continue
            try:
                tgt = int(row.get("target_bit", "0"))
            except ValueError:
                tgt = 0
            pc += 1
            cur = pair_bit_from_inst(ia, ib)
            if cur == tgt:
                pok += 1
            else:
                failures.append(f"pair {na}|{nb}: bit={cur} want={tgt}")

        elif kind == "triple":
            na = row.get("A_name", "").strip()
            nb = row.get("B_name", "").strip()
            nc = row.get("C_name", "").strip()
            if not na or not nb or not nc:
                continue
            ia, ib, ic = inst_map.get(na), inst_map.get(nb), inst_map.get(nc)
            if None in (ia, ib, ic):
                failures.append(f"triple {na},{nb},{nc}: instance missing")
                gc += 1
                continue
            gc += 1
            try:
                tgt_perm = int(row.get("target_perm", "0"))
            except ValueError:
                tgt_perm = 0
            try:
                exp = wc.permuted_order_names(
                    tuple(sorted((na, nb, nc))),
                    tgt_perm,
                )
            except Exception:
                failures.append(f"triple {na},{nb},{nc}: bad perm")
                continue
            obs = wc.order_names_left_to_right((ia, ib, ic))
            if obs == exp:
                gok += 1
            else:
                failures.append(
                    f"triple {na},{nb},{nc}: order={obs} want={exp}"
                )

    return pc, pok, gc, gok, failures


def main() -> int:
    p = argparse.ArgumentParser(description="Verify ordering placement watermark")
    p.add_argument("--input", default=os.environ.get("WM_VERIFY_INPUT"))
    p.add_argument("--cell-list", default=os.environ.get("WM_CELL_LIST"))
    p.add_argument("--output-csv", default=os.environ.get("WM_VERIFY_CELL_LIST"))
    args = p.parse_args(wc.argv_after_openroad_driver())

    if not args.input:
        p.error("--input / WM_VERIFY_INPUT required")
    if not args.cell_list:
        p.error("--cell-list / WM_CELL_LIST required")

    _log("start")
    _log(f"input={args.input}")
    _log(f"cell_list={args.cell_list}")
    t_phase = time.time()
    rows = _read_csv(args.cell_list)
    tech = Tech()
    design = Design(tech)
    design.readDb(args.input)
    block = design.getBlock()
    if block is None:
        print("No block", file=sys.stderr)
        return 1
    _log(f"read inputs done in {time.time() - t_phase:.2f}s: csv_rows={len(rows)}")

    t_phase = time.time()
    pc, pok, gc, gok, failures = verify_from_csv(block, rows)
    _log(f"verification scan done in {time.time() - t_phase:.2f}s")

    _log(
        f"pairs checked={pc} ok={pok}  "
        f"groups checked={gc} ok={gok}"
    )
    if failures:
        for line in failures[:40]:
            print(f"  FAIL: {line}")
        if len(failures) > 40:
            print(f"  ... and {len(failures) - 40} more")

    pc_p = wc.binomial_pc(pc, pc - pok, 0.5) if pc else 1.0
    pc_g = wc.binomial_pc(gc, gc - gok, 1.0 / 6.0) if gc else 1.0
    _log(f"Pc pairs<={pc_p:.3e} groups<={pc_g:.3e}")

    if args.output_csv:
        with open(args.output_csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["metric", "value"])
            w.writerow(["pairs_checked", pc])
            w.writerow(["pairs_ok", pok])
            w.writerow(["groups_checked", gc])
            w.writerow(["groups_ok", gok])
        _log(f"summary CSV -> {args.output_csv}")

    _log("done")
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
