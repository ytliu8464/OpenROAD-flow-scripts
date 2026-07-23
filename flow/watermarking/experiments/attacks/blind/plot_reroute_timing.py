#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Matched-thread reroute runtime: surgical (watermark nets) vs full (all nets).

Stacked bars decompose each run's wall-clock into:
    pin access   -- design-wide FlexPA (identical in both -> the floor)
    detail route -- FlexDR maze+search-repair (the only part that shrinks)
    other        -- read_db + track-assign + global-route + write (wall - pa - dr)

Shows why the surgical speedup is only ~2-3x (not proportional to net count):
the design-wide floor is paid regardless.

Input:  plots/../results/phase3/reroute_timing/reroute_timing_summary.csv
Output: plots/reroute_timing.png
"""
from __future__ import annotations

import csv
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
SRC = HERE / "results" / "phase3" / "reroute_timing" / "reroute_timing_summary.csv"
OUT = HERE / "plots" / "reroute_timing.png"
OUT_CSV = HERE / "plots" / "reroute_timing.csv"

LABELS = {"jpeg": "JPEG", "swerv_wrapper": "SweRV",
          "ariane136": "Ariane", "bp_multi": "BP"}
ORDER = ["jpeg", "swerv_wrapper", "ariane136", "bp_multi"]
# segment colours (standard, muted); pin-access = the constant floor
C_PA, C_DR, C_OT = "#4C78A8", "#E45756", "#B0B0B0"


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    rows = {}
    for r in csv.DictReader(open(SRC)):
        rows[(r["design"], r["mode"])] = r

    # --- CSV matching the figure (segment decomposition + speedup) ----------
    with open(OUT_CSV, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["design", "mode", "threads", "reroute_nets", "wall_s",
                     "pinaccess_s", "detailroute_s", "other_s", "full_over_surgical"])
        for d in ORDER:
            surg_wall = float(rows[(d, "surgical")]["wall_s"])
            for mode in ("surgical", "full"):
                r = rows[(d, mode)]
                pa = float(r["pinaccess_s"]); dr = float(r["detailroute_s"])
                wall = float(r["wall_s"]); ot = max(0.0, wall - pa - dr)
                ratio = float(rows[(d, "full")]["wall_s"]) / surg_wall
                wr.writerow([LABELS[d], mode, r["threads"], r["reroute_nets"],
                             f"{wall:.1f}", f"{pa:.1f}", f"{dr:.1f}", f"{ot:.1f}",
                             f"{ratio:.2f}"])
    print(f"[wrote] {OUT_CSV}")

    plt.rcParams.update({"font.size": 15, "axes.labelsize": 18,
                         "xtick.labelsize": 15, "ytick.labelsize": 14,
                         "legend.fontsize": 14})
    fig, ax = plt.subplots(figsize=(12.5, 7.0))

    modes = ["surgical", "full"]
    w = 0.36
    x = np.arange(len(ORDER))
    for mi, mode in enumerate(modes):
        pa = [float(rows[(d, mode)]["pinaccess_s"]) for d in ORDER]
        dr = [float(rows[(d, mode)]["detailroute_s"]) for d in ORDER]
        wall = [float(rows[(d, mode)]["wall_s"]) for d in ORDER]
        ot = [max(0.0, wall[i] - pa[i] - dr[i]) for i in range(len(ORDER))]
        xoff = x + (mi - 0.5) * (w + 0.03)
        b1 = ax.bar(xoff, pa, w, color=C_PA,
                    label="pin access (FlexPA, design-wide)" if mi == 0 else None)
        b2 = ax.bar(xoff, dr, w, bottom=pa, color=C_DR,
                    label="detail route (FlexDR)" if mi == 0 else None)
        b3 = ax.bar(xoff, ot, w, bottom=[pa[i] + dr[i] for i in range(len(ORDER))],
                    color=C_OT, label="other (read+track-assign+GRT+write)" if mi == 0 else None)
        # total + mode label on top
        for i in range(len(ORDER)):
            ax.annotate(f"{wall[i]:.0f}s", (xoff[i], wall[i]), ha="center",
                        va="bottom", fontsize=12.5, fontweight="bold",
                        color="#333")
            ax.annotate(mode, (xoff[i], -0.02 * max(wall)), ha="center",
                        va="top", fontsize=11, color="#555", rotation=0)

    # speedup annotation per design
    for i, d in enumerate(ORDER):
        s = float(rows[(d, "surgical")]["wall_s"])
        f = float(rows[(d, "full")]["wall_s"])
        ax.annotate(rf"$\times${f/s:.1f}", (x[i], max(s, f) * 1.10),
                    ha="center", va="bottom", fontsize=14, fontweight="bold",
                    color="#1B7A3D")

    ax.set_xticks(x)
    ax.set_xticklabels([LABELS[d] for d in ORDER], fontsize=15)
    ax.set_ylabel("wall-clock (s)  @ 8 threads")
    ax.set_ylim(0, max(float(rows[(d, "full")]["wall_s"]) for d in ORDER) * 1.22)
    ax.set_title("Reroute runtime at matched threads: watermark-only (surgical) vs full\n"
                 r"speedup is only $\sim$2–3$\times$ — pin access is a design-wide floor paid by both",
                 fontsize=15, fontweight="bold")
    ax.legend(loc="upper left", frameon=False)
    ax.grid(axis="y", color="0.9", lw=0.7)
    ax.margins(x=0.02)
    fig.tight_layout()
    fig.savefig(OUT, dpi=150)
    print(f"[wrote] {OUT}")
    # console table
    print(f"\n{'design':<10}{'surg_wall':>10}{'full_wall':>10}{'ratio':>7}"
          f"{'pa(s/f)':>10}{'dr(s/f)':>12}")
    for d in ORDER:
        s = rows[(d, "surgical")]; f = rows[(d, "full")]
        print(f"{LABELS[d]:<10}{float(s['wall_s']):>10.0f}{float(f['wall_s']):>10.0f}"
              f"{float(f['wall_s'])/float(s['wall_s']):>7.2f}"
              f"{s['pinaccess_s']+'/'+f['pinaccess_s']:>10}"
              f"{s['detailroute_s']+'/'+f['detailroute_s']:>12}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
