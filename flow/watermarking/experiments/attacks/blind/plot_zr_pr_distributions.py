#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Z_R / p_R and per-net wrong-way distributions for three net groups:

  watermarked  : the keyed WM_R nets in the WATERMARKED layout (forced ~0 ww)
  removed      : the SAME WM_R nets after the surgical tag-cleared reroute
  normal       : a size-matched control of non-watermark nets (null)

Across the four NanGate45 designs.  Panels:
  (a) Z_R per group  (+ detection threshold Z=3.72 <-> p_R=1e-4)
  (b) -log10(p_R) per group (+ alpha_R=1e-4 line)
  (c) per-net wrong-way rate distributions (boxes) -- the raw signal

Output: plots/wm_zr_pr_distributions.png  (+ .csv of the group stats)
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
BASE = HERE / "results" / "nangate45"
OUTB = HERE / "results" / "phase3" / "removal_check"
OUT_PNG = HERE / "plots" / "wm_zr_pr_distributions.png"
OUT_CSV = HERE / "plots" / "wm_zr_pr_distributions.csv"

import sys
sys.path.insert(0, str(HERE))
from lib.route_stat import read_counts_csv, read_watermark_nets, route_stat_from_counts

DESIGNS = [("jpeg", "JPEG"), ("swerv_wrapper", "SweRV"),
           ("ariane136", "Ariane"), ("bp_multi", "BP")]
GROUPS = ["watermarked", "removed", "normal"]
COLORS = {"watermarked": "#E45756", "removed": "#54A24B", "normal": "#9AA0A6"}
Z_THRESH = -__import__("statistics").NormalDist().inv_cdf(1e-4)  # ~3.719 -> p_R=1e-4


