# SPDX-License-Identifier: BSD-3-Clause
"""Blind placement attacker (openroad -python).

Randomly picks ``round(q_s * N_swappable_cells)`` cells in the placed ODB and
moves each one by one site within its row.  This perturbs ordering in a way
that targets neighbour-pair / triple-permutation watermarks without using any
knowledge of which cells are watermark cells.

Env:
    WM_ODB        input ODB (placed)
    WM_OUT_ODB    output ODB
    ATK_QS        fraction (e.g. 0.05)
    ATK_SEED      attacker PRNG seed (int)
"""

import os
import random
import sys

import odb  # type: ignore


def main():
    in_odb = os.environ["WM_ODB"]
    out_odb = os.environ["WM_OUT_ODB"]
    q_s = float(os.environ.get("ATK_QS", "0.05"))
    seed = int(os.environ.get("ATK_SEED", "1"))

    db = odb.dbDatabase.create()
    odb.read_db(db, in_odb)
    block = db.getChip().getBlock()
    rng = random.Random(seed)

    rows = list(block.getRows())
    if not rows:
        odb.write_db(db, out_odb)
        return 0
    site_w = rows[0].getSite().getWidth()
    insts = [i for i in block.getInsts() if i.getPlacementStatus() in (
        "PLACED", "FIRM", "LOCKED")]
    if not insts:
        odb.write_db(db, out_odb)
        return 0
    n_move = int(round(q_s * len(insts)))
    rng.shuffle(insts)
    moved = 0
    for inst in insts[:n_move]:
        try:
            x, y = inst.getOrigin()
        except Exception:
            continue
        dx = rng.choice((-1, 1)) * site_w
        inst.setOrigin(int(x + dx), int(y))
        moved += 1
    odb.write_db(db, out_odb)
    sys.stderr.write(f"[atk_p] moved={moved} q_s={q_s} seed={seed}\n")
    return 0


sys.exit(main())
