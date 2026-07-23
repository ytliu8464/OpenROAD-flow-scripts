#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Bar charts: PPA delta of PDMarks watermarking vs blind attack at q*.

For each design, two conditions are compared, both relative to the SAME
un-watermarked reference flow:
  * PDMarks (watermark overhead)  = pdmarks-all-stage vs reference
  * Blind attack @ q*             = placement attack at the design's q*
                                    (nearest available q_s) vs reference
Metrics: routed wirelength (relative %) and TNS.  TNS is normalized by the
total timing budget T_clk * N_endpoints (N_endpoints = flip-flop count):

    dTNS_budget% = 100 * (TNS_x - TNS_ref) / (T_clk * N_endpoints)

The sign is chosen so that POSITIVE means TNS_x is less negative than the
reference -> slack improved (better).  Dividing by the budget (always large and
positive) avoids the near-closure blow-up of the naive TNS_ref-relative %.

Outputs:
  plots/ppa_delta_wm_vs_attack.csv
  plots/ppa_delta_wm_vs_attack.png
"""
from __future__ import annotations

import csv
import glob
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE))
from bench_matrix import ACTIVE_BENCHES
from lib.orfs import load_experiment_metrics, load_reference

OUT_CSV = HERE / "plots" / "ppa_delta_wm_vs_attack.csv"
OUT_PNG = HERE / "plots" / "ppa_delta_wm_vs_attack.png"
# companion figure: TNS normalized by clock period only ( |TNS|/TCP , no #endpoints)
OUT_PNG_TCP = HERE / "plots" / "ppa_delta_wm_vs_attack_tns_tcp.png"
# companion figure: TNS as relative change vs reference, (TNS_new - TNS_ref)/TNS_ref
OUT_PNG_REL = HERE / "plots" / "ppa_delta_wm_vs_attack_tns_rel.png"
# companion figure: TNS as symmetric (bounded [-1,1]) relative change
OUT_PNG_BND = HERE / "plots" / "ppa_delta_wm_vs_attack_tns_bounded.png"

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


def _bench(p, d):
    return next((x for x in ACTIVE_BENCHES if x.platform == p and
                (x.design == d or x.design_nickname == d)), None)


def _qstar():
    q = {}
    for r in csv.DictReader(open(HERE / "plots" / "blind_qstar_data_updated.csv")):
        q[(r["platform"], r["design"])] = float(r["design_qstar"])
    return q


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    qstar = _qstar()
    rows = []
    for plat, design, label in DESIGNS:
        b = _bench(plat, design)
        nk = b.design_nickname
        qs_av = sorted({re.search(r"qs([0-9.]+)", p).group(1)
                        for p in glob.glob(f"{HERE}/logs/{plat}/{nk}/atk-p-{design}-qs*/6_report.json")},
                       key=float)
        qst = qstar[(plat, design)]
        qused = min(qs_av, key=lambda x: abs(float(x) - qst)) if qs_av else None
        ref = load_reference(plat, nk, b.wm_flow_variant)
        wm = load_experiment_metrics(plat, nk, "pdmarks-all-stage")
        atk = load_experiment_metrics(plat, nk, f"atk-p-{design}-qs{qused}") if qused else None

        # TNS normalized by the timing budget = clock period x endpoint count (FFs).
        tclk = ref.tcp_ns
        n_ep = ref.raw.get("finish__design__instance__count__class:sequential_cell")
        budget = (tclk * n_ep) if (tclk and n_ep) else None

        def dwl(m):
            return 100.0 * (m.rwl_um / ref.rwl_um - 1.0) if (m and m.rwl_um and ref.rwl_um) else None

        def dtns(m):
            return (m.tns_ns - ref.tns_ns) if (m and m.tns_ns is not None and ref.tns_ns is not None) else None

        def dtns_budget_pct(m):
            # dTNS as % of the timing budget; POSITIVE = TNS less negative -> better.
            if not (m and m.tns_ns is not None and ref.tns_ns is not None and budget):
                return None
            return 100.0 * (m.tns_ns - ref.tns_ns) / budget

        def dtns_tcp_pct(m):
            # dTNS as % of one clock period ( |TNS|/TCP , no #endpoints); + = better.
            if not (m and m.tns_ns is not None and ref.tns_ns is not None and tclk):
                return None
            return 100.0 * (m.tns_ns - ref.tns_ns) / tclk

        def dtns_rel_pct(m):
            # (TNS_ref - TNS_new)/TNS_ref.  TNS_ref<0, so POSITIVE = better (|TNS| shrank).
            # Undefined when TNS_ref == 0 (Ariane-ASAP7) -> None.
            if not (m and m.tns_ns is not None and ref.tns_ns is not None) or abs(ref.tns_ns) < 1e-9:
                return None
            return 100.0 * (ref.tns_ns - m.tns_ns) / ref.tns_ns

        def dtns_sym(m):
            # symmetric relative change: (|TNS_ref|-|TNS_new|)/(|TNS_new|+|TNS_ref|).
            # Bounded [-1,1]; + = |TNS| shrank (better).  0/0 -> 0 (handles TNS_ref=0).
            if not (m and m.tns_ns is not None and ref.tns_ns is not None):
                return None
            a, b = abs(m.tns_ns), abs(ref.tns_ns)
            if a + b < 1e-9:
                return 0.0
            return (b - a) / (a + b)

        def dwns(m):
            return (m.wns_ns - ref.wns_ns) if (m and m.wns_ns is not None and ref.wns_ns is not None) else None

        def dwns_tcp_pct(m):
            # dWNS as % of one clock period (worst-path slack, no #endpoints); + = better.
            if not (m and m.wns_ns is not None and ref.wns_ns is not None and tclk):
                return None
            return 100.0 * (m.wns_ns - ref.wns_ns) / tclk

        rows.append({
            "platform": plat, "design": design, "label": label.replace("\n", " "),
            "qstar": qst, "q_used": qused,
            "dWL_pdmarks_pct": dwl(wm), "dWL_attack_pct": dwl(atk),
            "dWNS_pdmarks_tcp_pct": dwns_tcp_pct(wm),
            "dWNS_attack_tcp_pct": dwns_tcp_pct(atk),
            "dWNS_pdmarks_ns": dwns(wm), "dWNS_attack_ns": dwns(atk),
            "dTNS_pdmarks_budget_pct": dtns_budget_pct(wm),
            "dTNS_attack_budget_pct": dtns_budget_pct(atk),
            "dTNS_pdmarks_tcp_pct": dtns_tcp_pct(wm),
            "dTNS_attack_tcp_pct": dtns_tcp_pct(atk),
            "dTNS_pdmarks_rel_pct": dtns_rel_pct(wm),
            "dTNS_attack_rel_pct": dtns_rel_pct(atk),
            "dTNS_pdmarks_sym": dtns_sym(wm),
            "dTNS_attack_sym": dtns_sym(atk),
            "dTNS_pdmarks_ns": dtns(wm), "dTNS_attack_ns": dtns(atk),
            "Tclk_ns": tclk, "N_endpoints": n_ep, "budget_ns": budget,
            "WL_ref": ref.rwl_um, "WL_wm": wm.rwl_um if wm else None,
            "WL_atk": atk.rwl_um if atk else None,
            "TNS_ref": ref.tns_ns, "TNS_wm": wm.tns_ns if wm else None,
            "TNS_atk": atk.tns_ns if atk else None,
        })

    # --- CSV ---
    with open(OUT_CSV, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wr.writeheader()
        for r in rows:
            wr.writerow({k: (f"{v:.3f}" if isinstance(v, float) else v) for k, v in r.items()})

    # --- figure: two stacked subplots, grouped bars ---
    import numpy as np
    labels = [lbl for _, _, lbl in DESIGNS]
    x = np.arange(len(labels))
    w = 0.38
    fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(11.0, 9.4))

    def bars(ax, key_wm, key_atk, ylabel, fmt, ylim=None, symlog=None):
        vw = [r[key_wm] or 0.0 for r in rows]
        va = [r[key_atk] or 0.0 for r in rows]
        b1 = ax.bar(x - w / 2, vw, w, label="PDMarks (watermark overhead)", color=C_WM)
        b2 = ax.bar(x + w / 2, va, w, label="Blind attack @ $q^*$", color=C_ATK)
        ax.axhline(0, color="0.5", lw=0.8)
        ax.set_ylabel(ylabel)
        ax.set_xticks(x)
        ax.grid(axis="y", color="0.9", lw=0.7)
        if symlog:
            ax.set_yscale("symlog", linthresh=symlog)
        if ylim:
            ax.set_ylim(*ylim)
        for bars_ in (b1, b2):
            for rect in bars_:
                h = rect.get_height()
                yy = h
                if ylim:                    # clamp off-scale labels to the axis
                    yy = max(ylim[0], min(h, ylim[1]))
                ax.annotate(fmt(h), (rect.get_x() + rect.get_width() / 2, yy),
                            xytext=(0, 2 if h >= 0 else -9), textcoords="offset points",
                            ha="center", fontsize=6.5, color="0.25")

    # WL (%)
    bars(a1, "dWL_pdmarks_pct", "dWL_attack_pct", r"$\Delta$ routed wirelength (%)", lambda h: f"{h:+.2f}")
    a1.set_xticklabels([])
    a1.set_title(r"$\Delta$PPA vs un-watermarked reference: PDMarks watermarking vs blind attack at $q^*$",
                 fontsize=11)
    a1.legend(frameon=False, fontsize=9, loc="upper left")
    # WNS normalized by clock period (worst-path slack; no #endpoints) -- + = better.
    bars(a2, "dWNS_pdmarks_tcp_pct", "dWNS_attack_tcp_pct",
         r"$\Delta$ WNS (% of TCP, +=better)", lambda h: f"{h:+.1f}")
    a2.set_xticklabels([])
    # TNS normalized by timing budget (TCP x #endpoints) -- POSITIVE = better (more slack).
    bars(a3, "dTNS_pdmarks_budget_pct", "dTNS_attack_budget_pct",
         r"$\Delta$ TNS (% of budget, +=better)", lambda h: f"{h:+.2f}")
    a3.set_xticklabels(labels, fontsize=9)
    a3.annotate(r"$\Delta$TNS $= 100\,(TNS_{x}-TNS_{ref})/(T_{clk}\times N_{endpoints})$;  "
                r"$+$ = slack improved;  all $|\Delta| < 0.3\%$",
                xy=(0.02, 0.96), xycoords="axes fraction",
                fontsize=6.8, color="#555555", ha="left", va="top")

    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=150)

    # --- companion figure: TNS normalized WITHOUT #endpoints ( |TNS|/TCP ) -------
    # Same layout; TNS panel divides by T_clk only, so the attack's ASAP7 timing
    # damage becomes visible (it was <0.3% under the #endpoints budget norm).
    # symlog axis + value labels handle the wide dynamic range.
    fig2, (c1, c2, c3) = plt.subplots(3, 1, figsize=(11.0, 9.4))
    bars(c1, "dWL_pdmarks_pct", "dWL_attack_pct",
         r"$\Delta$ routed wirelength (%)", lambda h: f"{h:+.2f}")
    c1.set_xticklabels([])
    c1.set_title(r"$\Delta$PPA vs un-watermarked reference: TNS normalized by $T_{clk}$ only "
                 r"($|TNS|/T_{clk}$, no #endpoints)", fontsize=11)
    c1.legend(frameon=False, fontsize=9, loc="upper left")
    # WNS panel is identical to the other figure (worst-path slack / TCP).
    bars(c2, "dWNS_pdmarks_tcp_pct", "dWNS_attack_tcp_pct",
         r"$\Delta$ WNS (% of TCP, +=better)", lambda h: f"{h:+.1f}")
    c2.set_xticklabels([])
    bars(c3, "dTNS_pdmarks_tcp_pct", "dTNS_attack_tcp_pct",
         r"$\Delta$ TNS (% of TCP, +=better)", lambda h: f"{h:+.0f}", symlog=10)
    c3.set_xticklabels(labels, fontsize=9)
    c3.annotate(r"$\Delta$TNS $= 100\,(TNS_{x}-TNS_{ref})/T_{clk}$  (symlog axis).  "
                "Attack drives the ASAP7 designs\nstrongly negative (worse); SweRV-NG45 "
                r"$+$ is re-route noise ($|TNS_{ref}|=365$ ns).",
                xy=(0.02, 0.03), xycoords="axes fraction",
                fontsize=6.8, color="#555555", ha="left", va="bottom")
    fig2.tight_layout()
    fig2.savefig(OUT_PNG_TCP, dpi=150)

    # --- companion figure: TNS as relative change vs reference --------------------
    #   dTNS_rel = (TNS_new - TNS_ref)/TNS_ref  (user formula).  TNS_ref<0 => + = WORSE.
    fig3, (d1, d2, d3) = plt.subplots(3, 1, figsize=(11.0, 9.4))
    bars(d1, "dWL_pdmarks_pct", "dWL_attack_pct",
         r"$\Delta$ routed wirelength (%)", lambda h: f"{h:+.2f}")
    d1.set_xticklabels([])
    d1.set_title(r"$\Delta$PPA vs un-watermarked reference: "
                 r"TNS relative to reference $(TNS_{ref}-TNS_{new})/TNS_{ref}$", fontsize=11)
    d1.legend(frameon=False, fontsize=9, loc="upper left")
    bars(d2, "dWNS_pdmarks_tcp_pct", "dWNS_attack_tcp_pct",
         r"$\Delta$ WNS (% of TCP, +=better)", lambda h: f"{h:+.1f}")
    d2.set_xticklabels([])
    # relative-% TNS: POSITIVE = better (|TNS| shrank).  symlog handles the -642% outlier.
    bars(d3, "dTNS_pdmarks_rel_pct", "dTNS_attack_rel_pct",
         r"$\Delta$ TNS (relative %, +=better)", lambda h: f"{h:+.0f}", symlog=10)
    d3.set_xticklabels(labels, fontsize=9)
    d3.annotate(r"$\Delta$TNS $= 100\,(TNS_{ref}-TNS_{new})/TNS_{ref}$  (symlog).  "
                r"$+$ = slack improved (better); attack drives ASAP7 designs strongly negative." + "\n"
                r"Ariane-ASAP7 undefined ($TNS_{ref}=0$); Ariane/BP-NG45 near closure "
                r"($|TNS_{ref}|<0.6$ ns) so % is inflated.",
                xy=(0.02, 0.03), xycoords="axes fraction",
                fontsize=6.8, color="#555555", ha="left", va="bottom")
    fig3.tight_layout()
    fig3.savefig(OUT_PNG_REL, dpi=150)

    # --- companion figure: symmetric (bounded) relative TNS change ---------------
    #   dTNS_sym = (|TNS_new|-|TNS_ref|)/(|TNS_new|+|TNS_ref|) in [-1,1]; + = worse.
    #   Same trend as the relative-% figure, but hard-bounded so nothing explodes.
    fig4, (e1, e2, e3) = plt.subplots(3, 1, figsize=(11.0, 9.4))
    bars(e1, "dWL_pdmarks_pct", "dWL_attack_pct",
         r"$\Delta$ routed wirelength (%)", lambda h: f"{h:+.2f}")
    e1.set_xticklabels([])
    e1.set_title(r"$\Delta$PPA vs un-watermarked reference: TNS as bounded symmetric change "
                 r"$(|TNS_{ref}|-|TNS_{new}|)/(|TNS_{new}|+|TNS_{ref}|)$", fontsize=10.5)
    e1.legend(frameon=False, fontsize=9, loc="upper left")
    bars(e2, "dWNS_pdmarks_tcp_pct", "dWNS_attack_tcp_pct",
         r"$\Delta$ WNS (% of TCP, +=better)", lambda h: f"{h:+.1f}")
    e2.set_xticklabels([])
    # bounded [-1,1]; POSITIVE = better.  No symlog/clip needed -- values can't explode.
    bars(e3, "dTNS_pdmarks_sym", "dTNS_attack_sym",
         r"TNS change index ($\pm$1, +=better)", lambda h: f"{h:+.2f}", ylim=(-1.05, 1.05))
    e3.set_xticklabels(labels, fontsize=9)
    e3.axhline(1.0, color="0.8", lw=0.7, ls=":")
    e3.axhline(-1.0, color="0.8", lw=0.7, ls=":")
    e3.annotate(r"bounded to $[-1,+1]$: $+1$ = $|TNS|\to0$ (fixed, better), $0$ = unchanged, "
                r"$-1$ = $|TNS|\to\infty$ (destroyed, worse)." + "\n"
                r"Same ordering as relative-% but no blow-up; Ariane-ASAP7 ($TNS_{ref}=0$) resolves to 0.",
                xy=(0.02, 0.03), xycoords="axes fraction",
                fontsize=6.8, color="#555555", ha="left", va="bottom")
    fig4.tight_layout()
    fig4.savefig(OUT_PNG_BND, dpi=150)

    print(f"[wrote] {OUT_CSV}")
    print(f"[wrote] {OUT_PNG}")
    print(f"[wrote] {OUT_PNG_TCP}")
    print(f"[wrote] {OUT_PNG_REL}")
    print(f"[wrote] {OUT_PNG_BND}")
    # console summary
    print(f"\n{'design':<16}{'q*':>5}{'qused':>7}{'dWL_wm%':>9}{'dWL_atk%':>10}"
          f"{'dTNS_wm%bud':>12}{'dTNS_atk%bud':>13}")
    for r in rows:
        print(f"{r['label']:<16}{r['qstar']:>5}{str(r['q_used']):>7}"
              f"{(r['dWL_pdmarks_pct'] or 0):>9.2f}{(r['dWL_attack_pct'] or 0):>10.2f}"
              f"{(r['dTNS_pdmarks_budget_pct'] or 0):>12.3f}{(r['dTNS_attack_budget_pct'] or 0):>13.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
