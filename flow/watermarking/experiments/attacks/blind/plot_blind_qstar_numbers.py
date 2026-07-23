#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Blind-attack q* figure sourced from the hand-edited Numbers spreadsheet.

Same model/style as plot_blind_qstar_updated.py (placement + CTS only, tau=0.75),
but the extraction-rate curves are read directly from
    plots/blind_qstar_data_updated.numbers
so any manual edits made in Numbers are reflected in the figure.  q*_P and q*_C
are recomputed from the (possibly edited) data exactly as the reference script does.

Outputs:
    plots/blind_qstar_numbers.png
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from numbers_parser import Document

HERE = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "pbq", HERE / "attacks/blind/plot_blind_qstar.py")
pbq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pbq)

TAU = 0.75
NUMBERS = HERE / "plots" / "blind_qstar_data_updated.numbers"
OUT_PNG = HERE / "plots" / "blind_qstar_numbers.png"

# enlarged font sizes (originals shown in comments)
FS_SUPTITLE = 15   # was 10.5
FS_LEGEND = 13     # was 9
FS_TITLE = 13      # was 10 (subplot titles)
FS_QSTAR = 11      # was 8  (in-panel q* labels)
FS_AXLABEL = 14    # was default ~10
FS_TICK = 12       # was default ~10


def _f(v):
    """Coerce a Numbers cell to float or None."""
    if v is None or v == "":
        return None
    return float(v)


def _load_curves():
    """Read the single data table and group rows by (platform, design)."""
    doc = Document(str(NUMBERS))
    table = doc.sheets[0].tables[0]
    rows = table.rows(values_only=True)
    header = [h.strip() if isinstance(h, str) else h for h in rows[0]]
    idx = {name: i for i, name in enumerate(header)}
    curves = {}
    for r in rows[1:]:
        if r[idx["platform"]] is None:
            continue
        plat = str(r[idx["platform"]]).strip()
        design = str(r[idx["design"]]).strip()
        curves.setdefault((plat, design), []).append({
            "q":   _f(r[idx["q_s"]]),
            "r_P": _f(r[idx["r_P"]]),
            "r_C": _f(r[idx["r_C"]]),
        })
    for key in curves:
        curves[key].sort(key=lambda d: d["q"])
    return curves


def _qs_stage(rows, key):
    """Per-stage q*: smallest q_s where that stage's rate drops below tau."""
    for d in rows:
        v = d[key]
        if v is not None and v < TAU:
            return d["q"]
    return None


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    curves = _load_curves()

    fig, axes = plt.subplots(2, 4, figsize=(15.0, 7.0), sharex=True, sharey=True)
    for col, (plat, design, label) in enumerate(pbq.DESIGNS):
        ax = axes[0 if plat == "nangate45" else 1][col % 4]
        rows = curves.get((plat, design))
        if not rows:
            ax.set_title(f"{label} (no data)", fontsize=9)
            continue
        qs = [d["q"] for d in rows]
        ax.plot(qs, [d["r_P"] for d in rows], "-o", ms=3, color="#e67e22", label=r"$r_P$ (placement)")
        ax.plot(qs, [d["r_C"] for d in rows], "-s", ms=3, color="#27ae60", label=r"$r_C$ (CTS)")
        ax.axhline(TAU, ls="--", lw=1.0, color="0.4")
        qp = _qs_stage(rows, "r_P")
        qc = _qs_stage(rows, "r_C")

        def _val_at(q, key):
            return next((d[key] for d in rows if d["q"] == q), None)

        # vertical marker rises only up to its own curve's height at q*
        if qp is not None:
            top = _val_at(qp, "r_P")
            ax.vlines(qp, 0, top if top is not None else TAU,
                      colors="#e67e22", linewidths=1.6, linestyles="-", alpha=0.9)
            ax.text(qp, 0.14, rf"$q^*_P={qp:g}$", color="#b5651d", fontsize=FS_QSTAR,
                    ha="center", bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.85))
        if qc is not None:
            top = _val_at(qc, "r_C")
            ax.vlines(qc, 0, top if top is not None else TAU,
                      colors="#1e8449", linewidths=1.6, linestyles=(0, (4, 2)), alpha=0.95)
            ax.text(qc, 0.03, rf"$q^*_C={qc:g}$", color="#1e8449", fontsize=FS_QSTAR,
                    ha="center", bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.85))
        ax.set_title(label, fontsize=FS_TITLE, fontweight="bold")
        ax.set_xlim(0.0, 1.02)
        ax.set_ylim(0.0, 1.05)
        ax.grid(color="0.92", lw=0.6)
        ax.tick_params(labelsize=FS_TICK)
        if col % 4 == 0:
            ax.set_ylabel("extraction rate", fontsize=FS_AXLABEL)
        if plat == "asap7":
            ax.set_xlabel(r"perturbation fraction $q_s$", fontsize=FS_AXLABEL)
    handles, labels = axes[0][0].get_legend_handles_labels()
    handles += [plt.Line2D([], [], ls="--", color="0.4"),
                plt.Line2D([], [], ls="-", color="#e67e22"),
                plt.Line2D([], [], ls=(0, (4, 2)), color="#1e8449")]
    labels += [rf"$\tau_P=\tau_C={TAU:g}$",
               r"$q^*_P$ (placement crosses $\tau$)",
               r"$q^*_C$ (CTS crosses $\tau$)"]
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.legend(handles, labels, ncol=5, frameon=False, loc="lower center",
               bbox_to_anchor=(0.5, 0.935), fontsize=FS_LEGEND)
    fig.suptitle("Blind attack (placement + CTS only): "
                 r"$q^*_P$ and $q^*_C$ mark where each carrier crosses $\tau=0.75$",
                 fontsize=FS_SUPTITLE, y=1.07)
    fig.savefig(OUT_PNG, dpi=150, bbox_inches="tight")
    print(f"[numbers] wrote {OUT_PNG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

