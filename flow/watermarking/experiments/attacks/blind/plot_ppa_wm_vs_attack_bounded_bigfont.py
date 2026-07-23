#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Regenerate ppa_delta_wm_vs_attack_tns_bounded.png with larger fonts.

Same 3-panel bounded figure as plot_ppa_wm_vs_attack.py (fig4), but:
  * all fonts (title, legend, axis labels, ticks, bar value labels) are enlarged,
  * the two-line explanatory footnote in the bottom (TNS) panel is removed.

Data is read from the frozen snapshot plots/ppa_delta_wm_vs_attack.csv (the same
values that produced the original figure), so no experiment pipeline is needed.

Output (overwrites): plots/ppa_delta_wm_vs_attack_tns_bounded.png
"""
from __future__ import annotations

import csv
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
CSV_IN = HERE / "plots" / "ppa_delta_wm_vs_attack.csv"
OUT_PNG = HERE / "plots" / "ppa_delta_wm_vs_attack_tns_bounded.png"

DESIGNS = [
    ("nangate45", "jpeg",          "JPEG\n(NG45)"),
    ("nangate45", "swerv_wrapper", "SweRV\n(NG45)"),
    ("nangate45", "ariane136",     "Ariane\n(NG45)"),
    ("nangate45", "bp_multi_top",  "BP\n(NG45)"),
    ("asap7",     "jpeg",          "JPEG\n(ASAP7)"),
    ("asap7",     "swerv_wrapper", "SweRV\n(ASAP7)"),
    ("asap7",     "cva6",          "CVA6\n(ASAP7)"),
    ("asap7",     "ariane",        "Ariane\n(ASAP7)"),
]
C_WM = "#4C78A8"   # PDMarks watermark
C_ATK = "#E45756"  # blind attack

# enlarged font sizes (originals shown in comments)
FS_TITLE = 14      # was 10.5
FS_LEGEND = 13     # was 9
FS_YLABEL = 14     # was default ~10
FS_TICK = 13       # was 9 (x) / default (y)
FS_VALUE = 9.5     # was 6.5


def _load():
    data = {}
    for r in csv.DictReader(open(CSV_IN)):
        data[(r["platform"], r["design"])] = r
    return data


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    data = _load()
    rows = [data[(plat, design)] for plat, design, _ in DESIGNS]
    labels = [lbl for _, _, lbl in DESIGNS]
    x = np.arange(len(labels))
    w = 0.38

    fig, (e1, e2, e3) = plt.subplots(3, 1, figsize=(13.0, 10.0))

    def bars(ax, key_wm, key_atk, ylabel, fmt, ylim=None):
        vw = [_f(r[key_wm]) for r in rows]
        va = [_f(r[key_atk]) for r in rows]
        b1 = ax.bar(x - w / 2, vw, w, label="PDMarks (watermark overhead)", color=C_WM)
        b2 = ax.bar(x + w / 2, va, w, label="Blind attack @ $q^*$", color=C_ATK)
        ax.axhline(0, color="0.5", lw=0.8)
        ax.set_ylabel(ylabel, fontsize=FS_YLABEL)
        ax.set_xticks(x)
        ax.tick_params(axis="y", labelsize=FS_TICK)
        ax.grid(axis="y", color="0.9", lw=0.7)
        if ylim:
            ax.set_ylim(*ylim)
        for bars_ in (b1, b2):
            for rect in bars_:
                h = rect.get_height()
                yy = h
                if ylim:                    # clamp off-scale labels to the axis
                    yy = max(ylim[0], min(h, ylim[1]))
                ax.annotate(fmt(h), (rect.get_x() + rect.get_width() / 2, yy),
                            xytext=(0, 3 if h >= 0 else -12), textcoords="offset points",
                            ha="center", fontsize=FS_VALUE, color="0.25")

    # WL (%)
    bars(e1, "dWL_pdmarks_pct", "dWL_attack_pct",
         r"$\Delta$ routed wirelength (%)", lambda h: f"{h:+.2f}")
    e1.set_xticklabels([])
    e1.set_title(r"$\Delta$PPA vs un-watermarked reference: TNS as bounded symmetric change "
                 r"$(|TNS_{ref}|-|TNS_{new}|)/(|TNS_{new}|+|TNS_{ref}|)$", fontsize=FS_TITLE)
    e1.legend(frameon=False, fontsize=FS_LEGEND, loc="upper left")
    # WNS normalized by clock period (worst-path slack; no #endpoints) -- + = better.
    bars(e2, "dWNS_pdmarks_tcp_pct", "dWNS_attack_tcp_pct",
         r"$\Delta$ WNS (% of TCP, +=better)", lambda h: f"{h:+.1f}")
    e2.set_xticklabels([])
    # bounded [-1,1]; POSITIVE = better.  No symlog/clip needed -- values can't explode.
    bars(e3, "dTNS_pdmarks_sym", "dTNS_attack_sym",
         r"TNS change index ($\pm$1, +=better)", lambda h: f"{h:+.2f}", ylim=(-1.05, 1.05))
    e3.set_xticklabels(labels, fontsize=FS_TICK)
    e3.axhline(1.0, color="0.8", lw=0.7, ls=":")
    e3.axhline(-1.0, color="0.8", lw=0.7, ls=":")
    # (two-line explanatory footnote removed per request)

    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=150, bbox_inches="tight")
    print(f"[bigfont] wrote {OUT_PNG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
