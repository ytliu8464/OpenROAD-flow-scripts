#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Grouped bar charts comparing watermarking methods on normalized PPA deltas.

Strictly reproduces the numbers in the paper:
  * Reference values (TCP, rWL, Power)  -> Table III  (tab:exp_setup)
  * Method deltas (dWNS,dTNS,drWL,dPower) -> Table V  (NG45, tab:ppa_ng45)
                                             Table VI (ASAP7, tab:ppa_asap7)

Methods: Kahng, Cell-scattering, Buffer-insertion, ICMarks, PDMarks (all-stage).

All four metrics are expressed as percentages.  WNS and TNS use *normalized*
denominators (naive relative-% is undefined for near-closure baselines, e.g.
Ariane-ASAP7 TNS_ref = 0):
    dWNS%  = 100 * dWNS / TCP                       (% of one clock period)
    dTNS%  = 100 * dTNS / (TCP * N_endpoints)       (% of total timing budget)
    drWL%  = 100 * drWL / rWL_ref
    dPower% = 100 * dPower / Power_ref
TNS is normalized by the total timing budget TCP * N_endpoints, where
N_endpoints is the design flip-flop count (a fixed design constant, not printed
in the paper tables): finish__design__instance__count__class:sequential_cell
from each reference 6_report.json.  Dividing by the budget (always large and
positive) tames the near-closure blow-up of the naive TNS_ref-relative %.

Sign convention follows the paper (delta = watermarked - reference):
    WNS/TNS  positive => slack less negative => timing improved (better)
    rWL/Power positive => quantity increased => overhead

Outputs:
    plots/methods_ppa_normalized.csv
    plots/methods_ppa_normalized.png
