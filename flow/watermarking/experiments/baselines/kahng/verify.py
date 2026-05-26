#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Kahng row-parity baseline -- verify.

For each selected cell, snap its current y-origin to the nearest row index
and compare the parity of that row index against the target_bit recorded at
embed time. Works at any post-embed stage (post-CTS, post-GRT, post-DRT).

Run as:
  openroad -python -exit verify.py \\
      --odb /path/to/N_stage.odb \\
      --embed-csv /path/to/kahng_embed.csv \\
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
    print(f"[{time.strftime('%H:%M:%S')} +{time.time()-_T0:6.1f}s] [kahng:verify] {msg}", flush=True)


def _sorted_row_bottoms(block) -> List[int]:
    rows = list(block.getRows())
    if not rows:
        raise RuntimeError("no rows in block")
    return sorted({r.getBBox().yMin() for r in rows})


def _row_index(y: int, row_bottoms: List[int]) -> int:
    best = 0
    best_d = abs(row_bottoms[0] - y)
    for i, rb in enumerate(row_bottoms[1:], start=1):
        d = abs(rb - y)
        if d < best_d:
            best = i
            best_d = d
    return best


def _parse_args(argv: List[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Kahng row-parity verifier")
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

    row_bottoms = _sorted_row_bottoms(block)
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
                "cur_row": -1, "cur_bit": -1,
                "match": False, "note": "not_found",
            })
            continue
        _, oy = inst.getOrigin()
        ridx = _row_index(oy, row_bottoms)
        cur_bit = ridx & 1
        match = (cur_bit == tbit)
        if not match:
            miss += 1
        results.append({
            "inst": inst_name, "target_bit": tbit,
            "cur_row": ridx, "cur_bit": cur_bit,
            "match": match, "note": "",
        })

    accepted = K - miss
    pc = pc_stage(K, K - accepted, 0.5)
    _log(f"stage={args.stage}  accepted={accepted}/{K}  Pc={pc:.2e}")
    if missing:
        _log(f"  {missing} instances not found in ODB (possibly removed)")

    out_csv = args.out_csv or str(Path(args.odb).parent / f"kahng_verify_{args.stage}.csv")
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["inst", "target_bit", "cur_row", "cur_bit", "match", "note"])
        w.writeheader()
        w.writerows(results)
    _log(f"verify CSV -> {out_csv}")

    print(f"KAHNG_VERIFY stage={args.stage}: K={K} accepted={accepted} Pc={pc:.4e}", flush=True)


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    main(argv)
