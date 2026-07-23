#!/usr/bin/env python3.12
# SPDX-License-Identifier: BSD-3-Clause
"""Regenerate fig:routing_attack and the post-attack routing statistic under
the revised canonical wrong-way wirelength fraction q_R(n).

For each NG45 design we compare three per-net q_R distributions:
  * watermarked  : q_R over WM_R on the owner all-stage layout
  * rerouted     : q_R over WM_R on the surgical removal ("cleared") layout,
                   where the watermarked nets were ripped up and rerouted
                   without the wrong-way penalty
  * unselected   : q_R over E_R \\ WM_R on the owner all-stage layout

We also report the post-attack net-level statistic (T_R, p_R): WM_R vs
E_R\\WM_R measured on the attacked layout.  If the local reroute pushes the
selected nets' q_R back up to the population level, T_R -> ~0 and p_R rises
above alpha_R -- i.e. the evidence is removable only by discarding and
re-implementing the protected routing.

Writes plots/routing_attack.png and results/phase_route_v2/route_v2_attack.json.
Requires numpy + matplotlib (host python3.12 has both).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

EXP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXP))
from lib.route_stat_v2 import (per_net_qr, read_watermark_nets, build_qr_vector,
                               observed_T, exact_tail_log10,
                               randomization_pvalue_normal)

QR = EXP / "results" / "phase_route_v2"
ALPHA_R = 1e-4

DESIGNS = [("JPEG", "jpeg"), ("SweRV", "swerv_wrapper"),
           ("Ariane", "ariane136"), ("BP", "bp_multi")]


def _qr_map(csv_path: Path):
    """{net -> q_R} over nets with tot_len>0."""
    out = {}
    for name, (ww, tot) in per_net_qr(csv_path).items():
        if tot > 0:
            out[name] = ww / tot
    return out


# colors matching figure/route_wm_attack.png
C_WM     = "#e8564b"   # watermarked (red)
C_REMOVE = "#5aa469"   # removed / rerouted (green)
C_NORMAL = "#b3b3b3"   # normal / unselected (gray)


def main():
    results = []
    plt.rcParams.update({"font.size": 15})
    fig, ax = plt.subplots(figsize=(7.2, 5.4))
    per_design = []   # (label, [wm%, removed%, normal%])
    for label, nick in DESIGNS:
        owner_csv = QR / f"allstage_{nick}.csv"
        atk_csv = QR / f"cleared_{nick}.csv"
        wm_txt = EXP / "results" / "nangate45" / nick / "pdmarks-all-stage" / "watermark_nets.txt"
        if not owner_csv.exists() or not wm_txt.exists():
            print(f"[skip] {label}: missing owner data", flush=True)
            continue

        owner = _qr_map(owner_csv)
        wm = read_watermark_nets(wm_txt)
        wm_elig = [n for n in wm if n in owner]
        unsel = [owner[n] for n in owner if n not in wm]
        q_wm_owner = [owner[n] for n in wm_elig]

        atk = _qr_map(atk_csv) if atk_csv.exists() else {}
        q_wm_atk = [atk[n] for n in wm_elig if n in atk]

        # post-attack statistic on the attacked layout (WM_R vs E_R\WM_R).
        # After local reroute the selected nets' q_R rises toward the population
        # level, so the statistic sits in the bulk of the null -> use the
        # (validated) normal approximation for its p-value.
        post = None
        if atk:
            counts = per_net_qr(atk_csv)
            vec = build_qr_vector(counts, wm)
            T_post = observed_T(vec.q, vec.sel)
            p_post = randomization_pvalue_normal(vec.q, vec.k, T_post)
            post = {"T_R": T_post, "p_R": p_post,
                    "r_R": 1.0 if p_post <= ALPHA_R else 0.0,
                    "k": vec.k, "E_R": vec.n_eligible}

        # owner statistic (exact net-level tail; selected nets are wrong-way-free)
        vc = build_qr_vector(per_net_qr(owner_csv), wm)
        T_own = observed_T(vc.q, vc.sel)
        log10_p_own = exact_tail_log10(vc.q, vc.sel)

        rec = {"design": label, "nick": nick,
               "owner": {"T_R": T_own, "log10_p_R": log10_p_own,
                         "q_wm_mean": float(np.mean(q_wm_owner)) if q_wm_owner else None,
                         "q_unsel_mean": float(np.mean(unsel)) if unsel else None},
               "attack": post,
               "n_wm": len(wm_elig), "n_wm_atk": len(q_wm_atk)}
        results.append(rec)
        print(f"{label}: owner T_R={T_own:+.3e} log10p_R={log10_p_own:.1f} | "
              f"attack " + (f"T_R={post['T_R']:+.3e} p_R={post['p_R']:.3f}"
                            if post else "n/a") +
              f" | q_wm(owner)={rec['owner']['q_wm_mean']:.4f} "
              f"q_wm(atk)={np.mean(q_wm_atk) if q_wm_atk else float('nan'):.4f} "
              f"q_unsel={rec['owner']['q_unsel_mean']:.4f}", flush=True)

        # store the three per-net wrong-way distributions as PERCENT
        per_design.append((label,
                           [np.array(q_wm_owner) * 100.0,
                            (np.array(q_wm_atk) if q_wm_atk else np.array([np.nan])) * 100.0,
                            np.array(unsel) * 100.0]))

    # ---- single-panel grouped boxplot (style of figure/route_wm_attack.png) ----
    ncat = 3
    width = 0.24
    offs = [-width, 0.0, width]
    colors = [C_WM, C_REMOVE, C_NORMAL]
    for gi, (label, cats) in enumerate(per_design):
        for ci in range(ncat):
            bp = ax.boxplot(cats[ci], positions=[gi + offs[ci]], widths=width * 0.9,
                            showfliers=False, patch_artist=True,
                            medianprops=dict(color="black", linewidth=1.4),
                            whiskerprops=dict(color="black"),
                            capprops=dict(color="black"),
                            boxprops=dict(edgecolor="black"))
            bp["boxes"][0].set_facecolor(colors[ci])
    ax.set_xticks(range(len(per_design)))
    ax.set_xticklabels([d[0] for d in per_design])
    ax.set_ylabel("per-net wrong-way rate (%)")
    ax.set_ylim(-2, 40)
    ax.grid(axis="y", alpha=0.25)
    # legend
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(facecolor=C_WM, edgecolor="black", label="watermarked"),
                       Patch(facecolor=C_REMOVE, edgecolor="black", label="rerouted"),
                       Patch(facecolor=C_NORMAL, edgecolor="black", label="normal")],
              loc="upper right", frameon=False)
    fig.tight_layout()
    outpng = EXP / "plots" / "routing_attack.png"
    fig.savefig(outpng, dpi=200)
    print(f"[fig] wrote {outpng}", flush=True)
    (QR / "route_v2_attack.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
