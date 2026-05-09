#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Verify CTS fanout-parity watermark across flow-stage ODB checkpoints.

Reads embed CSV (ground truth: target LCB name + target_bit). Do NOT try
to re-derive pair selection from the seed on later ODBs -- GRT/DRT may
have renamed or re-laid-out buffers.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from openroad import Design, Tech

import cts_watermark_common as cc


@dataclass
class WmPair:
    pair_idx: int
    pair_key: str
    target_lcb: str
    other_lcb: str
    target_bit: int


@dataclass
class PairStageResult:
    observed_fanout: Optional[int]
    observed_bit: Optional[int]
    satisfied: bool
    missing: bool


def _read_pairs_csv(path: str) -> List[WmPair]:
    out: List[WmPair] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            name = r.get("target_lcb", "").strip()
            if not name:
                continue
            try:
                idx = int(r.get("pair_idx", "0"))
            except ValueError:
                idx = 0
            try:
                tb = int(r.get("target_bit", "0"))
            except ValueError:
                tb = 0
            out.append(WmPair(
                pair_idx=idx,
                pair_key=r.get("pair_key", r.get("parent", "")).strip(),
                target_lcb=name,
                other_lcb=r.get("other_lcb", "").strip(),
                target_bit=tb,
            ))
    if not out:
        raise ValueError(f"No watermark pairs found in {path}")
    return out


def _parse_stages_env(s: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for tok in s.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if ":" not in tok:
            raise ValueError(f"Invalid stage token (expected label:path): {tok!r}")
        label, path = tok.split(":", 1)
        label, path = label.strip(), path.strip()
        if not label or not path:
            raise ValueError(f"Invalid stage token: {tok!r}")
        out.append((label, path))
    return out


def _verify_one_stage(
    pairs: Sequence[WmPair], odb_path: str, label: str
) -> Tuple[List[PairStageResult], List[str]]:
    tech = Tech()
    design = Design(tech)
    design.readDb(odb_path)
    block = design.getBlock()
    if block is None:
        raise RuntimeError(f"No block in {odb_path}")

    inst_map: Dict[str, object] = {inst.getName(): inst for inst in block.getInsts()}
    results: List[PairStageResult] = []
    failures: List[str] = []
    for wp in pairs:
        inst = inst_map.get(wp.target_lcb)
        if inst is None:
            results.append(PairStageResult(
                observed_fanout=None, observed_bit=None,
                satisfied=False, missing=True,
            ))
            failures.append(
                f"pair {wp.pair_idx}: {wp.target_lcb} missing in {label}"
            )
            continue
        try:
            f = cc.lcb_fanout(inst)
        except Exception as e:
            results.append(PairStageResult(
                observed_fanout=None, observed_bit=None,
                satisfied=False, missing=True,
            ))
            failures.append(
                f"pair {wp.pair_idx}: fanout() failed for {wp.target_lcb} "
                f"in {label}: {e}"
            )
            continue
        bit = f % 2
        ok = bit == wp.target_bit
        results.append(PairStageResult(
            observed_fanout=f, observed_bit=bit, satisfied=ok, missing=False,
        ))
        if not ok:
            failures.append(
                f"pair {wp.pair_idx}: {wp.target_lcb} fanout={f} bit={bit} "
                f"want={wp.target_bit}"
            )
    return results, failures


def _write_report(
    path: str, pairs: Sequence[WmPair],
    labels: Sequence[str],
    all_results: Sequence[Sequence[PairStageResult]],
) -> None:
    header = [
        "pair_idx", "pair_key", "target_lcb", "other_lcb", "target_bit",
    ]
    for lab in labels:
        header.extend([
            f"{lab}_fanout", f"{lab}_bit", f"{lab}_satisfied",
        ])
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for i, wp in enumerate(pairs):
            row: List[object] = [
                wp.pair_idx, wp.pair_key, wp.target_lcb, wp.other_lcb, wp.target_bit,
            ]
            for si in range(len(labels)):
                r = all_results[si][i]
                if r.missing:
                    row.extend(["", "", "False"])
                else:
                    row.extend([
                        r.observed_fanout if r.observed_fanout is not None else "",
                        r.observed_bit if r.observed_bit is not None else "",
                        r.satisfied,
                    ])
            w.writerow(row)


def main() -> int:
    p = argparse.ArgumentParser(
        description="Verify CTS watermark across flow-stage ODBs"
    )
    p.add_argument("--cell-list", default=os.environ.get("WM_CELL_LIST", ""))
    p.add_argument(
        "--stage", action="append", default=None, metavar="LABEL:ODB",
    )
    p.add_argument(
        "--output-csv", default=os.environ.get("WM_STAGE_REPORT", ""),
    )
    args = p.parse_args(cc.argv_after_openroad_driver())

    if not args.cell_list:
        p.error("--cell-list (or WM_CELL_LIST) is required")

    stages: List[Tuple[str, str]] = []
    if args.stage:
        for tok in args.stage:
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
                "Provide --stage LABEL:ODB or WM_VERIFY_STAGES"
            )
        stages = _parse_stages_env(env_st)

    pairs = _read_pairs_csv(args.cell_list)
    n = len(pairs)

    print(
        f"[cts_wm_verify_stages] pairs={n} from {args.cell_list}\n"
        f"{'stage':<18} {'pairs':>7} {'satisfied':>10} {'failed':>8} "
        f"{'missing':>8}  Pc(p=1/2)"
    )

    completed: List[Tuple[str, List[PairStageResult]]] = []
    any_fail = False
    for label, odb_path in stages:
        if not os.path.isfile(odb_path):
            print(
                f"{label:<18} ERROR: file not found: {odb_path}", file=sys.stderr
            )
            any_fail = True
            continue
        try:
            results, failures = _verify_one_stage(pairs, odb_path, label)
        except Exception as e:
            print(f"{label:<18} ERROR: {e}", file=sys.stderr)
            any_fail = True
            continue
        completed.append((label, results))
        sat = sum(1 for r in results if r.satisfied)
        miss = sum(1 for r in results if r.missing)
        fail = n - sat
        num_fail_parity = sum(
            1 for r in results if not r.missing and not r.satisfied
        )
        pc = cc.binomial_pc(n, fail)
        print(
            f"{label:<18} {n:7d} {sat:10d} {num_fail_parity:8d} {miss:8d}  "
            f"<= {pc:.6e}"
        )
        if failures and len(failures) <= 15:
            for line in failures:
                print(f"  FAIL: {line}")
        elif failures:
            print(f"  ({len(failures)} failing pairs; first 15 shown)")
            for line in failures[:15]:
                print(f"  FAIL: {line}")
        if fail > 0:
            any_fail = True

    if args.output_csv and completed and len(completed) == len(stages):
        labels = [lab for lab, _ in completed]
        all_rows = [res for _, res in completed]
        out_dir = os.path.dirname(os.path.abspath(args.output_csv))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        _write_report(args.output_csv, pairs, labels, all_rows)
        print(f"[cts_wm_verify_stages] report -> {args.output_csv}")
    elif args.output_csv:
        print(
            "[cts_wm_verify_stages] skipping report: not all stages completed",
            file=sys.stderr,
        )

    return 0 if not any_fail else 2


if __name__ == "__main__":
    raise SystemExit(main())
