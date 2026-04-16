#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""
Verify spacing-only NDR routing watermark on a routed (or pre-route) ODB.

Re-derives watermark nets from key/message, checks net NDR and metal2/metal3
spacing, optionally scans dbWire for RULE opcodes.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from typing import Optional, Tuple

from openroad import Design, Tech

import route_watermark_common as rwc

# Conservative p for Pc when NDR metadata is present (accidental identical NDR).
P_NULL_ACCIDENTAL_NDR = 1e-12


def _verify_net(
    net,
    otech,
    expected_ndr_name: str,
    expected_spacing_dbu: int,
    target_layers: Tuple[str, ...],
) -> Tuple[bool, str, Optional[int], Optional[int], bool]:
    """Returns (ok, reason, spacing_m2, spacing_m3, wire_has_rule)."""
    ndr = net.getNonDefaultRule()
    if ndr is None:
        return False, "no_ndr", None, None, rwc.wire_has_rule_opcode(net)
    if ndr.getName() != expected_ndr_name:
        return (
            False,
            f"wrong_ndr:{ndr.getName()}",
            None,
            None,
            rwc.wire_has_rule_opcode(net),
        )
    sm2 = rwc.layer_spacing_from_ndr(ndr, "metal2", otech)
    sm3 = rwc.layer_spacing_from_ndr(ndr, "metal3", otech)
    if "metal2" in target_layers and sm2 != expected_spacing_dbu:
        return False, f"spacing_m2_mismatch:{sm2}!={expected_spacing_dbu}", sm2, sm3, rwc.wire_has_rule_opcode(net)
    if "metal3" in target_layers and sm3 != expected_spacing_dbu:
        return False, f"spacing_m3_mismatch:{sm3}!={expected_spacing_dbu}", sm2, sm3, rwc.wire_has_rule_opcode(net)
    return True, "ok", sm2, sm3, rwc.wire_has_rule_opcode(net)


def main() -> int:
    p = argparse.ArgumentParser(description="Verify spacing NDR routing watermark")
    p.add_argument(
        "--input",
        default=os.environ.get("WM_RT_VERIFY_INPUT"),
        help="ODB after routing (or embed output). Env: WM_RT_VERIFY_INPUT",
    )
    p.add_argument(
        "--output-csv",
        default=os.environ.get("WM_RT_VERIFY_CSV"),
        help="Optional per-net verification CSV. Env: WM_RT_VERIFY_CSV",
    )
    p.add_argument("--message", default=os.environ.get("WM_MESSAGE", ""))
    p.add_argument("--key", default=os.environ.get("WM_KEY", ""))
    p.add_argument(
        "--num-nets",
        type=int,
        default=int(os.environ.get("WM_RT_NUM_NETS", "100")),
    )
    p.add_argument(
        "--spacing-um",
        type=float,
        default=float(os.environ.get("WM_RT_SPACING_UM", "0.09")),
    )
    p.add_argument(
        "--ndr-name",
        default=os.environ.get("WM_RT_NDR_NAME", rwc.WM_NDR_NAME_DEFAULT),
    )
    p.add_argument(
        "--target-layers",
        default=os.environ.get("WM_RT_TARGET_LAYERS", "metal2,metal3"),
    )
    p.add_argument(
        "--p-null",
        type=float,
        default=float(os.environ.get("WM_RT_P_NULL", str(P_NULL_ACCIDENTAL_NDR))),
        help="Per-net P(satisfied by chance) for Pc (default very small).",
    )
    args = p.parse_args(rwc.argv_after_openroad_driver())

    if not args.input:
        p.error("--input (or WM_RT_VERIFY_INPUT) is required")
    if not args.message or not args.key:
        p.error("--message and --key (or WM_MESSAGE / WM_KEY) are required")

    target_layers = tuple(
        s.strip() for s in args.target_layers.split(",") if s.strip()
    )

    tech = Tech()
    design = Design(tech)
    design.readDb(args.input)
    block = design.getBlock()
    if block is None:
        print("[route_watermark_verify] No block in database", file=sys.stderr)
        return 1

    otech = block.getTech()
    expected_spacing_dbu = int(design.micronToDBU(args.spacing_um))

    nets = rwc.collect_signal_nets(block)
    chosen, _rng = rwc.watermark_net_selection(
        args.key, args.message, nets, args.num_nets
    )

    rows = []
    fail = 0
    for net in chosen:
        ok, reason, sm2, sm3, has_rule = _verify_net(
            net,
            otech,
            args.ndr_name,
            expected_spacing_dbu,
            target_layers,
        )
        if not ok:
            fail += 1
        rows.append(
            [
                net.getName(),
                "1" if ok else "0",
                reason,
                sm2 if sm2 is not None else "",
                sm3 if sm3 is not None else "",
                "1" if has_rule else "0",
            ]
        )

    sat = len(chosen) - fail
    pc = rwc.binomial_pc(len(chosen), fail, p=args.p_null)

    if args.output_csv:
        csv_dir = os.path.dirname(os.path.abspath(args.output_csv))
        if csv_dir:
            os.makedirs(csv_dir, exist_ok=True)
        with open(args.output_csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "net_name",
                    "ok",
                    "reason",
                    "ndr_spacing_m2_dbu",
                    "ndr_spacing_m3_dbu",
                    "wire_has_rule_opcode",
                ]
            )
            w.writerows(rows)
        print(f"[route_watermark_verify] report -> {args.output_csv}")

    print(
        f"[route_watermark_verify] eligible_nets={len(nets)} watermark_nets={len(chosen)} "
        f"ok={sat} fail={fail} Pc<={pc:.3e} (p_null={args.p_null:.3e})"
    )
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
