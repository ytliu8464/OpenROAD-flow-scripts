# SPDX-License-Identifier: BSD-3-Clause
"""Blind routing attacker (openroad -python).

For q_s fraction of routable signal nets, *flip a random subset of their
segments to wrong-way* by perturbing the layer direction interpretation -- in
practice we simply increment a per-net "blind attack" tag.  Since we cannot
trivially rewrite routed wires without rerouting, we operate at the
*statistics* layer: this attacker writes a new route_counts CSV in which q_s
random nets have a fraction (default 25%) of their preferred segments
re-labelled as wrong-way.  That perturbation faithfully models the kind of
local detour an attacker would induce by inserting extra jogs.

This is intentionally a "soft" attack: it doesn't actually re-route, so PPA
costs are reported as 0; the harness records (r_P, r_C, Z_R, p_R) drift
under this perturbation.
"""

import csv
import os
import random
import sys


def main():
    in_csv = os.environ["WM_ROUTE_COUNTS_IN"]
    out_csv = os.environ["WM_ROUTE_COUNTS_OUT"]
    q_s = float(os.environ.get("ATK_QS", "0.05"))
    seed = int(os.environ.get("ATK_SEED", "1"))
    flip_frac = float(os.environ.get("ATK_FLIP_FRAC", "0.25"))
    rng = random.Random(seed)

    rows = list(csv.DictReader(open(in_csv)))
    eligible = [r for r in rows if int(r["total"]) > 0]
    n_target = int(round(q_s * len(eligible)))
    rng.shuffle(eligible)
    chosen = {r["net"] for r in eligible[:n_target]}

    with open(out_csv, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["net", "wrong_way", "total"])
        for r in rows:
            net = r["net"]
            tot = int(r["total"])
            ww  = int(r["wrong_way"])
            if net in chosen and tot > 0:
                bonus = int(round((tot - ww) * flip_frac))
                ww = min(tot, ww + bonus)
            wr.writerow([net, ww, tot])
    sys.stderr.write(f"[atk_r] perturbed {len(chosen)} of {len(eligible)} nets "
                     f"(q_s={q_s}, flip_frac={flip_frac})\n")
    return 0


sys.exit(main())
