#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Verify row-parity watermark cells across multiple post-flow ODB checkpoints.

Reads the embed cell list CSV (ground truth: cell names + required parities).
Do NOT re-run watermark_selection on later ODBs — CTS adds buffers and changes
the sorted candidate pool, which would select different cells.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

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


@dataclass
class WmCell:
    cell_name: str
    master: str
    required_parity: int


@dataclass
class CellStageResult:
    row_idx: Optional[int]
    satisfied: bool
    missing: bool
    x: Optional[int] = None
    y: Optional[int] = None


def _read_cell_list_csv(path: str) -> List[WmCell]:
    out: List[WmCell] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("cell_name", "").strip()
            if not name:
                continue
            par_s = row.get("required_parity", "0").strip()
            try:
                par = int(par_s)
            except ValueError:
                par = 0
            master = row.get("master", "").strip()
            out.append(WmCell(cell_name=name, master=master, required_parity=par))
    if not out:
        raise ValueError(f"No watermark cells found in {path}")
    return out


def _parse_stages_from_env(s: str) -> List[Tuple[str, str]]:
    """Parse WM_VERIFY_STAGES: 'label1:/path/a.odb,label2:/path/b.odb'."""
    stages: List[Tuple[str, str]] = []
    for token in s.split(","):
        token = token.strip()
        if not token:
            continue
        if ":" not in token:
            raise ValueError(f"Invalid stage token (expected label:path): {token!r}")
        label, path = token.split(":", 1)
        label = label.strip()
        path = path.strip()
        if not label or not path:
            raise ValueError(f"Invalid stage token: {token!r}")
        stages.append((label, path))
    return stages


def _verify_one_stage(
    cells: Sequence[WmCell],
    odb_path: str,
    label: str,
    dbu_per_micron: int,
) -> Tuple[List[CellStageResult], List[str]]:
    """Load one ODB and check each watermark cell. Returns (results, failure_lines)."""
    tech = Tech()
    design = Design(tech)
    design.readDb(odb_path)
    block = design.getBlock()
    if block is None:
        raise RuntimeError(f"No block in {odb_path}")

    rows = list(block.getRows())
    if not rows:
        raise RuntimeError(f"No rows in {odb_path}")

    row_bottoms = wc.sorted_row_bottoms(block)
    inst_map: Dict[str, object] = {inst.getName(): inst for inst in block.getInsts()}

    results: List[CellStageResult] = []
    failures: List[str] = []

    for c in cells:
        inst = inst_map.get(c.cell_name)
        if inst is None:
            results.append(
                CellStageResult(row_idx=None, satisfied=False, missing=True)
            )
            failures.append(f"{c.cell_name}: instance not found in {label}")
            continue

        x, y = wc.inst_bottom_left(inst)
        ri = _nearest_row_index(y, row_bottoms)
        ok = ri % 2 == c.required_parity
        results.append(
            CellStageResult(row_idx=ri, satisfied=ok, missing=False, x=x, y=y)
        )
        if not ok:
            y_um = y / float(dbu_per_micron)
            failures.append(
                f"{c.cell_name}: row_idx={ri} need_parity={c.required_parity} "
                f"y={y} DBU ({y_um:.3f} µm)"
            )

    return results, failures


def _write_report_csv(
    path: str,
    cells: Sequence[WmCell],
    stage_labels: Sequence[str],
    all_results: Sequence[Sequence[CellStageResult]],
) -> None:
    """One row per cell; columns cell_name, master, required_parity, then per stage."""
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        header = ["cell_name", "master", "required_parity"]
        for lab in stage_labels:
            header.append(f"{lab}_row_idx")
            header.append(f"{lab}_satisfied")
        writer.writerow(header)

        for i, c in enumerate(cells):
            row: List[object] = [c.cell_name, c.master, c.required_parity]
            for stage_i in range(len(stage_labels)):
                r = all_results[stage_i][i]
                if r.missing:
                    row.extend(["", "False"])
                else:
                    row.extend([r.row_idx if r.row_idx is not None else "", r.satisfied])
            writer.writerow(row)