"""
from __future__ import annotations

import csv
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
OUT_CSV = HERE / "plots" / "methods_ppa_normalized.csv"
OUT_PNG = HERE / "plots" / "methods_ppa_normalized.png"

METHODS = ["Kahng", "Cell-scattering", "Buffer-insertion", "ICMarks", "PDMarks"]
COLORS = {
    "Kahng":            "#4C78A8",
    "Cell-scattering":  "#F58518",
    "Buffer-insertion": "#54A24B",
    "ICMarks":          "#B279A2",
    "PDMarks":          "#E45756",   # our method (all-stage) -- emphasized
}

# ---- Table III reference values (TCP from paper; rWL um; Power mW) + FF count -
#   (platform, design): dict(tcp, rwl, power, ff).
#   ff = #endpoints (flip-flop count) from the reference 6_report.json
#        (finish__design__instance__count__class:sequential_cell).
REF = {
    ("NG45",  "JPEG"):   dict(tcp=1.00, rwl=581_986,   power=592, ff=4384),
    ("NG45",  "SweRV"):  dict(tcp=2.00, rwl=3_844_113, power=274, ff=11308),
    ("NG45",  "Ariane"): dict(tcp=3.50, rwl=7_688_658, power=254, ff=20667),
    ("NG45",  "BP"):     dict(tcp=4.80, rwl=3_174_283, power=141, ff=15813),
    ("ASAP7", "JPEG"):   dict(tcp=0.54, rwl=161_844,   power=175, ff=4384),
    ("ASAP7", "SweRV"):  dict(tcp=1.46, rwl=1_133_210, power=94,  ff=11694),
    ("ASAP7", "CVA6"):   dict(tcp=0.95, rwl=667_566,   power=162, ff=8465),
    ("ASAP7", "Ariane"): dict(tcp=1.40, rwl=1_435_591, power=129, ff=20442),
}

# ---- Tables V (NG45) and VI (ASAP7): (dWNS, dTNS, drWL, dPower) per method ----
DELTA = {
    ("NG45", "JPEG"): {
        "Kahng":            (-0.033, -0.811, -6901, -6.193),
        "Cell-scattering":  (-0.035, -0.899, -6731, -6.922),
        "Buffer-insertion": (-0.013, -2.036, -6990, -1.852),
        "ICMarks":          ( 0.034, -0.783, -6332, -6.677),
        "PDMarks":          ( 0.025, -0.741, -5494, -5.901),
    },
    ("NG45", "SweRV"): {
        "Kahng":            (-0.016,  -6.067, 1616, -0.350),
        "Cell-scattering":  (-0.029,  -9.615, 1623, -0.178),
        "Buffer-insertion": (-0.022,   2.908, 1979, -0.953),
        "ICMarks":          (-0.018, -17.402, 1976, -0.165),
        "PDMarks":          (-0.026,   7.156, 1146, -0.161),
    },
    ("NG45", "Ariane"): {
        "Kahng":            (-0.010, -0.457,  265,  0.050),
        "Cell-scattering":  (-0.025, -0.518, 1244, -0.107),
        "Buffer-insertion": (-0.032, -0.524, -969, -0.127),
        "ICMarks":          ( 0.026,  0.517,  151, -0.020),
        "PDMarks":          ( 0.017,  0.436, -267, -0.036),
    },
    ("NG45", "BP"): {
        "Kahng":            (-0.006, -0.006, -731,  0.001),
        "Cell-scattering":  ( 0.000,  0.000, -568, -0.013),
        "Buffer-insertion": (-0.007, -0.007, -260,  0.003),
        "ICMarks":          ( 0.005,  0.005,   14, -0.001),
        "PDMarks":          (-0.002, -0.002,  137,  0.006),
    },
    ("ASAP7", "JPEG"): {
        "Kahng":            (0.012,  0.795, 330, 0.634),
        "Cell-scattering":  (0.010,  0.768, 410, 0.546),
        "Buffer-insertion": (0.006, -0.225, 185, 0.096),
        "ICMarks":          (0.009,  0.773, 854, 1.250),
        "PDMarks":          (0.005, -0.053, 419, 0.431),
    },
    ("ASAP7", "SweRV"): {
        "Kahng":            ( 0.022, 1.832,  51,  0.048),
        "Cell-scattering":  ( 0.028, 1.993, 130,  0.047),
        "Buffer-insertion": ( 0.006, 0.264, 196,  0.145),
        "ICMarks":          ( 0.026, 1.810,  80,  0.048),
        "PDMarks":          (-0.002, -0.038, 45, -0.029),
    },
    ("ASAP7", "CVA6"): {
        "Kahng":            (-0.001, -0.062, 411, 0.154),
        "Cell-scattering":  (-0.035, -1.021, 302, 0.143),
        "Buffer-insertion": (-0.002, -0.116, 454, 0.039),
        "ICMarks":          (-0.023, -0.751, 389, 0.155),
        "PDMarks":          ( 0.000,  0.058, 243, 0.036),
    },
    ("ASAP7", "Ariane"): {
        "Kahng":            (0.008, 0.000, 121, 0.164),
        "Cell-scattering":  (0.010, 0.000,  78, 0.175),
        "Buffer-insertion": (0.007, 0.000,  82, 0.003),
        "ICMarks":          (0.003, 0.000, 145, 0.173),
        "PDMarks":          (0.001, 0.000,  65, 0.003),
    },
}

# x-axis order: 4 NG45 then 4 ASAP7
DESIGNS = [
    ("NG45", "JPEG"), ("NG45", "SweRV"), ("NG45", "Ariane"), ("NG45", "BP"),
    ("ASAP7", "JPEG"), ("ASAP7", "SweRV"), ("ASAP7", "CVA6"), ("ASAP7", "Ariane"),
]


def norm(plat, design, method):
    """Return (dWNS%, dTNS%, drWL%, dPower%) for one cell.

    dTNS% = 100 * dTNS / (TCP * N_endpoints)  (timing-budget norm; positive = better).
    """
    r = REF[(plat, design)]
    dwns, dtns, drwl, dpow = DELTA[(plat, design)][method]
    return (
        100.0 * dwns / r["tcp"],
        100.0 * dtns / (r["tcp"] * r["ff"]),
        100.0 * drwl / r["rwl"],
        100.0 * dpow / r["power"],
    )


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    # ---- CSV ----
    with open(OUT_CSV, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["platform", "design", "method", "TCP_ns", "N_endpoints",
                     "rWL_ref_um", "Power_ref_mW",
                     "dWNS_ns", "dTNS_ns", "drWL_um", "dPower_mW",
                     "dWNS_pct_TCP", "dTNS_pct_budget", "drWL_pct", "dPower_pct"])
        for plat, design in DESIGNS:
            r = REF[(plat, design)]
            for m in METHODS:
                dwns, dtns, drwl, dpow = DELTA[(plat, design)][m]
                pw, pt, pr, pp = norm(plat, design, m)
                wr.writerow([plat, design, m, r["tcp"], r["ff"], r["rwl"], r["power"],
                             dwns, dtns, drwl, dpow,
                             f"{pw:.4f}", f"{pt:.5f}", f"{pr:.4f}", f"{pp:.4f}"])

    # ---- figure: 4 metric rows x (8 designs x 5 methods) grouped bars ----
    metrics = [
        (0, r"$\Delta$WNS (% of TCP)",              "+ = more slack (better)"),
        (1, r"$\Delta$TNS (% of timing budget)",    "+ = more slack (better)"),
        (2, r"$\Delta$rWL (%)",                     "+ = more wirelength"),
        (3, r"$\Delta$Power (%)",                   "+ = more power"),
    ]
    labels = [f"{d}\n{p}" for p, d in DESIGNS]
    x = np.arange(len(DESIGNS))
    w = 0.16
    offs = {m: (i - (len(METHODS) - 1) / 2) * w for i, m in enumerate(METHODS)}

    fig, axes = plt.subplots(4, 1, figsize=(13.0, 12.0), sharex=True)
    for (mi, ylabel, signnote), ax in zip(metrics, axes):
        for m in METHODS:
            vals = [norm(p, d, m)[mi] for p, d in DESIGNS]
            ax.bar(x + offs[m], vals, w, label=m, color=COLORS[m],
                   edgecolor="black" if m == "PDMarks" else "none",
                   linewidth=0.6 if m == "PDMarks" else 0.0, zorder=3)
        ax.axhline(0, color="0.4", lw=0.8, zorder=2)
        ax.axvline(3.5, color="0.75", lw=1.0, ls="--", zorder=1)  # NG45 | ASAP7
        ax.set_ylabel(ylabel, fontsize=10)
        ax.grid(axis="y", color="0.92", lw=0.7, zorder=0)
        ax.margins(x=0.01)
        ax.annotate(signnote, xy=(0.995, 0.04), xycoords="axes fraction",
                    ha="right", va="bottom", fontsize=7.5, color="#555555",
                    style="italic")

    axes[0].legend(ncol=5, frameon=False, fontsize=9.5,
                   loc="lower center", bbox_to_anchor=(0.5, 1.02))
    # technology-node banners on the top axis
    axes[0].annotate("NanGate45", xy=(1.5, 0.90), xycoords=("data", "axes fraction"),
                     ha="center", fontsize=9, color="0.35", fontweight="bold")
    axes[0].annotate("ASAP7", xy=(5.5, 0.90), xycoords=("data", "axes fraction"),
                     ha="center", fontsize=9, color="0.35", fontweight="bold")
    axes[-1].set_xticks(x)
    axes[-1].set_xticklabels(labels, fontsize=9)
    fig.suptitle("Normalized PPA overhead of watermarking methods "
                 "(numbers from Tables III, V, VI)", fontsize=12, y=0.965)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(OUT_PNG, dpi=150)
    print(f"[wrote] {OUT_CSV}")
    print(f"[wrote] {OUT_PNG}")

    # ---- console summary: PDMarks vs the worst baseline per metric ----
    hdr = ["design"] + METHODS
    for mi, ylabel, _ in metrics:
        print(f"\n== {ylabel} ==")
        print(f"{'design':<14}" + "".join(f"{m[:9]:>11}" for m in METHODS))
        for p, d in DESIGNS:
            row = "".join(f"{norm(p, d, m)[mi]:>11.3f}" for m in METHODS)
            print(f"{d+' '+p:<14}{row}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
