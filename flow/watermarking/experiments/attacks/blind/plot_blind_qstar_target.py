#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""PLANNING-TARGET overlay for the blind-attack q* figure.

This is NOT a results figure. It plots the *measured* extraction curves
(identical to plot_blind_qstar.py) and overlays the q* >= 0.3 robustness
goal we want the ASAP7 parameter-tuning experiment to reach. The improved
curves themselves are deliberately NOT drawn -- they do not exist until the
re-embed + re-attack experiment produces them. Output is banner-marked so it
cannot be mistaken for data.

Output: plots/blind_qstar_target.png
"""

from __future__ import annotations

import sys
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
SRC = HERE / "attacks" / "blind" / "plot_blind_qstar.py"
spec = importlib.util.spec_from_file_location("pbq", SRC)
pbq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pbq)

OUT_PNG = HERE / "plots" / "blind_qstar_target.png"
TARGET_QSTAR = 0.3   # robustness goal for the ASAP7 tuning experiment


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    curves = {}
    for plat, design, label in pbq.DESIGNS:
        rows = pbq._load_curve(plat, design)
        if rows:
            curves[(plat, design)] = rows

    fig, axes = plt.subplots(2, 4, figsize=(15.0, 6.9), sharex=True, sharey=True)
    for col, (plat, design, label) in enumerate(pbq.DESIGNS):
        is_asap7 = (plat == "asap7")
        ax = axes[1 if is_asap7 else 0][col % 4]
        rows = curves.get((plat, design))
        if not rows:
            ax.set_title(f"{label} (no data)", fontsize=9)
            continue
        qs = [d["q"] for d in rows]
        ax.plot(qs, [d["r_P"] for d in rows], "-o", ms=3, color="#e67e22", label=r"$r_P$ (measured)")
        ax.plot(qs, [d["r_C"] for d in rows], "-s", ms=3, color="#27ae60", label=r"$r_C$ (measured)")
        ax.plot(qs, [d["r_all"] for d in rows], "-^", ms=3, color="#2c3e50", lw=2.0,
                label=r"$r_{\mathrm{all}}$ (measured)")
        ax.axhline(pbq.TAU_P, ls="--", lw=1.0, color="0.4")

        # measured q* (solid red)
        qs_star = pbq._qstar(rows)
        if qs_star is not None:
            ax.axvline(qs_star, ls="-", lw=1.4, color="#c0392b", alpha=0.7)
            ax.text(qs_star, 0.06, rf"meas. $q^*={qs_star:g}$", color="#c0392b",
                    fontsize=7.5, ha="center",
                    bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.85))

        # robustness GOAL: q* should reach >= TARGET_QSTAR (only meaningful where
        # it is not already met, i.e. the ASAP7 / low-q* designs)
        if is_asap7:
            ax.axvspan(0.0, TARGET_QSTAR, color="#2ecc71", alpha=0.10)
            ax.axvline(TARGET_QSTAR, ls=(0, (4, 2)), lw=1.6, color="#1e8449")
            ax.text(TARGET_QSTAR + 0.015, 0.90, rf"goal $q^*\geq{TARGET_QSTAR:g}$",
                    color="#1e8449", fontsize=7.5, ha="left", fontweight="bold")
            if qs_star is not None and qs_star < TARGET_QSTAR:
                ax.annotate("", xy=(TARGET_QSTAR, 0.5), xytext=(qs_star, 0.5),
                            arrowprops=dict(arrowstyle="->", color="#1e8449", lw=1.4))

        ax.set_title(label, fontsize=10, fontweight="bold")
        ax.set_xlim(0.0, 1.02)
        ax.set_ylim(0.0, 1.05)
        ax.grid(color="0.92", lw=0.6)
        if col % 4 == 0:
            ax.set_ylabel("extraction rate")
        if is_asap7:
            ax.set_xlabel(r"perturbation fraction $q_s$")

    handles, labels = axes[0][0].get_legend_handles_labels()
    handles += [plt.Line2D([], [], ls="--", color="0.4"),
                plt.Line2D([], [], ls=(0, (4, 2)), color="#1e8449")]
    labels += [rf"$\tau_P=\tau_C={pbq.TAU_P:g}$ (gate)",
               rf"$q^*\geq{TARGET_QSTAR:g}$ goal (ASAP7)"]
    fig.legend(handles, labels, ncol=5, frameon=False, loc="lower center",
               bbox_to_anchor=(0.5, -0.02), fontsize=9)

    fig.suptitle(
        "PLANNING TARGET — NOT MEASURED RESULTS.  Solid curves are measured data; "
        "the green goal marks the q*≥0.3 robustness objective for the ASAP7 "
        "parameter-tuning experiment.\nThe improved curves do not exist yet and are "
        "intentionally not drawn — they must come from the re-embed + re-attack run.",
        fontsize=10.5, color="#7b241c", fontweight="bold", y=1.02)

    fig.tight_layout(rect=(0, 0.04, 1, 0.93))
    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PNG, dpi=150, bbox_inches="tight")
    print(f"[target] wrote {OUT_PNG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
