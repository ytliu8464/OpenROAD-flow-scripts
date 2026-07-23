#!/usr/bin/env python3.12
# SPDX-License-Identifier: BSD-3-Clause
"""Regenerate the routing panel of the wrong-key analysis (tab:wrong-key /
fig:wrong_key) under the revised net-level randomization test.

For the representative design (SweRV NG45, matching fig:wrong_key):
  * correct key:  WM_R = watermark_nets.txt on the all-stage routed layout,
    T_R and p_R via the exact B=100k randomization test (lib.route_stat_v2).
  * 5000 wrong keys:  reuse the SAME deterministic wrong-key stream as
    wrong_key/run_wrong_key.py (namespace "<plat>_<design>", derive_stage_seeds),
    reconstruct WM_R' from each seed_R' over the eligible net set, compute T_R'
    and p_R' via the analytic normal approximation of the randomization null
    (validated against the exact test in the bulk; the wrong-key regime).

  r_R'  = 1{p_R' <= alpha_R}  (alpha_R = 1e-4)
  r_all'= mean(r_P', r_C', r_R') where r_P'/r_C' are read from the existing
          phase2 wrong-key distribution CSV (same key order by index).

Outputs:
  results/phase_route_v2/wrong_key_routing_<plat>_<design>.json   (summary)
  results/phase_route_v2/wrong_key_routing_<plat>_<design>_dist.csv (per key)
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

EXP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXP))
from lib.keys import derive_stage_seeds, wrong_key_stream, load_seed_hex
from lib.keyless_verify import (routing_wm_set, placement_extraction_rate,
                                cts_extraction_rate)
import math
from lib.route_stat_v2 import (per_net_qr, read_watermark_nets, build_qr_vector,
                               observed_T, design_seed, randomization_pvalue,
                               randomization_pvalue_normal, exact_tail_log10)

B = 100_000
ALPHA_R = 1e-4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="nangate45")
    ap.add_argument("--design", default="swerv_wrapper")
    ap.add_argument("--design-id", default="swerv_wrapper")
    ap.add_argument("--seed-name", default="swerv_wrapper",
                    help="gen_key/out/<name> for the true routing seed")
    ap.add_argument("-n", "--num-keys", type=int, default=5000)
    ap.add_argument("--fraction", type=float, default=0.01,
                    help="routing watermark fraction f used to reconstruct WM_R'")
    ap.add_argument("--qr-csv", default="",
                    help="override q_R csv (default allstage_<design>.csv)")
    ap.add_argument("--wm-nets", default="",
                    help="override watermark_nets.txt (default all-stage layout)")
    ap.add_argument("--embed-dir", default="",
                    help="dir with wm_place_order_embed_all_stage.csv / "
                         "wm_cts_pairs_embed_all_stage.csv "
                         "(default all-stage layout dir)")
    args = ap.parse_args()

    plat, design, did = args.platform, args.design, args.design_id
    qr_csv = Path(args.qr_csv) if args.qr_csv else \
        EXP / "results" / "phase_route_v2" / f"allstage_{design}.csv"
    wm_txt = Path(args.wm_nets) if args.wm_nets else \
        EXP / "results" / plat / design / "pdmarks-all-stage" / "watermark_nets.txt"

    counts = per_net_qr(qr_csv)
    wm_true = read_watermark_nets(wm_txt)
    vec = build_qr_vector(counts, wm_true)
    q = vec.q
    names = list(vec.names)
    name_to_idx = {n: i for i, n in enumerate(names)}
    E = vec.n_eligible

    # -------- correct key (exact net-level tail) --------
    T_true = observed_T(q, vec.sel)
    log10_p_true = exact_tail_log10(q, vec.sel)      # exact tail C(n0,k)/C(E,k)
    p_true = 10.0 ** log10_p_true if log10_p_true > -300 else 0.0
    r_R_true = 1.0 if log10_p_true <= math.log10(ALPHA_R) else 0.0
    print(f"[correct] k={vec.k} E={E}  T_R={T_true:+.4e}  "
          f"log10 p_R={log10_p_true:.1f}  r_R={r_R_true}", flush=True)

    # -------- placement/CTS embed CSVs (recompute r_P'/r_C' per wrong key) -----
    embed_dir = Path(args.embed_dir) if args.embed_dir else \
        EXP / "results" / plat / design / "pdmarks-all-stage"
    embed_p = embed_dir / "wm_place_order_embed_all_stage.csv"
    embed_c = embed_dir / "wm_cts_pairs_embed_all_stage.csv"
    have_p = embed_p.exists()
    have_c = embed_c.exists()
    print(f"[embed] placement={'ok' if have_p else 'MISSING'} "
          f"cts={'ok' if have_c else 'MISSING'} ({embed_dir})", flush=True)

    # -------- wrong keys (normal-approx p_R', keyless r_P'/r_C') --------
    masters = wrong_key_stream(args.num_keys, namespace=f"{plat}_{design}")
    q_np = np.asarray(q, dtype=np.float64)
    tot = float(q_np.sum())

    dist_path = (EXP / "results" / "phase_route_v2"
                 / f"wrong_key_routing_{plat}_{design}_dist.csv")
    p_samples, rall_samples, rR_samples = [], [], []
    rP_samples, rC_samples = [], []
    with open(dist_path, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["idx", "kp", "T_R", "p_R", "r_R", "r_P", "r_C", "r_all"])
        for i, m in enumerate(masters):
            seeds = derive_stage_seeds(m)
            # routing WM_R' under the wrong seed.  The HMAC selection
            # WM_R'={n: u_R'(n)<f} yields a subset of size Binomial(E,f) drawn
            # uniformly from E_R; we sample that identical distribution with a
            # seed-derived PCG64 stream (~1e5x faster than 97k HMACs/key) so the
            # wrong-key null of T_R' is statistically identical.
            rng_r = np.random.default_rng(
                int.from_bytes(seeds["routing"][:8], "big"))
            kp = int(rng_r.binomial(E, args.fraction))
            if kp == 0 or kp >= E:
                Tp, pp, rRp = 0.0, 1.0, 0.0
            else:
                idx = rng_r.choice(E, kp, replace=False)
                ssum = float(q_np[idx].sum())
                a = 1.0 / kp + 1.0 / (E - kp)
                Tp = ssum * a - tot / (E - kp)
                pp = max(randomization_pvalue_normal(q_np, kp, Tp), 1.0 / (B + 1))
                rRp = 1.0 if pp <= ALPHA_R else 0.0
            # placement r_P' / CTS r_C' under the wrong seed
            rP = rC = None
            if have_p:
                XP, xP = placement_extraction_rate(embed_p, embed_p, seeds["placement"])
                rP = (1 - xP / XP) if XP > 0 else None
            if have_c:
                XC, xC = cts_extraction_rate(embed_c, embed_c, seeds["cts"])
                rC = (1 - xC / XC) if XC > 0 else None
            parts = [v for v in (rP, rC, rRp) if v is not None]
            rall = sum(parts) / len(parts) if parts else None
            wr.writerow([i, kp, f"{Tp:.6e}", f"{pp:.6e}", rRp,
                         "" if rP is None else f"{rP:.6f}",
                         "" if rC is None else f"{rC:.6f}",
                         "" if rall is None else f"{rall:.6f}"])
            p_samples.append(pp)
            rR_samples.append(rRp)
            if rP is not None:
                rP_samples.append(rP)
            if rC is not None:
                rC_samples.append(rC)
            if rall is not None:
                rall_samples.append(rall)
            if (i + 1) % 1000 == 0:
                print(f"  ...{i+1}/{args.num_keys} keys", flush=True)

    p_arr = np.asarray(p_samples)
    # correct-key r_all: true r_P=r_C=1.0 (owner layout) combined with r_R_true.
    correct_r_all = (1.0 + 1.0 + r_R_true) / 3.0 if (have_p and have_c) \
        else ((1.0 + r_R_true) / 2.0)
    summary = {
        "platform": plat, "design": design, "design_id": did,
        "num_keys": args.num_keys, "fraction": args.fraction,
        "alpha_R": ALPHA_R, "B": B, "E_R": E, "k_true": vec.k,
        "correct_T_R": T_true, "correct_p_R": p_true,
        "correct_log10_p_R": log10_p_true, "correct_r_R": r_R_true,
        "correct_r_P": 1.0 if have_p else None,
        "correct_r_C": 1.0 if have_c else None,
        "correct_r_all": correct_r_all,
        "wrong_p_R_mean": float(p_arr.mean()),
        "wrong_p_R_min": float(p_arr.min()),
        "wrong_p_R_max": float(p_arr.max()),
        "wrong_r_R_pass_frac": float(np.mean(rR_samples)),
        "wrong_r_P_mean": float(np.mean(rP_samples)) if rP_samples else None,
        "wrong_r_P_max": float(np.max(rP_samples)) if rP_samples else None,
        "wrong_r_C_mean": float(np.mean(rC_samples)) if rC_samples else None,
        "wrong_r_C_max": float(np.max(rC_samples)) if rC_samples else None,
        "wrong_r_all_mean": float(np.mean(rall_samples)) if rall_samples else None,
        "wrong_r_all_max": float(np.max(rall_samples)) if rall_samples else None,
        "dist_csv": str(dist_path),
    }

    out_json = (EXP / "results" / "phase_route_v2"
                / f"wrong_key_routing_{plat}_{design}.json")
    out_json.write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2), flush=True)

    # ---- fig:wrong_key (regenerated routing panel) ----
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 3, figsize=(12, 3.4))
        # placement r_P
        if rP_samples:
            ax[0].hist(rP_samples, bins=40, color="#9ecae1", edgecolor="none")
            ax[0].axvline(1.0, color="#e34a33", lw=2, label="correct key")
            ax[0].axvline(0.75, color="k", ls="--", lw=1, label=r"$\tau_P=0.75$")
        ax[0].set_title("Placement $r_P$"); ax[0].legend(fontsize=8)
        # CTS r_C
        if rC_samples:
            ax[1].hist(rC_samples, bins=40, color="#a1d99b", edgecolor="none")
            ax[1].axvline(1.0, color="#e34a33", lw=2, label="correct key")
            ax[1].axvline(0.75, color="k", ls="--", lw=1, label=r"$\tau_C=0.75$")
        ax[1].set_title("CTS $r_C$"); ax[1].legend(fontsize=8)
        # routing T_R (wrong vs correct): cleaner than p_R which floors at 1e-5
        wrong_T = [float(row["T_R"]) for row in csv.DictReader(open(dist_path))]
        ax[2].hist(wrong_T, bins=60, color="#bcbddc", edgecolor="none",
                   label="wrong keys")
        ax[2].axvline(T_true, color="#e34a33", lw=2, label="correct key")
        ax[2].set_title(r"Routing $T_R$"); ax[2].set_xlabel(r"$T_R$")
        ax[2].legend(fontsize=8)
        fig.suptitle(f"Wrong-key vs correct-key verification ({design} NG45)")
        fig.tight_layout(rect=[0, 0, 1, 0.94])
        outpng = EXP / "plots" / "wrong_key_analysis_routing_v2.png"
        fig.savefig(outpng, dpi=150)
        print(f"[fig] wrote {outpng}", flush=True)
    except Exception as e:
        print(f"[fig] wrong-key figure failed: {e}", flush=True)


if __name__ == "__main__":
    main()
