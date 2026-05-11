#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Cell-scattering baseline -- verify phase.

Re-reads each cell's current column parity and compares against the original
embed CSV (cell_scattering_embed.csv).  Works at any post-embed stage
(post-CTS, post-GRT, post-DRT).

Run as:
  openroad -python -exit verify.py \\
      --odb /path/to/N_stage.odb \\
      --embed-csv /path/to/cell_scattering_embed.csv \\
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

from _common import load_seed_hex, pc_stage  # noqa: E402

_T0 = time.time()


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')} +{time.time()-_T0:6.1f}s] [cellscatter:verify] {msg}", flush=True)


def _site_width(block) -> int:
    for row in block.getRows():
        site = row.getSite()
        if site is not None:
            return site.getWidth()
    raise RuntimeError("no rows found")


def _core_bbox(block):
    bb = block.getCoreArea()
    return bb.xMin(), bb.yMin(), bb.xMax(), bb.yMax()


def _parse_args(argv: List[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Cell-scattering verifier")
    ap.add_argument("--odb",       required=True)
    ap.add_argument("--embed-csv", required=True)
    ap.add_argument("--stage",     default="unknown")
    ap.add_argument("--out-csv",   default="")
    return ap.parse_args(argv)


def main(argv: List[str]) -> None:
    args = _parse_args(argv)

    # Load embed manifest
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

    # Load ODB
    from openroad import Design, Tech
    tech = Tech()
    design = Design(tech)
    design.readDb(args.odb)
    block = design.getBlock()

    site_w = _site_width(block)
    cx_min, _, _, _ = _core_bbox(block)

    name_to_inst = {inst.getName(): inst for inst in block.getInsts()}

    results: List[dict] = []
    miss = 0
    missing = 0

    for inst_name, erow in embed.items():
        tbit = int(erow["target_bit"])
        inst = name_to_inst.get(inst_name)
        if inst is None:
            missing += 1
            miss += 1
            results.append({
                "inst": inst_name, "target_bit": tbit,
                "cur_col": -1, "cur_bit": -1,
                "match": False, "note": "not_found",
            })
            continue
        ox, _ = inst.getOrigin()
        col = (ox - cx_min) // site_w
        cur_bit = col & 1
        match = (cur_bit == tbit)
        if not match:
            miss += 1
        results.append({
            "inst": inst_name, "target_bit": tbit,
            "cur_col": col, "cur_bit": cur_bit,
            "match": match, "note": "",
        })

    accepted = K - miss
    pc = pc_stage(K, K - accepted, 0.5)
    _log(f"stage={args.stage}  accepted={accepted}/{K}  Pc={pc:.2e}")
    if missing:
        _log(f"  {missing} instances not found in ODB (possibly removed during flow)")

    if args.out_csv:
        out_csv = args.out_csv
    else:
        out_csv = str(Path(args.odb).parent / f"cell_scattering_verify_{args.stage}.csv")

    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["inst", "target_bit", "cur_col", "cur_bit", "match", "note"])
        w.writeheader()
        w.writerows(results)
    _log(f"verify CSV -> {out_csv}")

    # summary line
    print(f"CELLSCATTER_VERIFY stage={args.stage}: K={K} accepted={accepted} Pc={pc:.4e}", flush=True)


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    main(argv)
