#!/usr/bin/env python3.11
# SPDX-License-Identifier: BSD-3-Clause
"""Blind routing attacker (paper §7.1).

Picks q_s fraction of routable signal nets at random and writes their names
to ``WM_NETS_ATTACK``.  The caller (``run_blind_attack.py``) then invokes
``routing_wrong_way/run_attack_route.sh`` which loads the watermarked
4_cts.odb, tags every WM net normally, *clears* the watermark tag on the
selected attack subset, and re-runs detail_route -- so the attacked nets are
routed without the wrong-way penalty.

Env:
    WM_ROUTE_COUNTS_IN  per-net (wrong, total) CSV (used to define eligible)
    WM_NETS_ATTACK_OUT  path where the attack-net list is written
    ATK_QS              fraction (e.g. 0.10)
    ATK_SEED            attacker PRNG seed (default 1)
"""
import csv
import os
import random
import sys


def main() -> int:
    in_csv  = os.environ["WM_ROUTE_COUNTS_IN"]
    out_txt = os.environ["WM_NETS_ATTACK_OUT"]
    q_s     = float(os.environ.get("ATK_QS",   "0.05"))
    seed    = int(  os.environ.get("ATK_SEED", "1"))
    rng = random.Random(seed)

    eligible = []
    with open(in_csv) as f:
        for r in csv.DictReader(f):
            try:
                if int(r.get("total", "0")) > 0:
                    eligible.append(r["net"])
            except (TypeError, ValueError):
                continue
    if not eligible:
        sys.stderr.write(f"[atk_r] no routable nets in {in_csv}\n")
        with open(out_txt, "w"):
            pass
        return 0

    n_target = int(round(q_s * len(eligible)))
    rng.shuffle(eligible)
    chosen = eligible[:n_target]

    os.makedirs(os.path.dirname(out_txt) or ".", exist_ok=True)
    with open(out_txt, "w") as f:
        for n in chosen:
            f.write(n + "\n")
    sys.stderr.write(
        f"[atk_r] eligible={len(eligible)} target={n_target} "
        f"chosen={len(chosen)} q_s={q_s} seed={seed}\n"
    )
    return 0


sys.exit(main())
