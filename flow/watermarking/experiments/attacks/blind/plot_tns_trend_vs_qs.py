#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""TNS-degradation trend vs attack strength q_s (small multiples).

A single q* bar cannot show a *trend*.  This reads the post-route sweep in
plots/blind_qstar_data.csv (placement blind attack, dTNS_ns vs its watermarked
baseline at each q_s) and plots normalized TNS vs q_s per design, so the reader
can see the attack's timing damage grow with attack strength.

Normalization (same as the other figures): dTNS as % of the timing budget
    dTNS%_budget = 100 * dTNS_ns / (TCP * N_endpoints)
with POSITIVE = slack improved (better); attack damage therefore trends DOWN.

Because most benchmarks are at timing closure (|TNS_ref| ~ 0, shown in each
title), only the two ASAP7 designs with real timing pressure -- CVA6 and JPEG
-- carry a clean signal; the near-closure panels are honestly ~flat/noisy.

Output:
    plots/tns_trend_vs_qs.png
"""
from __future__ import annotations

import csv
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
SRC = HERE / "plots" / "blind_qstar_data.csv"
OUT_PNG = HERE / "plots" / "tns_trend_vs_qs.png"

# TCP (paper Table III, ns), FF = #endpoints (reference report), |TNS_ref| (ns)
META = {
    ("nangate45", "jpeg"):          dict(tcp=1.00, ff=4384,  tns=-35.433, label="JPEG (NG45)"),
    ("nangate45", "swerv_wrapper"): dict(tcp=2.00, ff=11308, tns=-365.187, label="SweRV (NG45)"),
    ("nangate45", "ariane136"):     dict(tcp=3.50, ff=20667, tns=-0.524,  label="Ariane (NG45)"),
    ("nangate45", "bp_multi_top"):  dict(tcp=4.80, ff=15813, tns=-0.461,  label="BP (NG45)"),
    ("asap7", "jpeg"):              dict(tcp=0.54, ff=4384,  tns=-0.940,  label="JPEG (ASAP7)"),
    ("asap7", "swerv_wrapper"):     dict(tcp=1.46, ff=11694, tns=-3.338,  label="SweRV (ASAP7)"),
    ("asap7", "cva6"):              dict(tcp=0.95, ff=8465,  tns=-0.983,  label="CVA6 (ASAP7)"),
    ("asap7", "ariane"):            dict(tcp=1.40, ff=20442, tns=0.000,   label="Ariane (ASAP7)"),
}
ORDER = [
    ("nangate45", "jpeg"), ("nangate45", "swerv_wrapper"),
    ("nangate45", "ariane136"), ("nangate45", "bp_multi_top"),
    ("asap7", "jpeg"), ("asap7", "swerv_wrapper"),
    ("asap7", "cva6"), ("asap7", "ariane"),
]
# designs carrying a clean, meaningful timing signal (real pressure + clean trend)
CLEAN = {("asap7", "cva6"), ("asap7", "jpeg")}


def _load():
    curves = {k: [] for k in META}
    qstar = {}
    for r in csv.DictReader(open(SRC)):
        k = (r["platform"], r["design"])
        if k not in curves:
            continue
        if r.get("design_qstar"):
            try:
                qstar[k] = float(r["design_qstar"])
            except ValueError:
                pass
        if r.get("dTNS_ns") in (None, ""):
            continue
        m = META[k]
        budget = m["tcp"] * m["ff"]
        curves[k].append((float(r["q_s"]), 100.0 * float(r["dTNS_ns"]) / budget))
    for k in curves:
        curves[k].sort()
    return curves, qstar


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    curves, qstar = _load()

    # per-row (technology) shared y-limits so flat panels look flat
    def row_lim(plat):
        vals = [v for (p, d) in ORDER if p == plat for _, v in curves[(p, d)]]
        lo, hi = min(vals), max(vals)
        pad = 0.12 * (hi - lo or 1.0)
        return lo - pad, hi + pad
    ylim = {"nangate45": row_lim("nangate45"), "asap7": row_lim("asap7")}

    fig, axes = plt.subplots(2, 4, figsize=(15.0, 6.6), sharex=True)
    for i, (plat, design) in enumerate(ORDER):
        ax = axes[0 if plat == "nangate45" else 1][i % 4]
        m = META[(plat, design)]
        pts = curves[(plat, design)]
        clean = (plat, design) in CLEAN
        col = "#C0392B" if clean else "#95A5A6"
        if pts:
            xs, ys = zip(*pts)
            ax.plot(xs, ys, "-o", ms=4, lw=1.8 if clean else 1.2, color=col,
                    zorder=3)
        ax.axhline(0, color="0.35", lw=0.9)
        ax.fill_between([0, 1.02], ylim[plat][0], 0, color="#E74C3C", alpha=0.06,
                        zorder=0)  # shaded "worse" half-plane
        qs = qstar.get((plat, design))
        if qs is not None:
            ax.axvline(qs, ls="--", lw=1.0, color="0.5")
            lo, hi = ylim[plat]
            ax.text(qs + 0.02, lo + 0.06 * (hi - lo), rf"$q^*$={qs:g}",
                    fontsize=7.5, color="0.4", ha="left", va="bottom")
        ax.set_ylim(*ylim[plat])
        ax.set_xlim(0.0, 1.02)
        ax.grid(color="0.92", lw=0.6, zorder=0)
        title = f"{m['label']}   |$TNS_{{ref}}$|={abs(m['tns']):.2f} ns"
        ax.set_title(title, fontsize=9.5,
                     fontweight="bold" if clean else "normal",
                     color="#7B241C" if clean else "0.35")
        if i % 4 == 0:
            ax.set_ylabel(r"$\Delta$TNS (% of budget)" + "\n(+=better, ↓=worse)",
                          fontsize=9)
        if plat == "asap7":
            ax.set_xlabel(r"attack strength $q_s$", fontsize=9)

    fig.suptitle("Blind-attack TNS degradation vs attack strength $q_s$   "
                 r"(red = designs with real timing pressure; gray = at closure, |TNS|$\approx$0)",
                 fontsize=11, y=0.99)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(OUT_PNG, dpi=150)
    print(f"[wrote] {OUT_PNG}")

    # console: slope of the clean signals
    print("\nClean-signal TNS trend (% of budget):")
    for k in ORDER:
        if k in CLEAN:
            pts = curves[k]
            print(f"  {META[k]['label']:<14} " +
                  "  ".join(f"q{q:g}:{v:+.3f}" for q, v in pts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
