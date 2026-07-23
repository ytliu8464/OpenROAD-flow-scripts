#!/usr/bin/env python3.12
# SPDX-License-Identifier: BSD-3-Clause
"""Regenerate figure/wrong_key_analysis with the CORRECTED routing panel (c).

Panels (a) Placement, (b) CTS, (d) Combined are reproduced byte-for-byte from
plot_wrong_key_revised.py (same phase2 data, same overrides).  Only panel (c)
Routing is replaced with the revised net-level randomization test result:
wrong-key p_R from results/phase_route_v2/wrong_key_routing_*_dist.csv and the
exact correct-key tail (<10^-100).

To guarantee (a)(b)(d) are pixel-identical, the corrected panel (c) is
composited onto the committed original wrong_key_analysis.png (both figures
share figsize/dpi/layout, so the (c) quadrant aligns exactly).

Output: plots/wrong_key_analysis_v2.png
"""
from __future__ import annotations

import csv
import importlib.util
import math
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
_spec = importlib.util.spec_from_file_location(
    "wk", Path(__file__).resolve().parent / "plot_wrong_key.py")
wk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wk)

# revised-script constants (panels a/b/d must match exactly)
CTS_MAX = 0.75
CTS_ADD_BAR_LEFT = 0.725
CTS_ADD_BAR_WIDTH = 0.025
CTS_ADD_BAR_HEIGHT = 0.05
CTS_TAU_C_X = 0.74
CTS_TAU_ALL_X = 0.70
TAU_C_ROUTING_LOGX = -1.1

ROUTE_V2_DIST = HERE / "results" / "phase_route_v2" / \
    "wrong_key_routing_nangate45_swerv_wrapper_dist.csv"
ROUTE_V2_JSON = HERE / "results" / "phase_route_v2" / \
    "wrong_key_routing_nangate45_swerv_wrapper.json"
ORIG = Path("/home/fetzfs_projects/MISC-ytliu/watermarking/paper/"
            "69e26558ca8b3966ef49372b/figure/wrong_key_analysis.png")
OUT = HERE / "plots" / "wrong_key_analysis_v2.png"


def _panel_routing_v2(ax):
    """Routing panel (c) using the corrected net-level p_R (route_v2)."""
    rows = list(csv.DictReader(open(ROUTE_V2_DIST)))
    wrong = [float(r["p_R"]) for r in rows if r["p_R"]]
    # best wrong keys, data-driven (same categories/colors/labels as base)
    def _best(metric, mode):
        cand = [r for r in rows if r.get(metric) not in (None, "")]
        if not cand:
            return None
        pick = (max if mode == "max" else min)(
            cand, key=lambda r: float(r[metric]))
        return float(pick["p_R"])
    markers = [  # (p_R value, color, label)
        (_best("r_P", "max"),   "#e67e22", r"max $\tau_P$ wrong key"),
        (_best("r_C", "max"),   "#27ae60", r"max $\tau_C$ wrong key"),
        (_best("r_all", "max"), "#8e44ad", r"max $\tau_{\mathrm{all}}$ wrong key"),
        (min(wrong),            "#c0392b", r"min $p_R$ wrong key"),
    ]

    floor_log = -6.0
    wrong_plot = [max(math.log10(max(v, wk.P_FLOOR)), floor_log)
                  for v in wrong if v > 0]
    bins = [floor_log + i * ((0.0 - floor_log) / 36) for i in range(37)]
    ax.hist(wrong_plot, bins=bins, density=True, color="#9fc2dc",
            edgecolor="#47718e", alpha=0.85, label="Wrong keys (n=5000)")
    # correct key: exact tail below 1e-100 -> clip to the left edge
    ax.axvline(floor_log, color="#a51e36", linewidth=2.3,
               label=r"Correct key $<10^{-100}$")
    ax.axvline(math.log10(wk.ALPHA_R), color="black", linestyle="--",
               linewidth=1.5, label=rf"$\alpha_R$={wk.ALPHA_R:.0e}")
    for v, color, label in markers:
        if v is None or v <= 0:
            continue
        x = max(math.log10(max(v, wk.P_FLOOR)), floor_log)
        ax.axvline(x, color=color, linestyle=":", linewidth=1.6, alpha=0.95,
                   label=f"{label} = {v:.1e}")
    ax.set_xlim(floor_log, 0.0)
    ax.set_xticks([floor_log, -5, -4, -3, -2, -1, 0])
    ax.set_xticklabels([r"$\leq -6$", "-5", "-4", "-3", "-2", "-1", "0"])
    ax.set_title("(c) Routing", loc="left", fontweight="bold")
    ax.set_xlabel(r"$\log_{10}$ routing $p$-value $p_R$ (smaller is stronger)")
    ax.set_ylabel("Density")
    ax.grid(axis="y", color="0.9", linewidth=0.7)


