#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Blind-attack extraction curves + ownership defeat fraction q*.

Reads the all-stage blind-attack JSONs
    results/phase3/raw/blind_<plat>_<design>_all_stage_qs<q>.json
for every q_s on disk (works with the coarse 5-point grid or the fine
0.1 grid), recomputes the ownership decision with the recalculated
thresholds, finds q* (the smallest q_s that defeats ownership), and
draws one small-multiple per design showing r_P, r_C, r_all vs q_s with
the threshold lines and q* marked.

Ownership rule (Eq. ownership_rule):
    accept  <=>  >= 2 of {r_P>=TAU_P, r_C>=TAU_C, r_R} pass
    r_R = 1 if p_R <= ALPHA_R else 0   (routing; absent on ASAP7)
r_all (mean of the available per-stage rates) is reported as a confidence
summary only; it does not gate the decision.

Thresholds (from the wrong-key maxima, Table VIII):
    TAU_P = TAU_C = 0.75,  ALPHA_R = 1e-4

Outputs:
    plots/blind_qstar.png
    results/phase3/blind_qstar.csv   (design, q*, r_all@q*, per-stage @q*)
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE))
from bench_matrix import ACTIVE_BENCHES

RAW = HERE / "results" / "phase3" / "raw"
OUT_PNG = HERE / "plots" / "blind_qstar.png"
OUT_CSV = HERE / "results" / "phase3" / "blind_qstar.csv"

# Per-stage thresholds (see subsec:wrong-key). r_all is reported, not gated.
TAU_P = 0.75
TAU_C = 0.75
ALPHA_R = 1e-4

# Paper labels / display order (NG45 first row, ASAP7 second row).
DESIGNS = [
    ("nangate45", "jpeg",          "JPEG (NG45)"),
    ("nangate45", "swerv_wrapper", "SweRV (NG45)"),
    ("nangate45", "ariane136",     "Ariane (NG45)"),
    ("nangate45", "bp_multi_top",  "BP (NG45)"),
    ("asap7",     "jpeg",          "JPEG (ASAP7)"),
    ("asap7",     "swerv_wrapper", "SweRV (ASAP7)"),
    ("asap7",     "cva6",          "CVA6 (ASAP7)"),
    ("asap7",     "ariane",        "Ariane (ASAP7)"),
]


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _accept(r_P, r_C, p_R):
    """Ownership decision under the recalculated thresholds. Returns
    (accept, r_all, r_R)."""
    r_R = None
    if p_R is not None:
        r_R = 1.0 if p_R <= ALPHA_R else 0.0
    parts = [v for v in (r_P, r_C, r_R) if v is not None]
    r_all = sum(parts) / len(parts) if parts else None
    n_pass = sum([
        r_P is not None and r_P >= TAU_P,
        r_C is not None and r_C >= TAU_C,
        r_R == 1.0,
    ])
    accept = n_pass >= 2   # r_all is reported only, not a gate
    return accept, r_all, r_R


def _read_q_field(pattern, field):
    """Return {q_s: value} for `field` over JSONs matching `pattern`."""
    out = {}
    for p in RAW.glob(pattern):
        q = _num(p.stem.split("_qs")[-1])
        if q is None:
            continue
        out[q] = _num(json.loads(p.read_text()).get(field))
    return out


def _carry_pR(q, coarse_pR):
    """Reuse the routing p_R from the nearest coarse q_s <= q (routing is not
    re-run at the fine gap points; r_R stays significant through q~0.8). Returns
    None when no routing channel exists (ASAP7)."""
    avail = sorted((qq, v) for qq, v in coarse_pR.items() if v is not None)
    if not avail:
        return None
    below = [(qq, v) for qq, v in avail if qq <= q]
    return (below[-1][1] if below else avail[0][1])


def _load_curve(plat, design):
    """Merge the coarse all-stage runs (full r_P/r_C/p_R) with the fine
    reroute-free placement/CTS sweeps (r_P, r_C), reusing the coarse routing
    vote for the fine gap points. Returns a sorted list of per-q_s dicts."""
    # coarse all-stage: full triple
    c_rP = _read_q_field(f"blind_{plat}_{design}_all_stage_qs*.json", "r_P")
    c_rC = _read_q_field(f"blind_{plat}_{design}_all_stage_qs*.json", "r_C")
    c_pR = _read_q_field(f"blind_{plat}_{design}_all_stage_qs*.json", "p_R")
    # fine reroute-free per-stage sweeps
    f_rP = _read_q_field(f"blind_{plat}_{design}_placement_qs*.json", "r_P")
    f_rC = _read_q_field(f"blind_{plat}_{design}_cts_qs*.json", "r_C")

    all_q = sorted(set(c_rP) | set(c_rC) | set(f_rP) | set(f_rC))
    rows = []
    for q in all_q:
        r_P = f_rP.get(q, c_rP.get(q))
        r_C = f_rC.get(q, c_rC.get(q))
        p_R = c_pR.get(q) if q in c_pR else _carry_pR(q, c_pR)
        accept, r_all, r_R = _accept(r_P, r_C, p_R)
        rows.append({"q": q, "r_P": r_P, "r_C": r_C, "p_R": p_R,
                     "r_R": r_R, "r_all": r_all, "accept": accept})
    return rows


