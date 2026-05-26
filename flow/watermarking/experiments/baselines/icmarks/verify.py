#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""ICMarks baseline -- verify.

Per selected cell, re-read the parity along the embedded axis (x or y) and
compare against the recorded target_bit. Also report region-containment of
each selected cell as an auxiliary metric (logged, not folded into Pc).

Run as:
  openroad -python -exit verify.py \\
      --odb /path/to/N_stage.odb \\
      --embed-csv /path/to/icmarks_embed.csv \\
      --stage DRT
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path
from typing import List

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent.parent))

from _common import pc_stage  # noqa: E402

_T0 = time.time()


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')} +{time.time()-_T0:6.1f}s] [icmarks:verify] {msg}", flush=True)


def _site_width(block) -> int:
    for row in block.getRows():
        s = row.getSite()
        if s is not None:
            return s.getWidth()
    raise RuntimeError("no rows")


def _row_height(block) -> int:
    for row in block.getRows():
        s = row.getSite()
        if s is not None:
            return s.getHeight()
    raise RuntimeError("no rows")


def _core_xmin(block) -> int:
    return block.getCoreArea().xMin()


def _parse_args(argv: List[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="ICMarks verifier")
    ap.add_argument("--odb",       required=True)
    ap.add_argument("--embed-csv", required=True)
    ap.add_argument("--stage",     default="unknown")
    ap.add_argument("--out-csv",   default="")
    return ap.parse_args(argv)


def main(argv: List[str]) -> None:
    args = _parse_args(argv)

    embed: dict[str, dict] = {}
    with open(args.embed_csv) as f:
        for row in csv.DictReader(f):
            if row.get("satisfied", "True") in ("True", "true", "1"):
                embed[row["inst"]] = row
    if not embed:
        _log("no satisfied entries in embed CSV -- nothing to verify")
        return

    K = len(embed)
    _log(f"embed manifest: K={K}  stage={args.stage}")

    from openroad import Design, Tech
    tech = Tech()
    design = Design(tech)
    design.readDb(args.odb)
    block = design.getBlock()

    site_w = _site_width(block)
    row_h = _row_height(block)
    cx_min = _core_xmin(block)

    name_to_inst = {inst.getName(): inst for inst in block.getInsts()}

    results: List[dict] = []
    miss = 0
    in_region = 0

    for inst_name, erow in embed.items():
        axis = int(erow["axis"])
        tbit = int(erow["target_bit"])
        rx0 = int(erow["rw_x0"]); ry0 = int(erow["rw_y0"])
        rx1 = int(erow["rw_x1"]); ry1 = int(erow["rw_y1"])

        inst = name_to_inst.get(inst_name)
        if inst is None:
            miss += 1
            results.append({
                "inst": inst_name, "axis": axis, "target_bit": tbit,
                "cur_bit": -1, "match": False, "in_region": False, "note": "not_found",
            })
            continue
        ox, oy = inst.getOrigin()
        bb = inst.getBBox()
        cx = (bb.xMin() + bb.xMax()) // 2
        cy = (bb.yMin() + bb.yMax()) // 2
        contained = rx0 <= cx < rx1 and ry0 <= cy < ry1
        if contained:
            in_region += 1

        if axis == 0:
            cur_bit = ((ox - cx_min) // site_w) & 1
        else:
            cur_bit = (oy // row_h) & 1

        match = (cur_bit == tbit)
        if not match:
            miss += 1
        results.append({
            "inst": inst_name, "axis": axis, "target_bit": tbit,
            "cur_bit": cur_bit, "match": match, "in_region": contained, "note": "",
        })

    accepted = K - miss
    pc = pc_stage(K, K - accepted, 0.5)
    _log(f"stage={args.stage}  accepted={accepted}/{K}  Pc={pc:.2e}  in_region={in_region}/{K}")

    out_csv = args.out_csv or str(Path(args.odb).parent / f"icmarks_verify_{args.stage}.csv")
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["inst", "axis", "target_bit", "cur_bit", "match", "in_region", "note"])
        w.writeheader()
        w.writerows(results)
    _log(f"verify CSV -> {out_csv}")

    print(f"ICMARKS_VERIFY stage={args.stage}: K={K} accepted={accepted} Pc={pc:.4e} in_region={in_region}", flush=True)


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    main(argv)
