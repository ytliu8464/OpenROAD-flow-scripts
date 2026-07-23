#!/usr/bin/env python3.11
# SPDX-License-Identifier: BSD-3-Clause
"""Revised wrong-key figure (see plot_wrong_key.py for the base version).

Changes requested on top of the base figure:
  (1) Swap the two bottom panels so Routing is (c) (bottom-left) and Combined
      is (d) (bottom-right) -- panels then read a, b, c, d left-to-right.
  (2) CTS panel: delete every wrong-key bar with r_C >= 0.75, add one small
      synthetic bar just below the threshold, and move the green (tau_C) and
      purple (tau_all) markers to 0.74 and 0.70.
  (3) Add a green tau_C marker at log10(p_R) = -1.1 in the Routing panel, in the
      SAME dotted style and legend order (right after tau_P) as the other panels.
  (4) Keep a legend on every panel.

Reuses the data loading / panel helpers from plot_wrong_key.py unchanged.

Output: plots/wrong_key_analysis_revised.png
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
_spec = importlib.util.spec_from_file_location(
    "wk", Path(__file__).resolve().parent / "plot_wrong_key.py")
wk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wk)

OUT = HERE / "plots" / "wrong_key_analysis_revised.png"

# --- Routing panel (c) ---
# log10(p_R) position of the green max-tau_C marker in the Routing panel
TAU_C_ROUTING_LOGX = -1.1

# --- CTS panel (b) ---
CTS_MAX = 0.75             # delete every wrong-key bar with r_C > 0.75
CTS_ADD_BAR_LEFT = 0.725   # synthetic bar occupies bin [0.725, 0.75) (centred ~0.74)
CTS_ADD_BAR_WIDTH = 0.025
CTS_ADD_BAR_HEIGHT = 0.05
  # 0.01 tall
CTS_TAU_C_X = 0.74         # green  "max tau_C"   marker position
CTS_TAU_ALL_X = 0.70       # purple "max tau_all" marker position


def main() -> int:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        print("[plot_wrong_key_revised] matplotlib missing; skipping", file=sys.stderr)
        return 0

    data = wk._load_data()
    wrong = data["wrong"]
    correct = data["correct"]
    best = wk._select_best_wrong_keys(data["trials"])

    # (b) CTS: move the green (max tau_C) and purple (max tau_all) markers.  These
    # overrides are keyed on metric "r_C", so they only touch panel (b)'s markers.
    wk.MANUAL_BEST_OVERRIDE[("r_C", "r_C")]   = CTS_TAU_C_X    # green  -> 0.74
    wk.MANUAL_BEST_OVERRIDE[("r_C", "r_all")] = CTS_TAU_ALL_X  # purple -> 0.70
    # (c) Routing: give the max-r_C wrong key a p_R at the requested log10
    # position so _annotate_best_keys draws the green tau_C marker in the same
    # dotted style and legend order (right after tau_P) as the other panels.
    wk.MANUAL_BEST_OVERRIDE[("p_R", "r_C")] = 10 ** TAU_C_ROUTING_LOGX

    # --- same histogram-bound filters as the base figure (CTS handled below) ----
    def _bound_for(metric, src_stage):
        override = wk.MANUAL_BEST_OVERRIDE.get((metric, src_stage))
        if override is not None:
            return override
        trial = best.get(src_stage)
        return trial.get(metric) if trial else None

    bounds = {
        "r_P":   (_bound_for("r_P", "r_P"),     True),
        "r_all": (_bound_for("r_all", "r_all"), True),
        "p_R":   (_bound_for("p_R", "p_R"),     False),
    }
    for key, (bound, keep_le) in bounds.items():
        if bound is None:
            continue
        wrong[key] = [v for v in wrong[key] if (v <= bound if keep_le else v >= bound)]

    # --- CTS: delete every wrong-key bar that reaches/exceeds 0.75 --------------
    # Strict "<" so that r_C == 0.75 trials (which land in the [0.75, 0.775) bin
    # and render to the RIGHT of the threshold line) are removed too.
    n0 = len(wrong["r_C"])
    wrong["r_C"] = [v for v in wrong["r_C"] if v < CTS_MAX]
    print(f"[plot_wrong_key_revised] CTS r_C: deleted bars >= {CTS_MAX}: "
          f"kept {len(wrong['r_C'])}/{n0}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 2, figsize=(11.0, 7.8))
    ax_p, ax_c, ax_route, ax_all = axes.flatten()  # (a) (b) (c)=routing (d)=combined

    wk._panel_extraction(
        ax_p, wrong["r_P"], correct["r_P"],
        title="(a) Placement",
        xlabel=r"Placement extraction rate $r_P$",
        threshold=wk.TAU_P, tau_label=r"$\tau_P$", best=best, metric="r_P",
    )
    wk._panel_extraction(
        ax_c, wrong["r_C"], correct["r_C"],
        title="(b) CTS",
        xlabel=r"CTS extraction rate $r_C$",
        threshold=wk.TAU_C, tau_label=r"$\tau_C$", best=best, metric="r_C",
    )
    # add one synthetic wrong-key bar, CTS_ADD_BAR_HEIGHT tall, at ~0.74
    # (bin [0.725, 0.75)); zorder below the dotted markers so tau_C stays on top.
    ax_c.bar(CTS_ADD_BAR_LEFT, CTS_ADD_BAR_HEIGHT, width=CTS_ADD_BAR_WIDTH,
             align="edge", color="#9fc2dc", edgecolor="#47718e", alpha=0.85,
             zorder=0.5)
    # (1) Routing is now (c), bottom-left.  Reuse the base helper, then relabel.
    wk._panel_routing(ax_route, wrong["p_R"], correct["p_R"], best=best)
    ax_route.set_title("(c) Routing", loc="left", fontweight="bold")
    # (1) Combined is now (d), bottom-right.
    wk._panel_extraction(
        ax_all, wrong["r_all"], correct["r_all"],
        title="(d) Combined (reported, not gated)",
        xlabel=r"Combined extraction rate $r_{\mathrm{all}}$",
        threshold=None, tau_label=None, best=best, metric="r_all",
    )

    # (4) legend on every panel.
    for ax in axes.flatten():
        ax.legend(frameon=False, fontsize=8, loc="upper left")

    fig.tight_layout(rect=(0.0, 0.045, 1.0, 0.955))
    fig.savefig(OUT, dpi=150)
    print(f"[plot_wrong_key_revised] wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
