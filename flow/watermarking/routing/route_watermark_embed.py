#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""
Embed spacing-only NDR routing watermark on selected signal nets (post-CTS ODB).

Assigns a block-scoped non-default rule with wider spacing on metal2/metal3,
then attaches it to PRNG-selected nets. Run global + detailed routing afterward.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys

from openroad import Design, Tech

import route_watermark_common as rwc


def main() -> int:
    p = argparse.ArgumentParser(description="Embed spacing NDR routing watermark")
    p.add_argument(
        "--input",
        default=os.environ.get("WM_RT_INPUT"),
        help="Input post-CTS .odb. Env: WM_RT_INPUT",
    )
    p.add_argument(
        "--output-odb",
        default=os.environ.get("WM_RT_OUTPUT_ODB"),
        help="Output .odb with NDR assigned. Env: WM_RT_OUTPUT_ODB",
    )
    p.add_argument(
        "--output-csv",
        default=os.environ.get("WM_RT_OUTPUT_CSV"),
        help="CSV list of watermark nets. Env: WM_RT_OUTPUT_CSV",
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
        help="NDR spacing on metal2/metal3 (microns). Default 0.09 for NanGate45.",
    )
    p.add_argument(
        "--ndr-name",
        default=os.environ.get("WM_RT_NDR_NAME", rwc.WM_NDR_NAME_DEFAULT),
        help=f"NDR name (default {rwc.WM_NDR_NAME_DEFAULT})",
    )
    p.add_argument(
        "--target-layers",
        default=os.environ.get("WM_RT_TARGET_LAYERS", "metal2,metal3"),
        help="Comma-separated routing layer names for extra spacing",
    )
    args = p.parse_args(rwc.argv_after_openroad_driver())

    if not args.input or not args.output_odb:
        p.error("--input and --output-odb (or WM_RT_INPUT / WM_RT_OUTPUT_ODB) are required")
    if not args.message or not args.key:
        p.error("--message and --key (or WM_MESSAGE / WM_KEY) are required")

    target_layers = tuple(
        s.strip() for s in args.target_layers.split(",") if s.strip()
    )
    if not target_layers:
        p.error("--target-layers must list at least one layer")

    tech = Tech()
    design = Design(tech)
    design.readDb(args.input)
    block = design.getBlock()
    if block is None:
        print("[route_watermark_embed] No block in database", file=sys.stderr)
        return 1

    otech = block.getTech()
    spacing_dbu = int(design.micronToDBU(args.spacing_um))

    nets = rwc.collect_signal_nets(block)
    chosen, _rng = rwc.watermark_net_selection(
        args.key, args.message, nets, args.num_nets
    )

    ndr = rwc.create_spacing_ndr(
        block, otech, spacing_dbu, args.ndr_name, target_layers
    )
    for net in chosen:
        net.setNonDefaultRule(ndr)

    if args.output_csv:
        csv_dir = os.path.dirname(os.path.abspath(args.output_csv))
        if csv_dir:
            os.makedirs(csv_dir, exist_ok=True)
        with open(args.output_csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(
                ["net_name", "num_iterms", "ndr_name", "spacing_um_m2_m3"]
            )
            for net in chosen:
                w.writerow(
                    [
                        net.getName(),
                        len(list(net.getITerms())),
                        args.ndr_name,
                        args.spacing_um,
                    ]
                )
        print(f"[route_watermark_embed] net list -> {args.output_csv}")

    design.writeDb(args.output_odb)
    print(
        f"[route_watermark_embed] nets_total_eligible={len(nets)} "
        f"watermark_nets={len(chosen)} ndr={args.ndr_name!r} "
        f"spacing_dbu_m2_m3={spacing_dbu} -> {args.output_odb}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
