#!/usr/bin/env python3.11
# SPDX-License-Identifier: BSD-3-Clause
"""Plot the wrong-key Pc distribution for each design (Fig. wrong_key_analysis)."""

from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from bench_matrix import ACTIVE_BENCHES

RAW = HERE / "results" / "phase2" / "raw"
OUT = HERE / "plots" / "wrong_key_analysis.png"


def main():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        print("[plot_wrong_key] matplotlib missing; skipping figure", file=sys.stderr)
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 4, figsize=(14, 6), sharey=True)
    axes = axes.flatten()
    for ax, b in zip(axes, ACTIVE_BENCHES):
        slug = f"{b.platform}_{b.design}"
        dist = RAW / f"wrong_key_{slug}_dist.csv"
        if not dist.exists():
            ax.set_title(f"{b.paper_label} (no data)")
            ax.axis("off")
            continue
        pcs = []
        for row in csv.DictReader(open(dist)):
            try:
                pcs.append(float(row["pc_all"]))
            except Exception:
                pass
        if not pcs:
            ax.set_title(f"{b.paper_label} (empty)")
            ax.axis("off")
            continue
        log_pcs = [math.log10(max(p, 1e-300)) for p in pcs]
        ax.hist(log_pcs, bins=40, color="#3477eb", edgecolor="#1b4f9e")
        ax.set_title(b.paper_label)
        ax.set_xlabel(r"$\log_{10} P_c$")
    # blank any unused subplots
    for ax in axes[len(ACTIVE_BENCHES):]:
        ax.axis("off")
    axes[0].set_ylabel("# wrong keys")
    fig.suptitle("Wrong-key $P_c$ null distribution (1000 random keys each)",
                 fontsize=13)
    fig.tight_layout()
    fig.savefig(OUT, dpi=150)
    print(f"[plot_wrong_key] wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
