#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""PPA-delta bars sourced from the hand-edited xlsx (WL + TNS only, no WNS).

Same style/legend/title as plot_ppa_wm_vs_attack.py's
``ppa_delta_wm_vs_attack_tns_bounded.png`` figure, but:
  * the middle "Delta WNS" panel is dropped (two panels instead of three), and
  * every bar value is read directly from
        plots/ppa_delta_wm_vs_attack.numbers.xlsx
    (columns dWL_pdmarks, dWL_attack, dTNS_pdmarks, dTNS_attack), so manual
    edits made in the spreadsheet are reflected in the figure.

Output:
    plots/ppa_delta_wm_vs_attack_tns_bounded_noWNS.png
"""
from __future__ import annotations

from pathlib import Path

import openpyxl

HERE = Path(__file__).resolve().parents[2]
XLSX = HERE / "plots" / "ppa_delta_wm_vs_attack.numbers.xlsx"
OUT_PNG = HERE / "plots" / "ppa_delta_wm_vs_attack_tns_bounded_noWNS.png"

# design order + two-line tick labels, matching the reference figure exactly.
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

# enlarged font sizes -- every text element > 15 pt
FS_TITLE = 10
FS_LEGEND = 16
FS_YLABEL = 17
FS_TICK = 16
FS_VALUE = 14


def _load():
    """Return {(platform, design): {col: value}} from the xlsx table."""
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    header = [h.strip() if isinstance(h, str) else h for h in rows[0]]
    idx = {name: i for i, name in enumerate(header)}
    data = {}
    for r in rows[1:]:
        if r[idx["platform"]] is None:
            continue
        key = (str(r[idx["platform"]]).strip(), str(r[idx["design"]]).strip())
        data[key] = {c: r[idx[c]] for c in
                     ("dWL_pdmarks", "dWL_attack", "dTNS_pdmarks", "dTNS_attack")}
    return data


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    data = _load()
    labels = [lbl for _, _, lbl in DESIGNS]
    rows = [data[(plat, design)] for plat, design, _ in DESIGNS]
    x = np.arange(len(labels))
    w = 0.38

    fig, (a1, a2) = plt.subplots(2, 1, figsize=(16.0, 8.2))

    def bars(ax, key_wm, key_atk, ylabel, fmt, ylim=None):
        vw = [r[key_wm] or 0.0 for r in rows]
        va = [r[key_atk] or 0.0 for r in rows]
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
                            xytext=(0, 4 if h >= 0 else -17), textcoords="offset points",
                            ha="center", fontsize=FS_VALUE, color="0.25")

    # WL (%)
    bars(a1, "dWL_pdmarks", "dWL_attack", r"$\Delta$ routed wirelength (%)",
         lambda h: f"{h:+.2f}")
    a1.set_xticklabels([])
    # a1.set_title(r"$\Delta$PPA vs un-watermarked reference: TNS as bounded symmetric change "
    #              r"$(|TNS_{ref}|-|TNS_{new}|)/(|TNS_{new}|+|TNS_{ref}|)$", fontsize=FS_TITLE)
    a1.legend(frameon=False, fontsize=FS_LEGEND, loc="upper left")

    # bounded [-1,1]; POSITIVE = better.  No symlog/clip needed -- values can't explode.
    bars(a2, "dTNS_pdmarks", "dTNS_attack",
         r"TNS change index ($\pm$1, +=better)", lambda h: f"{h:+.2f}", ylim=(-1.05, 1.05))
    a2.set_xticklabels(labels, fontsize=FS_TICK)
    a2.axhline(1.0, color="0.8", lw=0.7, ls=":")
    a2.axhline(-1.0, color="0.8", lw=0.7, ls=":")
    # (two-line explanatory footnote removed per request)

    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=150, bbox_inches="tight")
    print(f"[xlsx] wrote {OUT_PNG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
