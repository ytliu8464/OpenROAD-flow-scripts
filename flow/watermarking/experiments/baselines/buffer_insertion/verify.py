#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Buffer-insertion baseline -- verify phase.

Re-counts the number of BUF-master instances on each selected net and checks
whether the parity matches the target_bit from the embed CSV.
Works at any post-embed stage (post-GRT, post-DRT).

Run as:
  openroad -python -exit verify.py \\
      --odb /path/to/N_stage.odb \\
      --embed-csv /path/to/buffer_insertion_embed.csv \\
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
    print(f"[{time.strftime('%H:%M:%S')} +{time.time()-_T0:6.1f}s] [bufins:verify] {msg}", flush=True)


def _count_buf_instances(net) -> int:
    count = 0
    for iterm in net.getITerms():
        mname = iterm.getInst().getMaster().getName().upper()
        if mname.startswith("BUF"):
            count += 1
    return count


def _parse_args(argv: List[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Buffer-insertion verifier")
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
                embed[row["net"]] = row

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

    net_by_name = {net.getName(): net for net in block.getNets()}

    results: List[dict] = []
    miss = 0

    for net_name, erow in embed.items():
        tbit = int(erow["target_bit"])
        net = net_by_name.get(net_name)
        if net is None:
            # Try the split net name
            net = net_by_name.get(net_name + "__bufins__")
        if net is None:
            miss += 1
            results.append({
                "net": net_name, "target_bit": tbit,
                "n_buf": -1, "parity": -1, "match": False, "note": "not_found",
            })
            continue

        n = _count_buf_instances(net)
        par = n & 1
        match = (par == tbit)
        if not match:
            miss += 1
        results.append({
            "net": net_name, "target_bit": tbit,
            "n_buf": n, "parity": par, "match": match, "note": "",
        })

    accepted = K - miss
    pc = pc_stage(K, K - accepted, 0.5)
    _log(f"stage={args.stage}  accepted={accepted}/{K}  Pc={pc:.2e}")

    out_csv = args.out_csv or str(Path(args.odb).parent / f"buffer_insertion_verify_{args.stage}.csv")
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["net", "target_bit", "n_buf", "parity", "match", "note"])
        w.writeheader()
        w.writerows(results)
    _log(f"verify CSV -> {out_csv}")

    print(f"BUFINS_VERIFY stage={args.stage}: K={K} accepted={accepted} Pc={pc:.4e}", flush=True)


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    main(argv)