def main() -> int:
    p = argparse.ArgumentParser(
        description="Verify watermark cells across multiple flow-stage ODBs"
    )
    p.add_argument(
        "--cell-list",
        default=os.environ.get("WM_CELL_LIST", ""),
        help="Embed CSV (ground truth). Env: WM_CELL_LIST",
    )
    p.add_argument(
        "--stage",
        action="append",
        default=None,
        metavar="LABEL:ODB",
        help="Checkpoint label and .odb path (repeatable). "
        "Alternatively set WM_VERIFY_STAGES='label:path,...'",
    )
    p.add_argument(
        "--output-csv",
        default=os.environ.get("WM_STAGE_REPORT", ""),
        help="Optional per-cell x stage report CSV. Env: WM_STAGE_REPORT",
    )
    p.add_argument(
        "--dbu-per-micron",
        type=int,
        default=int(os.environ.get("WM_DBU_PER_MICRON", "2000")),
        help="DBU per micron for optional coordinate display (default 2000).",
    )
    args = p.parse_args(wc.argv_after_openroad_driver())

    if not args.cell_list:
        p.error("--cell-list (or WM_CELL_LIST) is required")

    stages: List[Tuple[str, str]] = []
    if args.stage:
        for tok in args.stage:
            tok = tok.strip()
            if ":" not in tok:
                p.error(f"--stage must be LABEL:path, got {tok!r}")
            lab, path = tok.split(":", 1)
            lab, path = lab.strip(), path.strip()
            if not lab or not path:
                p.error(f"Invalid --stage {tok!r}")
            stages.append((lab, path))
    else:
        env_st = os.environ.get("WM_VERIFY_STAGES", "").strip()
        if not env_st:
            p.error(
                "No --stage given and WM_VERIFY_STAGES is empty. "
                "Provide --stage LABEL:ODB ... or WM_VERIFY_STAGES"
            )
        stages = _parse_stages_from_env(env_st)

    cells = _read_cell_list_csv(args.cell_list)
    n = len(cells)

    print(
        f"[watermark_verify_stages] cells={n} from {args.cell_list}\n"
        f"{'stage':<18} {'constraints':>11} {'satisfied':>10} {'failed':>8} "
        f"{'missing':>8}  Pc(coincidence)"
    )

    completed_stages: List[Tuple[str, List[CellStageResult]]] = []
    any_fail = False

    for label, odb_path in stages:
        if not os.path.isfile(odb_path):
            print(f"{label:<18} ERROR: file not found: {odb_path}", file=sys.stderr)
            any_fail = True
            continue
        try:
            results, failures = _verify_one_stage(
                cells, odb_path, label, args.dbu_per_micron
            )
        except Exception as e:
            print(f"{label:<18} ERROR: {e}", file=sys.stderr)
            any_fail = True
            continue

        completed_stages.append((label, results))
        sat = sum(1 for r in results if r.satisfied)
        miss = sum(1 for r in results if r.missing)
        fail = n - sat
        num_fail_parity = sum(1 for r in results if not r.missing and not r.satisfied)
        pc = wc.binomial_pc(n, fail)
        print(
            f"{label:<18} {n:11d} {sat:10d} {num_fail_parity:8d} {miss:8d}  <= {pc:.6e}"
        )
        if failures and len(failures) <= 15:
            for line in failures:
                print(f"  FAIL: {line}")
        elif failures:
            print(f"  ({len(failures)} failing cells; first 15 shown)")
            for line in failures[:15]:
                print(f"  FAIL: {line}")

        if fail > 0:
            any_fail = True

    if args.output_csv and completed_stages:
        if len(completed_stages) == len(stages):
            labels = [lab for lab, _ in completed_stages]
            all_rows = [res for _, res in completed_stages]
            _write_report_csv(args.output_csv, cells, labels, all_rows)
            print(f"[watermark_verify_stages] report written -> {args.output_csv}")
        else:
            print(
                "[watermark_verify_stages] skipping --output-csv: not all stages completed",
                file=sys.stderr,
            )

    return 0 if not any_fail else 2


if __name__ == "__main__":
    raise SystemExit(main())
