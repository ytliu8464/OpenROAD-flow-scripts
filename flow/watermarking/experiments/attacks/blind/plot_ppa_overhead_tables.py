#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Signed-PPA-overhead figure (reproduces fig_ppa_overhead.png from paper tables).

2x2 panels, one metric each; x-axis = watermarking methods.  For every method the
BAR is the mean over the 8 designs and the DOTS are the per-design values.  A
green half-plane marks the "better" side and a red half-plane the "worse" side.

All numbers come straight from the paper:
  * reference rWL / power  -> Table III (tab:exp_setup)
  * method deltas          -> Table V (NG45) and Table VI (ASAP7)

  (a) drWL%   = 100 * dRWL_um  / rWL_ref_um    better = down (less wirelength)
  (b) dPower% = 100 * dPower_mW / Power_ref_mW  better = down (less power)
  (c) dWNS (ns)  raw table value               better = up (more slack)
  (d) dTNS (ns)  raw table value               better = up (more slack)

Outputs:
    plots/fig_ppa_overhead_tablesVVI.png
    plots/fig_ppa_overhead_tablesVVI.csv   (per-method means)
"""
from __future__ import annotations

import csv
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
OUT_PNG = HERE / "plots" / "fig_ppa_overhead_tablesVVI.png"
OUT_CSV = HERE / "plots" / "fig_ppa_overhead_tablesVVI.csv"

METHODS = ["Row-parity", "Cell-scattering", "Buffer-insertion", "ICMarks", "PDMarks"]
# standard matplotlib tab10 palette; PDMarks = tab:red so it stands out naturally
COLORS = ["grey", "#7FB3D5", "#48A999", "#F0A04B", "indianred"]

# ---- Table III reference (rWL um, Power mW) ---------------------------------
REF = {
    ("NG45",  "JPEG"):   dict(rwl=581_986,   power=592),
    ("NG45",  "SweRV"):  dict(rwl=3_844_113, power=274),
    ("NG45",  "Ariane"): dict(rwl=7_688_658, power=254),
    ("NG45",  "BP"):     dict(rwl=3_174_283, power=141),
    ("ASAP7", "JPEG"):   dict(rwl=161_844,   power=175),
    ("ASAP7", "SweRV"):  dict(rwl=1_133_210, power=94),
    ("ASAP7", "CVA6"):   dict(rwl=667_566,   power=162),
    ("ASAP7", "Ariane"): dict(rwl=1_435_591, power=129),
}
# ---- Tables V (NG45) / VI (ASAP7): (dWNS, dTNS, drWL, dPower) per method -----
#      (key label uses the paper's method name "Kahng"; fig_ppa_overhead.png
#       labelled the same row "Row-parity".)
DELTA = {
    ("NG45", "JPEG"): {
        "Row-parity": (-0.033, -0.811, -6901, -6.193),
        "Cell-scattering": (-0.035, -0.899, -6731, -6.922),
        "Buffer-insertion": (-0.013, -2.036, -6990, -1.852),
        "ICMarks": (0.034, -0.783, -6332, -6.677),
        "PDMarks": (0.025, -0.741, -5494, -5.901),
    },
    ("NG45", "SweRV"): {
        "Row-parity": (-0.016, -6.067, 1616, -0.350),
        "Cell-scattering": (-0.029, -9.615, 1623, -0.178),
        "Buffer-insertion": (-0.022, 2.908, 1979, -0.953),
        "ICMarks": (-0.018, -17.402, 1976, -0.165),
        "PDMarks": (-0.026, 7.156, 1146, -0.161),
    },
    ("NG45", "Ariane"): {
        "Row-parity": (-0.010, -0.457, 265, 0.050),
        "Cell-scattering": (-0.025, -0.518, 1244, -0.107),
        "Buffer-insertion": (-0.032, -0.524, -969, -0.127),
        "ICMarks": (0.026, 0.517, 151, -0.020),
        "PDMarks": (0.017, 0.436, -267, -0.036),
    },
    ("NG45", "BP"): {
        "Row-parity": (-0.006, -0.006, -731, 0.001),
        "Cell-scattering": (0.000, 0.000, -568, -0.013),
        "Buffer-insertion": (-0.007, -0.007, -260, 0.003),
        "ICMarks": (0.005, 0.005, 14, -0.001),
        "PDMarks": (-0.002, -0.002, 137, 0.006),
    },
    ("ASAP7", "JPEG"): {
        "Row-parity": (0.012, 0.795, 330, 0.634),
        "Cell-scattering": (0.010, 0.768, 410, 0.546),
        "Buffer-insertion": (0.006, -0.225, 185, 0.096),
        "ICMarks": (0.009, 0.773, 854, 1.250),
        "PDMarks": (0.005, -0.053, 419, 0.431),
    },
    ("ASAP7", "SweRV"): {
        "Row-parity": (0.022, 1.832, 51, 0.048),
        "Cell-scattering": (0.028, 1.993, 130, 0.047),
        "Buffer-insertion": (0.006, 0.264, 196, 0.145),
        "ICMarks": (0.026, 1.810, 80, 0.048),
        "PDMarks": (-0.002, -0.038, 45, -0.029),
    },
    ("ASAP7", "CVA6"): {
        "Row-parity": (-0.001, -0.062, 411, 0.154),
        "Cell-scattering": (-0.035, -1.021, 302, 0.143),
        "Buffer-insertion": (-0.002, -0.116, 454, 0.039),
        "ICMarks": (-0.023, -0.751, 389, 0.155),
        "PDMarks": (0.000, 0.058, 243, 0.036),
    },
    ("ASAP7", "Ariane"): {
        "Row-parity": (0.008, 0.000, 121, 0.164),
        "Cell-scattering": (0.010, 0.000, 78, 0.175),
        "Buffer-insertion": (0.007, 0.000, 82, 0.003),
        "ICMarks": (0.003, 0.000, 145, 0.173),
        "PDMarks": (0.001, 0.000, 65, 0.003),
    },
}
DESIGNS = [
    ("NG45", "JPEG"), ("NG45", "SweRV"), ("NG45", "Ariane"), ("NG45", "BP"),
    ("ASAP7", "JPEG"), ("ASAP7", "SweRV"), ("ASAP7", "CVA6"), ("ASAP7", "Ariane"),
]


def per_design(method):
    """Return (rwl_pct[], power_pct[], wns_ns[], tns_ns[]) over the 8 designs."""
    rwl, pwr, wns, tns = [], [], [], []
    for plat, design in DESIGNS:
        r = REF[(plat, design)]
        dwns, dtns, drwl, dpow = DELTA[(plat, design)][method]
        rwl.append(100.0 * drwl / r["rwl"])
        pwr.append(100.0 * dpow / r["power"])
        wns.append(dwns)
        tns.append(dtns)
    return rwl, pwr, wns, tns


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    data = {m: per_design(m) for m in METHODS}   # m -> (rwl,pwr,wns,tns)
    rng = np.random.default_rng(0)
    x = np.arange(len(METHODS))

    GREEN, RED = "#E7F4EA", "#FDECEA"   # better / worse half-plane tints
    fmt2 = lambda v: f"{v:+.2f}"
    fmt3 = lambda v: f"{v:+.3f}"
    panels = [
        dict(idx=0, ylabel=r"$\Delta$rWL  (%)",   better="down", fmt=fmt2,
             note="↓ better\n(less wirelength)", budget=True),
        dict(idx=1, ylabel=r"$\Delta$Power  (%)",  better="down", fmt=fmt2,
             note="↓ better\n(less power)",      budget=True),
        dict(idx=2, ylabel=r"$\Delta$WNS  (ns)",   better="up", fmt=fmt3,
             note="↑ better\n(more slack)",      budget=False),
        dict(idx=3, ylabel=r"$\Delta$TNS  (ns)",   better="up", fmt=fmt2,
             note="↑ better\n(more slack)",      budget=False),
    ]
    tags = ["(a)", "(b)", "(c)", "(d)"]

    plt.rcParams.update({
        "font.size": 16, "axes.labelsize": 20, "ytick.labelsize": 16,
        "legend.fontsize": 17,
    })
    fig, axes = plt.subplots(2, 2, figsize=(14.0, 10.5))
    axf = axes.flatten()

    for p, ax, tag in zip(panels, axf, tags):
        j = p["idx"]
        series = [data[m][j] for m in METHODS]          # per-method list of 8
        means = [float(np.mean(s)) for s in series]

        lo = min(min(s) for s in series)
        hi = max(max(s) for s in series)
        span = hi - lo or 1.0
        y0, y1 = lo - 0.22 * span, hi + 0.22 * span
        if p["budget"]:                                  # keep +-1% guides visible
            y0, y1 = min(y0, -1.3), max(y1, 1.3)

        # subtle better/worse half-plane shading
        if p["better"] == "down":
            ax.axhspan(y0, 0, color=GREEN, alpha=0.7, zorder=0)
            ax.axhspan(0, y1, color=RED, alpha=0.7, zorder=0)
        else:
            ax.axhspan(0, y1, color=GREEN, alpha=0.7, zorder=0)
            ax.axhspan(y0, 0, color=RED, alpha=0.7, zorder=0)

        # bars (mean) + per-design dots, one colour per method (see legend)
        for i, m in enumerate(METHODS):
            ax.bar(i, means[i], 0.66, color=COLORS[i], edgecolor="white",
                   linewidth=1.0, zorder=2)
            jit = (rng.random(len(series[i])) - 0.5) * 0.30
            ax.scatter(i + jit, series[i], s=30, color="#444444", alpha=0.45,
                       edgecolor="white", linewidth=0.4, zorder=3)
            # mean value label -- just outside the bar end, white-backed
            lbl = p["fmt"](means[i])
            is_pd = (m == "PDMarks")
            if means[i] >= 0:
                ly, va = means[i] + 0.045 * span, "bottom"
            else:
                ly, va = means[i] - 0.045 * span, "top"
            ax.annotate(lbl, (i, ly), ha="center", va=va, zorder=5,
                        fontsize=15, fontweight="bold",
                        color="#B22222" if is_pd else "0.12",
                        bbox=dict(boxstyle="round,pad=0.15", fc="white",
                                  ec="none", alpha=0.78))

        if p["budget"]:
            ax.axhline(1.0, ls="--", lw=1.2, color="0.5")
            ax.axhline(-1.0, ls="--", lw=1.2, color="0.5")
            ax.text(len(METHODS) - 0.5, 1.03, r"$\pm$1% budget", ha="right",
                    va="bottom", fontsize=13, color="0.4")

        ax.axhline(0, color="0.2", lw=1.2, zorder=1)
        ax.set_xlim(-0.6, len(METHODS) - 0.4)
        ax.set_ylim(y0, y1)
        ax.set_ylabel(p["ylabel"])
        ax.set_xticks([])                                # methods identified by legend
        ax.grid(axis="y", color="0.9", lw=0.7, zorder=0)
        ax.annotate(p["note"], xy=(0.03, 0.97 if p["better"] == "up" else 0.03),
                    xycoords="axes fraction", ha="left",
                    va="top" if p["better"] == "up" else "bottom",
                    fontsize=14, color="#1B7A3D", style="italic")
        ax.set_title(tag, loc="left", fontsize=19, fontweight="bold")

    # single shared legend identifies the methods (no per-subplot x labels)
    import matplotlib.patches as mpatches
    handles = [mpatches.Patch(color=COLORS[i], label=METHODS[i])
               for i in range(len(METHODS))]
    fig.legend(handles=handles, loc="upper center", ncol=len(METHODS),
               frameon=False, bbox_to_anchor=(0.5, 0.955))
    fig.suptitle("Signed PPA change vs. un-watermarked reference "
                 r"(bars = mean over 8 designs, dots = per-design)",
                 fontsize=18, fontweight="bold", y=0.995)
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    fig.savefig(OUT_PNG, dpi=150)

    # per-method means CSV
    with open(OUT_CSV, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["method", "mean_dRWL_pct", "mean_dPower_pct",
                     "mean_dWNS_ns", "mean_dTNS_ns"])
        for m in METHODS:
            rwl, pwr, wns, tns = data[m]
            import numpy as _np
            wr.writerow([m, f"{_np.mean(rwl):.4f}", f"{_np.mean(pwr):.4f}",
                         f"{_np.mean(wns):.4f}", f"{_np.mean(tns):.4f}"])

    print(f"[wrote] {OUT_PNG}")
    print(f"[wrote] {OUT_CSV}")
    print(f"\n{'method':<18}{'rWL%':>8}{'Power%':>9}{'WNS ns':>9}{'TNS ns':>9}")
    for m in METHODS:
        rwl, pwr, wns, tns = data[m]
        import numpy as _np
        print(f"{m:<18}{_np.mean(rwl):>8.2f}{_np.mean(pwr):>9.2f}"
              f"{_np.mean(wns):>9.3f}{_np.mean(tns):>9.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
