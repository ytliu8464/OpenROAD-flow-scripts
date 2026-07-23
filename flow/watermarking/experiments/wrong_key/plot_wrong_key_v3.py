#!/usr/bin/env python3.12
# SPDX-License-Identifier: BSD-3-Clause
"""wrong_key_analysis_v3.png -- identical to the committed wrong_key_analysis.png
(same segment-level data, same panels a/b/c/d) with TWO legend-text edits only:

  1. Remove "(n=5000)" from the "Wrong keys" entry in all four panels.
  2. Relabel the routing (c) correct-key entry to "Correct key=6.2e-197"
     (the mean over the four NG45 designs, excluding AES).

Nothing else changes: the histograms, correct-key / threshold / best-key lines,
and panels (a)(b)(d) are drawn from the same phase2 data and the same overrides
as plot_wrong_key_revised.py.  Only the legend strings are rewritten.

Output: plots/wrong_key_analysis_v3.png
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
_spec = importlib.util.spec_from_file_location(
    "wk", Path(__file__).resolve().parent / "plot_wrong_key.py")
wk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wk)

OUT = HERE / "plots" / "wrong_key_analysis_v3.png"

# revised-script constants (panels a/b/c/d must match the committed figure)
CTS_MAX = 0.75
CTS_ADD_BAR_LEFT = 0.725
CTS_ADD_BAR_WIDTH = 0.025
CTS_ADD_BAR_HEIGHT = 0.05
CTS_TAU_C_X = 0.74
CTS_TAU_ALL_X = 0.70
TAU_C_ROUTING_LOGX = -1.1

ROUTING_CORRECT_LABEL = "Mean correct key=6.2e-197"  # mean over 4 NG45 designs (excl. AES)


def _fix_labels(ax, is_routing):
    """Strip '(n=5000)' from the wrong-key entry; relabel the routing correct
    key.  Rewrites the legend using the existing handles so only text changes."""
    handles, labels = ax.get_legend_handles_labels()
    new = []
    for lab in labels:
        if lab.startswith("Wrong keys"):
            new.append("Wrong keys")
        elif is_routing and lab.startswith("Correct key="):
            new.append(ROUTING_CORRECT_LABEL)
        else:
            new.append(lab)
    ax.legend(handles, new, frameon=False, fontsize=8, loc="upper left")


def main() -> int:
    data = wk._load_data()
    wrong = data["wrong"]
    correct = data["correct"]
    best = wk._select_best_wrong_keys(data["trials"])

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
    wrong["r_C"] = [v for v in wrong["r_C"] if v < CTS_MAX]

    OUT.parent.mkdir(parents=True, exist_ok=True)
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
    wk._panel_routing(ax_route, wrong["p_R"], correct["p_R"], best=best)
    ax_route.set_title("(c) Routing", loc="left", fontweight="bold")
    wk._panel_extraction(ax_all, wrong["r_all"], correct["r_all"],
                         title="(d) Combined (reported, not gated)",
                         xlabel=r"Combined extraction rate $r_{\mathrm{all}}$",
                         threshold=None, tau_label=None, best=best, metric="r_all")

    # legend: strip n=5000 everywhere; relabel routing correct key
    for ax in (ax_p, ax_c, ax_all):
        _fix_labels(ax, is_routing=False)
    _fix_labels(ax_route, is_routing=True)

    fig.tight_layout(rect=(0.0, 0.045, 1.0, 0.955))
    fig.savefig(OUT, dpi=150)
    print(f"[plot_wrong_key_v3] wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
