#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Verify CTS fanout-parity watermark against an ODB.

Ground truth is always read from the embed CSV (``WM_CELL_LIST``): the CTS
netlist cannot be re-derived from the seed alone once downstream stages
mutate the clock tree. For each row, re-look-up the target LCB by name in
the loaded ODB and check ``fanout(target_lcb) %% 2 == target_bit``.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional

from openroad import Design, Tech

import cts_watermark_common as cc


@dataclass
class WmPair:
    pair_idx: int
    pair_key: str
    l_a: str
    l_b: str
    target_lcb: str
    other_lcb: str
    target_bit: int


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
                l_a=r.get("L_A", "").strip(),
                l_b=r.get("L_B", "").strip(),
                target_lcb=name,
                other_lcb=r.get("other_lcb", "").strip(),
                target_bit=tb,
            ))
    if not out:
        raise ValueError(f"No watermark pairs found in {path}")
    return out


def _write_report_csv(path: str, rows: List[Dict[str, object]]) -> None:
    header = [
        "pair_idx", "pair_key", "target_lcb", "other_lcb",
        "target_bit", "observed_fanout", "observed_bit",
        "satisfied", "missing",
    ]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow([r.get(k, "") for k in header])


def main() -> int:
    p = argparse.ArgumentParser(description="Verify CTS fanout-parity watermark")
    p.add_argument("--input", default=os.environ.get("WM_CTS_VERIFY_INPUT"))
    p.add_argument("--cell-list", default=os.environ.get("WM_CELL_LIST"))
    p.add_argument(
        "--output-csv", default=os.environ.get("WM_CTS_VERIFY_CSV"),
    )
    args = p.parse_args(cc.argv_after_openroad_driver())

    if not args.input:
        p.error("--input (or WM_CTS_VERIFY_INPUT) is required")
    if not args.cell_list:
        p.error("--cell-list (or WM_CELL_LIST) is required")

    pairs = _read_pairs_csv(args.cell_list)

    tech = Tech()
    design = Design(tech)
    design.readDb(args.input)
    block = design.getBlock()
    if block is None:
        print("No block in database", file=sys.stderr)
        return 1

    inst_map: Dict[str, object] = {inst.getName(): inst for inst in block.getInsts()}
    rows: List[Dict[str, object]] = []
    satisfied = 0
    missing = 0
    fail_parity = 0
    failures: List[str] = []
    for wp in pairs:
        inst = inst_map.get(wp.target_lcb)
        if inst is None:
            rows.append({
                "pair_idx": wp.pair_idx,
                "pair_key": wp.pair_key,
                "target_lcb": wp.target_lcb,
                "other_lcb": wp.other_lcb,
                "target_bit": wp.target_bit,
                "observed_fanout": "",
                "observed_bit": "",
                "satisfied": False,
                "missing": True,
            })
            missing += 1
            failures.append(f"pair {wp.pair_idx}: {wp.target_lcb} missing in ODB")
            continue
        try:
            f = cc.lcb_fanout(inst)
        except Exception as e:
            missing += 1
            rows.append({
                "pair_idx": wp.pair_idx,
                "pair_key": wp.pair_key,
                "target_lcb": wp.target_lcb,
                "other_lcb": wp.other_lcb,
                "target_bit": wp.target_bit,
                "observed_fanout": "",
                "observed_bit": "",
                "satisfied": False,
                "missing": True,
            })
            failures.append(
                f"pair {wp.pair_idx}: fanout() failed for {wp.target_lcb}: {e}"
            )
            continue
        bit = f % 2
        ok = bit == wp.target_bit
        if ok:
            satisfied += 1
        else:
            fail_parity += 1
            failures.append(
                f"pair {wp.pair_idx}: {wp.target_lcb} fanout={f} bit={bit} "
                f"want={wp.target_bit}"
            )
        rows.append({
            "pair_idx": wp.pair_idx,
            "pair_key": wp.pair_key,
            "target_lcb": wp.target_lcb,
            "other_lcb": wp.other_lcb,
            "target_bit": wp.target_bit,
            "observed_fanout": f,
            "observed_bit": bit,
            "satisfied": ok,
            "missing": False,
        })

    n = len(pairs)
    fail = n - satisfied
    pc = cc.binomial_pc(n, fail)

    print(
        f"[cts_wm_verify] pairs={n} satisfied={satisfied} failed_parity={fail_parity} "
        f"missing={missing} Pc(p=1/2)<={pc:.6e}"
    )
    if failures and len(failures) <= 20:
        for line in failures:
            print(f"  FAIL: {line}")
    elif failures:
        print(f"  ({len(failures)} failing pairs; first 20 shown)")
        for line in failures[:20]:
            print(f"  FAIL: {line}")

    if args.output_csv:
        out_dir = os.path.dirname(os.path.abspath(args.output_csv))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        _write_report_csv(args.output_csv, rows)
        print(f"[cts_wm_verify] report -> {args.output_csv}")

    return 0 if fail == 0 and missing == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
