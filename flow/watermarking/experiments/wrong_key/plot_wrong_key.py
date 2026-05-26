#!/usr/bin/env python3.11
# SPDX-License-Identifier: BSD-3-Clause
"""Plot wrong-key verification distributions.

This figure is intentionally aligned with ``tab:wrong-key``: it compares the
wrong-key null distribution against the correct-key evidence for placement,
CTS, the combined extraction score, and routing.  The per-design Pc histogram
was technically correct but hard to interpret because the correct-key Pc is
usually many orders of magnitude off the visible scale.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from bench_matrix import ACTIVE_BENCHES

RAW = HERE / "results" / "phase2" / "raw"
OUT = HERE / "plots" / "wrong_key_analysis.png"

P_FLOOR = 1e-300
TAU_P = 0.75
TAU_C = 0.90
TAU_ALL = 0.80
ALPHA_R = 0.0001


def _as_float(value) -> float | None:
    if value in ("", None):
        return None
    try:
        out = float(value)
    except Exception:
        return None
    return out if math.isfinite(out) else None


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _append_float(dst: list[float], value) -> None:
    out = _as_float(value)
    if out is not None:
        dst.append(out)


def _load_data() -> dict:
    wrong = {"r_P": [], "r_C": [], "r_all": [], "p_R": []}
    correct = {"r_P": [], "r_C": [], "r_all": [], "p_R": []}
    false_rall = total_rall = 0
    false_pc = total_pc = 0

    for b in ACTIVE_BENCHES:
        slug = f"{b.platform}_{b.design}"
        summary = _read_json(RAW / f"wrong_key_{slug}.json")
        _append_float(correct["r_P"], summary.get("true_r_P"))
        _append_float(correct["r_C"], summary.get("true_r_C"))
        _append_float(correct["r_all"], summary.get("true_r_all"))
        _append_float(correct["p_R"], summary.get("true_p_R"))

        true_rall = _as_float(summary.get("true_r_all"))
        true_pc = _as_float(summary.get("true_Pc"))
        dist = RAW / f"wrong_key_{slug}_dist.csv"
        if not dist.exists():
            continue
        for row in csv.DictReader(open(dist)):
            _append_float(wrong["r_P"], row.get("r_P"))
            _append_float(wrong["r_C"], row.get("r_C"))
            _append_float(wrong["r_all"], row.get("r_all"))
            _append_float(wrong["p_R"], row.get("p_R"))

            r_all = _as_float(row.get("r_all"))
            if true_rall is not None and r_all is not None:
                total_rall += 1
                if r_all >= true_rall:
                    false_rall += 1
            pc = _as_float(row.get("pc_all"))
            if true_pc is not None and pc is not None:
                total_pc += 1
                if pc <= true_pc:
                    false_pc += 1

    return {
        "wrong": wrong,
        "correct": correct,
        "false_rall": false_rall,
        "total_rall": total_rall,
        "false_pc": false_pc,
        "total_pc": total_pc,
    }


def _fmt_mean(vals: list[float], *, pvalue: bool = False) -> str:
    if not vals:
        return "n/a"
    mean = sum(vals) / len(vals)
    if pvalue and mean < 1e-3:
        return f"{mean:.1e}"
    return f"{mean:.3f}"


def _panel_extraction(ax, wrong, correct, *, title, xlabel, threshold, tau_label):
    bins = [i / 40 for i in range(41)]
    ax.hist(wrong, bins=bins, density=True, color="#9fc2dc", edgecolor="#47718e",
            alpha=0.85, label=f"Wrong keys (n=5000)")
    if correct:
        for v in correct:
            ax.axvline(v, color="#a51e36", alpha=0.16, linewidth=2.0)
        ax.axvline(sum(correct) / len(correct), color="#a51e36", linewidth=2.3,
                   label=f"Correct key")
    ax.axvline(threshold, color="black", linestyle="--", linewidth=1.5,
               label=rf"{tau_label}={threshold:.2f}")
    ax.set_xlim(0.0, 1.02)
    ax.set_title(title, loc="left", fontweight="bold")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Density")
    ax.grid(axis="y", color="0.9", linewidth=0.7)


def _panel_routing(ax, wrong, correct):
    # The correct-key p-values can be far below double precision range, while
    # wrong-key p-values live in the ordinary null range near [0, 1].  Plotting
    # raw p-values hides one of those two facts, so use clipped log10(p_R).
    floor_log = -6.0
    wrong_plot = [
        max(math.log10(max(v, P_FLOOR)), floor_log) for v in wrong if v > 0
    ]
    correct_plot = [
        max(math.log10(max(v, P_FLOOR)), floor_log) for v in correct
    ]
    bins = [floor_log + i * ((0.0 - floor_log) / 36) for i in range(37)]
    ax.hist(wrong_plot, bins=bins, density=True, color="#9fc2dc",
            edgecolor="#47718e", alpha=0.85,
            label=f"Wrong keys (n=5000)")
    if correct_plot:
        for v in correct_plot:
            ax.axvline(v, color="#a51e36", alpha=0.22, linewidth=2.0)
        ax.axvline(sum(correct_plot) / len(correct_plot), color="#a51e36",
                   linewidth=2.3,
                   label=f"Correct key={_fmt_mean(correct, pvalue=True)}")
    ax.axvline(math.log10(ALPHA_R), color="black", linestyle="--", linewidth=1.5,
               label=rf"$\alpha_R$={ALPHA_R:.2f}")
    ax.set_xlim(floor_log, 0.0)
    ax.set_xticks([floor_log, -5, -4, -3, -2, -1, 0])
    ax.set_xticklabels([r"$\leq -6$", "-5", "-4", "-3", "-2", "-1", "0"])
    ax.set_title("(d) Routing", loc="left", fontweight="bold")
    ax.set_xlabel(r"$\log_{10}$ routing $p$-value $p_R$ (smaller is stronger)")
    ax.set_ylabel("Density")
    ax.grid(axis="y", color="0.9", linewidth=0.7)


def main() -> int:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        print("[plot_wrong_key] matplotlib missing; skipping figure", file=sys.stderr)
        return 0

    data = _load_data()
    wrong = data["wrong"]
    correct = data["correct"]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 2, figsize=(11.0, 7.8))
    ax_p, ax_c, ax_all, ax_r = axes.flatten()

    _panel_extraction(
        ax_p, wrong["r_P"], correct["r_P"],
        title="(a) Placement",
        xlabel=r"Placement extraction rate $r_P$",
        threshold=TAU_P,
        tau_label=r"$\tau_P$",
    )
    _panel_extraction(
        ax_c, wrong["r_C"], correct["r_C"],
        title="(b) CTS",
        xlabel=r"CTS extraction rate $r_C$",
        threshold=TAU_C,
        tau_label=r"$\tau_C$",
    )
    _panel_extraction(
        ax_all, wrong["r_all"], correct["r_all"],
        title="(c) Combined",
        xlabel=r"Combined extraction rate $r_{\mathrm{all}}$",
        threshold=TAU_ALL,
        tau_label=r"$\tau_{\mathrm{all}}$",
    )
    _panel_routing(ax_r, wrong["p_R"], correct["p_R"])

    for ax in axes.flatten():
        ax.legend(frameon=False, fontsize=9, loc="upper left")

    fpr_rall = (
        data["false_rall"] / data["total_rall"] if data["total_rall"] else 0.0
    )
    fpr_pc = data["false_pc"] / data["total_pc"] if data["total_pc"] else 0.0
    # fig.suptitle(
    #     "Wrong-key verification: random keys stay below the ownership threshold",
    #     fontsize=13,
    #     y=0.985,
    # )
    # fig.text(
    #     0.5, 0.018,
    #     f"Observed false positives: r_all {data['false_rall']}/{data['total_rall']}"
    #     f" ({fpr_rall:.3g}), Pc {data['false_pc']}/{data['total_pc']}"
    #     f" ({fpr_pc:.3g}). Routing p-values use NG45 cases only; ASAP7 routing"
    #     " is skipped because wrong-way counts are structurally zero.",
    #     ha="center",
    #     fontsize=9,
    # )
    fig.tight_layout(rect=(0.0, 0.045, 1.0, 0.955))
    fig.savefig(OUT, dpi=150)
    print(f"[plot_wrong_key] wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
