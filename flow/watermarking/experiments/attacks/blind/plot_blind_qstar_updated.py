#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Updated blind-attack q* figure: placement + CTS only (no routing, no r_all).

Model (per user): routing is dropped (identifiable by statistics), and only the
placement and CTS carriers are considered on BOTH technologies. The blind attack
DEFEATS ownership only when BOTH extraction rates fall below tau=0.75; i.e.
ownership holds while EITHER r_P >= 0.75 OR r_C >= 0.75.  q* is the smallest q_s
at which both r_P < 0.75 AND r_C < 0.75.

Outputs:
    plots/blind_qstar_data_updated.csv
    plots/blind_qstar_updated.png
"""
from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("pbq", HERE / "attacks/blind/plot_blind_qstar.py")
pbq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pbq)

TAU = 0.75
OUT_CSV = HERE / "plots" / "blind_qstar_data_updated.csv"
OUT_PNG = HERE / "plots" / "blind_qstar_updated.png"
PPA_SRC = HERE / "plots" / "blind_qstar_data.csv"   # for the PPA-delta columns


def _held(r_P, r_C):
    """Ownership held while EITHER stage >= tau; defeated only when BOTH < tau."""
    pP = (r_P is not None and r_P >= TAU)
    pC = (r_C is not None and r_C >= TAU)
    return pP or pC


def _qstar(rows):
    """Overall defeat: smallest q_s where BOTH stages are below tau."""
    for d in rows:
        if not _held(d["r_P"], d["r_C"]):
            return d["q"]
    return None


def _qs_stage(rows, key):
    """Per-stage q*: smallest q_s where that stage's rate drops below tau."""
    for d in rows:
        v = d[key]
        if v is not None and v < TAU:
            return d["q"]
    return None


def _load_ppa():
    ppa = {}
    if PPA_SRC.exists():
        for r in csv.DictReader(open(PPA_SRC)):
            k = (r["platform"], r["design"], round(float(r["q_s"]), 3))
            ppa[k] = {c: r.get(c, "") for c in
                      ("dWNS_ns", "dTNS_ns", "dPower_pct", "drWL_pct")}
    return ppa


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ppa = _load_ppa()
    curves = {}
    for plat, design, label in pbq.DESIGNS:
        rows = pbq._load_curve(plat, design)
        if rows:
            # q_s = 0: no attack -> watermark fully intact -> extraction = 1.0
            origin = dict(rows[0])
            origin.update(q=0.0, r_P=1.0, r_C=1.0, r_R=1.0, r_all=1.0,
                          p_R=None, accept=True)
            curves[(plat, design)] = [origin] + rows

    # --- CSV ---
    cols = ["platform", "design", "label", "q_s", "r_P", "r_C", "ownership_held",
            "qstar_placement", "qstar_cts", "design_qstar",
            "is_qstar_placement", "is_qstar_cts",
            "dWNS_ns", "dTNS_ns", "dPower_pct", "drWL_pct"]
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        print(f"{'design':<16}{'q*_P':>6}{'q*_C':>6}{'q*_both':>8}")
        print("-" * 36)
        for plat, design, label in pbq.DESIGNS:
            rows = curves.get((plat, design))
            if not rows:
                continue
            qp = _qs_stage(rows, "r_P")
            qc = _qs_stage(rows, "r_C")
            qs = _qstar(rows)
            for d in rows:
                p = ppa.get((plat, design, round(d["q"], 3)), {})
                w.writerow([plat, design, label, d["q"], d["r_P"], d["r_C"],
                            int(_held(d["r_P"], d["r_C"])), qp, qc, qs,
                            int(d["q"] == qp), int(d["q"] == qc),
                            p.get("dWNS_ns", ""), p.get("dTNS_ns", ""),
                            p.get("dPower_pct", ""), p.get("drWL_pct", "")])
            print(f"{label:<16}{(qp if qp is not None else '>1'):>6}"
                  f"{(qc if qc is not None else '>1'):>6}"
                  f"{(qs if qs is not None else '>1'):>8}")

    # --- figure ---
    fig, axes = plt.subplots(2, 4, figsize=(15.0, 6.4), sharex=True, sharey=True)
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
            ax.text(qp, 0.14, rf"$q^*_P={qp:g}$", color="#b5651d", fontsize=8,
                    ha="center", bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.85))
        if qc is not None:
            top = _val_at(qc, "r_C")
            ax.vlines(qc, 0, top if top is not None else TAU,
                      colors="#1e8449", linewidths=1.6, linestyles=(0, (4, 2)), alpha=0.95)
            ax.text(qc, 0.03, rf"$q^*_C={qc:g}$", color="#1e8449", fontsize=8,
                    ha="center", bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.85))
        ax.set_title(label, fontsize=10, fontweight="bold")
        ax.set_xlim(0.0, 1.02)
        ax.set_ylim(0.0, 1.05)
        ax.grid(color="0.92", lw=0.6)
        if col % 4 == 0:
            ax.set_ylabel("extraction rate")
        if plat == "asap7":
            ax.set_xlabel(r"perturbation fraction $q_s$")
    handles, labels = axes[0][0].get_legend_handles_labels()
    handles += [plt.Line2D([], [], ls="--", color="0.4"),
                plt.Line2D([], [], ls="-", color="#e67e22"),
                plt.Line2D([], [], ls=(0, (4, 2)), color="#1e8449")]
    labels += [rf"$\tau_P=\tau_C={TAU:g}$",
               r"$q^*_P$ (placement crosses $\tau$)",
               r"$q^*_C$ (CTS crosses $\tau$)"]
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.legend(handles, labels, ncol=5, frameon=False, loc="lower center",
               bbox_to_anchor=(0.5, 0.935), fontsize=9)
    fig.suptitle("Blind attack (placement + CTS only): "
                 r"$q^*_P$ and $q^*_C$ mark where each carrier crosses $\tau=0.75$",
                 fontsize=10.5, y=1.07)
    fig.savefig(OUT_PNG, dpi=150, bbox_inches="tight")
    print(f"\n[updated] wrote {OUT_CSV}")
    print(f"[updated] wrote {OUT_PNG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