def _fnv(s: str) -> float:
    h = 2166136261
    for b in s.encode():
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h / 2**32


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    stats = {}          # (design,group) -> RouteStat
    perr = {}           # (design,group) -> list of per-net wrong-way rates
    for nick, lbl in DESIGNS:
        wm = read_watermark_nets(OUTB / nick / "wm_nets.txt")
        base = read_counts_csv(BASE / nick / "pdmarks-all-stage" / "route_counts_5_route.csv")
        clr = read_counts_csv(OUTB / nick / "route_counts_surgical_cleared.csv")
        # size-matched normal control: non-wm nets (t>0), keyed pick to |wm|
        nonwm = [(n, w, t) for n, (w, t) in base.items() if t > 0 and n not in wm]
        nonwm.sort(key=lambda x: _fnv(x[0]))
        k = sum(1 for n, (w, t) in base.items() if t > 0 and n in wm)
        control = {n for n, _, _ in nonwm[:k]}
        stats[(lbl, "watermarked")] = route_stat_from_counts(base, wm)
        stats[(lbl, "removed")]     = route_stat_from_counts(clr, wm)
        stats[(lbl, "normal")]      = route_stat_from_counts(base, control)
        perr[(lbl, "watermarked")] = [w / t for n, (w, t) in base.items() if t > 0 and n in wm]
        perr[(lbl, "removed")]     = [w / t for n, (w, t) in clr.items()  if t > 0 and n in wm]
        perr[(lbl, "normal")]      = [w / t for n, w, t in nonwm]

    # ---- CSV ----
    with open(OUT_CSV, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["design", "group", "n_nets", "wrongway_WM", "wrongway_bg",
                     "Z_R", "p_R"])
        for nick, lbl in DESIGNS:
            for g in GROUPS:
                s = stats[(lbl, g)]
                wr.writerow([lbl, g, s.n_selected, f"{s.p_hat_1:.4f}",
                             f"{s.p_hat_0:.4f}", f"{s.Z_R:.3f}", f"{s.p_R:.3e}"])

    labels = [lbl for _, lbl in DESIGNS]
    x = np.arange(len(labels))
    w = 0.26
    plt.rcParams.update({"font.size": 13, "axes.labelsize": 15,
                         "xtick.labelsize": 13, "ytick.labelsize": 12,
                         "legend.fontsize": 12})
    fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(18.0, 5.6))

    # (a) Z_R
    for gi, g in enumerate(GROUPS):
        vals = [stats[(lbl, g)].Z_R for lbl in labels]
        a1.bar(x + (gi - 1) * w, vals, w, color=COLORS[g], label=g,
               edgecolor="white", linewidth=0.7)
    a1.axhline(Z_THRESH, ls="--", color="0.35", lw=1.2)
    a1.text(len(labels) - 0.5, Z_THRESH + 1.5, r"detect thresh $Z{=}3.72$",
            ha="right", fontsize=10, color="0.35")
    a1.axhline(0, color="0.5", lw=0.8)
    a1.set_xticks(x); a1.set_xticklabels(labels)
    a1.set_ylabel(r"$Z_R$")
    a1.set_title("(a)  routing statistic $Z_R$", fontsize=14, fontweight="bold")
    a1.legend(frameon=False)
    a1.grid(axis="y", color="0.92", lw=0.7)

    # (b) -log10(p_R)  (clamped)
    for gi, g in enumerate(GROUPS):
        vals = [-math.log10(max(stats[(lbl, g)].p_R, 1e-250)) for lbl in labels]
        a2.bar(x + (gi - 1) * w, vals, w, color=COLORS[g], label=g,
               edgecolor="white", linewidth=0.7)
    a2.axhline(4.0, ls="--", color="0.35", lw=1.2)
    a2.text(len(labels) - 0.5, 8, r"$\alpha_R{=}10^{-4}$", ha="right",
            fontsize=10, color="0.35")
    a2.set_xticks(x); a2.set_xticklabels(labels)
    a2.set_ylabel(r"$-\log_{10}\, p_R$   (higher = more significant)")
    a2.set_title("(b)  significance $p_R$", fontsize=14, fontweight="bold")
    a2.grid(axis="y", color="0.92", lw=0.7)

    # (c) per-net wrong-way rate distributions (boxes)
    for gi, g in enumerate(GROUPS):
        data = [np.array(perr[(lbl, g)]) * 100 for lbl in labels]
        pos = x + (gi - 1) * w
        bp = a3.boxplot(data, positions=pos, widths=w * 0.9, patch_artist=True,
                        showfliers=False, medianprops=dict(color="black", lw=1.2))
        for box in bp["boxes"]:
            box.set(facecolor=COLORS[g], alpha=0.85, edgecolor="white")
    a3.set_xticks(x); a3.set_xticklabels(labels)
    a3.set_ylabel("per-net wrong-way rate (%)")
    a3.set_title("(c)  per-net wrong-way distribution", fontsize=14, fontweight="bold")
    a3.grid(axis="y", color="0.92", lw=0.7)
    handles = [plt.Rectangle((0, 0), 1, 1, color=COLORS[g]) for g in GROUPS]
    a3.legend(handles, GROUPS, frameon=False)

    fig.suptitle("Routing-watermark statistic across net groups "
                 "(watermarked → removed by surgical tag-cleared reroute ≈ normal)",
                 fontsize=15, fontweight="bold", y=1.02)
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=150, bbox_inches="tight")
    print(f"[wrote] {OUT_PNG}\n[wrote] {OUT_CSV}\n")
    print(f"{'design':<8}{'group':<13}{'wwWM%':>7}{'wwBG%':>7}{'Z_R':>8}{'p_R':>11}")
    for nick, lbl in DESIGNS:
        for g in GROUPS:
            s = stats[(lbl, g)]
            print(f"{lbl:<8}{g:<13}{s.p_hat_1*100:>6.1f}%{s.p_hat_0*100:>6.1f}%"
                  f"{s.Z_R:>8.1f}{s.p_R:>11.1e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