def _qstar(rows):
    for d in rows:
        if not d["accept"]:
            return d["q"]
    return None


def main() -> int:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        print("[blind_qstar] matplotlib missing; skipping figure", file=sys.stderr)
        return 0

    curves = {}
    for plat, design, label in DESIGNS:
        rows = _load_curve(plat, design)
        if rows:
            curves[(plat, design)] = rows

    # --- q* table ---
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CSV, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["design", "qstar", "r_all_at_qstar", "r_P_at_qstar",
                     "r_C_at_qstar", "p_R_at_qstar", "n_qs"])
        print(f"{'design':<16}{'q*':>6}{'r_all@q*':>10}{'r_P@q*':>8}{'r_C@q*':>8}{'#q_s':>6}")
        print("-" * 56)
        for plat, design, label in DESIGNS:
            rows = curves.get((plat, design))
            if not rows:
                print(f"{label:<16}{'(no data)':>6}")
                continue
            qs = _qstar(rows)
            at = next((d for d in rows if d["q"] == qs), None)
            if at:
                wr.writerow([label, qs, at["r_all"], at["r_P"], at["r_C"],
                             at["p_R"], len(rows)])
                print(f"{label:<16}{qs:>6}{at['r_all']:>10.3f}"
                      f"{at['r_P']:>8.3f}{at['r_C']:>8.3f}{len(rows):>6}")
            else:
                wr.writerow([label, "none", "", "", "", "", len(rows)])
                print(f"{label:<16}{'none':>6}{'':>10}{'':>8}{'':>8}{len(rows):>6}")

    # --- figure: 2 rows (NG45 / ASAP7) x 4 designs ---
    fig, axes = plt.subplots(2, 4, figsize=(15.0, 6.6), sharex=True, sharey=True)
    for col, (plat, design, label) in enumerate(DESIGNS):
        ax = axes[0 if plat == "nangate45" else 1][col % 4]
        rows = curves.get((plat, design))
        if not rows:
            ax.set_title(f"{label} (no data)", fontsize=9)
            continue
        qs = [d["q"] for d in rows]
        rP = [d["r_P"] for d in rows]
        rC = [d["r_C"] for d in rows]
        rall = [d["r_all"] for d in rows]
        ax.plot(qs, rP, "-o", ms=3, color="#e67e22", label=r"$r_P$")
        ax.plot(qs, rC, "-s", ms=3, color="#27ae60", label=r"$r_C$")
        ax.plot(qs, rall, "-^", ms=3, color="#2c3e50", lw=2.0,
                label=r"$r_{\mathrm{all}}$ (confidence)")
        # per-stage acceptance threshold (the gate); r_all is not gated
        ax.axhline(TAU_P, ls="--", lw=1.0, color="0.4")
        # q* marker
        qs_star = _qstar(rows)
        if qs_star is not None:
            ax.axvline(qs_star, ls="-", lw=1.4, color="#c0392b", alpha=0.7)
            ax.text(qs_star, 0.04, rf"$q^*={qs_star:g}$", color="#c0392b",
                    fontsize=8, ha="center",
                    bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.8))
        ax.set_title(label, fontsize=10, fontweight="bold")
        ax.set_xlim(0.0, 1.02)
        ax.set_ylim(0.0, 1.05)
        ax.grid(color="0.92", lw=0.6)
        if col % 4 == 0:
            ax.set_ylabel("extraction rate")
        if (0 if plat == "nangate45" else 1) == 1:
            ax.set_xlabel(r"perturbation fraction $q_s$")
    # one legend + threshold annotation
    handles, labels = axes[0][0].get_legend_handles_labels()
    handles += [plt.Line2D([], [], ls="--", color="0.4")]
    labels += [rf"$\tau_P=\tau_C={TAU_P:g}$ (per-stage gate)"]
    fig.legend(handles, labels, ncol=5, frameon=False, loc="upper center",
               bbox_to_anchor=(0.5, 1.0), fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PNG, dpi=150)
    print(f"\n[blind_qstar] wrote {OUT_PNG}")
    print(f"[blind_qstar] wrote {OUT_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