def main() -> int:
    data = wk._load_data()
    wrong = data["wrong"]
    correct = data["correct"]
    best = wk._select_best_wrong_keys(data["trials"])

    # panels (a)/(b) overrides identical to plot_wrong_key_revised.py
    wk.MANUAL_BEST_OVERRIDE[("r_C", "r_C")] = CTS_TAU_C_X
    wk.MANUAL_BEST_OVERRIDE[("r_C", "r_all")] = CTS_TAU_ALL_X
    wk.MANUAL_BEST_OVERRIDE[("p_R", "r_C")] = 10 ** TAU_C_ROUTING_LOGX

    def _bound_for(metric, src_stage):
        override = wk.MANUAL_BEST_OVERRIDE.get((metric, src_stage))
        if override is not None:
            return override
        trial = best.get(src_stage)
        return trial.get(metric) if trial else None

    bounds = {"r_P": (_bound_for("r_P", "r_P"), True),
              "r_all": (_bound_for("r_all", "r_all"), True),
              "p_R": (_bound_for("p_R", "p_R"), False)}
    for key, (bound, keep_le) in bounds.items():
        if bound is None:
            continue
        wrong[key] = [v for v in wrong[key]
                      if (v <= bound if keep_le else v >= bound)]
    n0 = len(wrong["r_C"])
    wrong["r_C"] = [v for v in wrong["r_C"] if v < CTS_MAX]

    fig, axes = plt.subplots(2, 2, figsize=(11.0, 7.8))
    ax_p, ax_c, ax_route, ax_all = axes.flatten()
    wk._panel_extraction(ax_p, wrong["r_P"], correct["r_P"],
                         title="(a) Placement",
                         xlabel=r"Placement extraction rate $r_P$",
                         threshold=wk.TAU_P, tau_label=r"$\tau_P$",
                         best=best, metric="r_P")
    wk._panel_extraction(ax_c, wrong["r_C"], correct["r_C"],
                         title="(b) CTS",
                         xlabel=r"CTS extraction rate $r_C$",
                         threshold=wk.TAU_C, tau_label=r"$\tau_C$",
                         best=best, metric="r_C")
    ax_c.bar(CTS_ADD_BAR_LEFT, CTS_ADD_BAR_HEIGHT, width=CTS_ADD_BAR_WIDTH,
             align="edge", color="#9fc2dc", edgecolor="#47718e", alpha=0.85,
             zorder=0.5)
    _panel_routing_v2(ax_route)                       # <-- corrected panel (c)
    wk._panel_extraction(ax_all, wrong["r_all"], correct["r_all"],
                         title="(d) Combined (reported, not gated)",
                         xlabel=r"Combined extraction rate $r_{\mathrm{all}}$",
                         threshold=None, tau_label=None, best=best, metric="r_all")
    for ax in axes.flatten():
        ax.legend(frameon=False, fontsize=8, loc="upper left")
    fig.tight_layout(rect=(0.0, 0.045, 1.0, 0.955))
    fresh = HERE / "plots" / "_wrong_key_fresh.png"
    fig.savefig(fresh, dpi=150)
    print(f"[fixed] rendered full figure -> {fresh}")

    # --- composite (c) quadrant onto the committed original ---
    if ORIG.exists():
        base = Image.open(ORIG).convert("RGB")
        new = Image.open(fresh).convert("RGB")
        if base.size != new.size:
            print(f"[fixed] size mismatch base={base.size} new={new.size}; "
                  "saving fresh render instead")
            base = new
        else:
            W, H = base.size
            # bottom-left quadrant = panel (c); paste with a small inset so the
            # inter-panel gap (not (a)/(d) content) defines the seam.
            x0, x1 = 0, W // 2
            y0, y1 = H // 2, H
            region = new.crop((x0, y0, x1, y1))
            base.paste(region, (x0, y0))
        base.save(OUT)
        print(f"[fixed] composited corrected (c) -> {OUT}")
    else:
        fig.savefig(OUT, dpi=150)
        print(f"[fixed] original not found; wrote full figure -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
