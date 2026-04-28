#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Verify CTS fanout-parity watermark against an ODB.

Ground truth is read from the embed CSV: ``target_lcb``, ``target_bit``.
Parity is ``seq_fanout(target_lcb) % 2``. For ``quasi_leaf`` rows,
``repair_fanout_*`` tampering is flagged when counts diverge from embed.
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
    channel: str
    l_a: str
    l_b: str
    target_lcb: str
    other_lcb: str
    target_bit: int
    repair_fanout_target: Optional[int]
    repair_fanout_other: Optional[int]


def _parse_opt_int(val: object) -> Optional[int]:
    if val is None or val == "":
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def read_pairs_csv(path: str) -> List[WmPair]:
    """Load accepted watermark pairs from embed CSV (also used by verify_stages).

    The embed CSV is an audit log: it contains successful embeds, failed trial
    rows (for example ``no_boundary_ff``), and bookkeeping skips. Verification
    must only check accepted rows, otherwise failed attempts are reported as
    downstream parity failures.
    """
    out: List[WmPair] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            name = r.get("target_lcb", "").strip()
            if not name:
                continue
            skipped = (r.get("skipped_reason") or "").strip()
            if skipped:
                continue
            try:
                idx = int(r.get("pair_idx", "0"))
            except ValueError:
                idx = 0
            try:
                tb = int(r.get("target_bit", "0"))
            except ValueError:
                tb = 0
            final_bit = _parse_opt_int(r.get("final_bit"))
            if final_bit is not None and final_bit != tb:
                continue
            ch = (r.get("channel") or "pure").strip().lower()
            if ch not in ("pure", "quasi_leaf", "pure_quasi"):
                ch = "pure"
            out.append(
                WmPair(
                    pair_idx=idx,
                    pair_key=r.get("pair_key", r.get("parent", "")).strip(),
                    channel=ch,
                    l_a=r.get("L_A", "").strip(),
                    l_b=r.get("L_B", "").strip(),
                    target_lcb=name,
                    other_lcb=r.get("other_lcb", "").strip(),
                    target_bit=tb,
                    repair_fanout_target=_parse_opt_int(r.get("repair_fanout_target")),
                    repair_fanout_other=_parse_opt_int(r.get("repair_fanout_other")),
                )
            )
    if not out:
        raise ValueError(f"No watermark pairs found in {path}")
    return out


def _write_report_csv(path: str, rows: List[Dict[str, object]]) -> None:
    header = [
        "pair_idx",
        "pair_key",
        "channel",
        "target_lcb",
        "other_lcb",
        "target_bit",
        "observed_seq_fanout",
        "observed_bit",
        "repair_fanout_target_expected",
        "repair_fanout_other_expected",
        "repair_fanout_target_observed",
        "repair_fanout_other_observed",
        "tampered",
        "satisfied",
        "missing",
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
    p.add_argument("--output-csv", default=os.environ.get("WM_CTS_VERIFY_CSV"))
    args = p.parse_args(cc.argv_after_openroad_driver())

    if not args.input:
        p.error("--input (or WM_CTS_VERIFY_INPUT) is required")
    if not args.cell_list:
        p.error("--cell-list (or WM_CELL_LIST) is required")

    pairs = read_pairs_csv(args.cell_list)

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
    tampered_count = 0
    failures: List[str] = []

    for wp in pairs:
        inst = inst_map.get(wp.target_lcb)
        if inst is None:
            rows.append({
                "pair_idx": wp.pair_idx,
                "pair_key": wp.pair_key,
                "channel": wp.channel,
                "target_lcb": wp.target_lcb,
                "other_lcb": wp.other_lcb,
                "target_bit": wp.target_bit,
                "observed_seq_fanout": "",
                "observed_bit": "",
                "repair_fanout_target_expected": wp.repair_fanout_target,
                "repair_fanout_other_expected": wp.repair_fanout_other,
                "repair_fanout_target_observed": "",
                "repair_fanout_other_observed": "",
                "tampered": "",
                "satisfied": False,
                "missing": True,
            })
            missing += 1
            failures.append(f"pair {wp.pair_idx}: {wp.target_lcb} missing in ODB")
            continue
        try:
            seq_n = cc.lcb_seq_fanout(inst)
            rt_obs = cc.lcb_repair_fanout(inst)
            inst_o = inst_map.get(wp.other_lcb) if wp.other_lcb else None
            ro_obs = (
                cc.lcb_repair_fanout(inst_o) if inst_o is not None else None
            )
        except Exception as e:
            missing += 1
            rows.append({
                "pair_idx": wp.pair_idx,
                "pair_key": wp.pair_key,
                "channel": wp.channel,
                "target_lcb": wp.target_lcb,
                "other_lcb": wp.other_lcb,
                "target_bit": wp.target_bit,
                "observed_seq_fanout": "",
                "observed_bit": "",
                "repair_fanout_target_expected": wp.repair_fanout_target,
                "repair_fanout_other_expected": wp.repair_fanout_other,
                "repair_fanout_target_observed": "",
                "repair_fanout_other_observed": "",
                "tampered": "",
                "satisfied": False,
                "missing": True,
            })
            failures.append(
                f"pair {wp.pair_idx}: seq_fanout failed for {wp.target_lcb}: {e}"
            )
            continue

        bit = seq_n % 2
        ok_parity = bit == wp.target_bit

        tampered = False
        if wp.channel in ("quasi_leaf", "pure_quasi"):
            if wp.repair_fanout_target is not None and rt_obs != wp.repair_fanout_target:
                tampered = True
            if (
                wp.repair_fanout_other is not None
                and wp.other_lcb
                and ro_obs is not None
                and ro_obs != wp.repair_fanout_other
            ):
                tampered = True
            if tampered:
                tampered_count += 1

        ok = ok_parity and not tampered
        if ok:
            satisfied += 1
        else:
            if not ok_parity:
                fail_parity += 1
            failures.append(
                f"pair {wp.pair_idx}: {wp.target_lcb} seq_fanout={seq_n} bit={bit} "
                f"want={wp.target_bit} tampered={tampered}"
            )

        rows.append({
            "pair_idx": wp.pair_idx,
            "pair_key": wp.pair_key,
            "channel": wp.channel,
            "target_lcb": wp.target_lcb,
            "other_lcb": wp.other_lcb,
            "target_bit": wp.target_bit,
            "observed_seq_fanout": seq_n,
            "observed_bit": bit,
            "repair_fanout_target_expected": wp.repair_fanout_target,
            "repair_fanout_other_expected": wp.repair_fanout_other,
            "repair_fanout_target_observed": rt_obs,
            "repair_fanout_other_observed": ro_obs if ro_obs is not None else "",
            "tampered": tampered,
            "satisfied": ok,
            "missing": False,
        })

    n = len(pairs)
    fail = n - satisfied
    pc = cc.binomial_pc(n, fail)

    print(
        f"[cts_wm_verify] pairs={n} satisfied={satisfied} failed_parity={fail_parity} "
        f"tampered={tampered_count} missing={missing} Pc(p=1/2)<={pc:.6e}"
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
