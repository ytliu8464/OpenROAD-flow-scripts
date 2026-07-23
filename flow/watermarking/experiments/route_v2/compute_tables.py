#!/usr/bin/env python3.12
# SPDX-License-Identifier: BSD-3-Clause
"""Recompute every routing-dependent paper number under the revised net-level
randomization test (paper Eqs. eq:routing_net_fraction ..
eq:routing_randomization_pvalue), reporting the *exact* net-level coincidence
probability.

Because every watermarked net carries zero canonical wrong-way wirelength
(q_R = 0), the coincidence probability P_{c,R} = P[T_R^{(b)} <= T_R] is the
exact combinatorial tail C(n_0,k)/C(E,k) (rigorous upper bound C(n_le,k)/C(E,k)
when a few selected nets are non-zero).  This is the assumption-free analog of
the placement/CTS exact Bernoulli tail 0.5^{X_s}; a Monte-Carlo randomization
estimate would merely floor it at 1/(B+1).  The exact tails are astronomically
small (10^-253 .. 10^-1093), so we report the conservative rigorous bound
P_{c,R} < 10^{-CAP} (CAP=100) -- below every placement/CTS value and baseline.

Produces JSON + printed blocks for:
  * tab:ppa      R-only P_c and NG45 all-stage P_c
  * tab:survival (T_R, p_R) at post-DRT, plus post-DRT r_all
  * tab:sensitivity  route rows (T_R, p_R) for the f and lambda sweeps

Run with:  python3.12 route_v2/compute_tables.py   (numpy required)
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

EXP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXP))
from lib.route_stat_v2 import (per_net_qr, read_watermark_nets,
                               build_qr_vector, observed_T, exact_tail_log10)

ALPHA_R = 1e-4
CAP_LOG10 = -100          # report P_c < 10^{CAP_LOG10} when the true tail is below it
QR = EXP / "results" / "phase_route_v2"
OUT = EXP / "results" / "phase_route_v2"
PHASE1 = EXP / "results" / "phase1" / "raw"

DESIGNS = [
    ("JPEG",   "jpeg",           "jpeg"),
    ("SweRV",  "swerv_wrapper",  "swerv_wrapper"),
    ("Ariane", "ariane136",      "ariane136"),
    ("BP",     "bp_multi",       "bp_multi"),
]


def _fmt_pc(log10p):
    """Capped reporting: '< 1e-100' when the exact tail is below the cap,
    else the actual value in 1.0e-NN form."""
    if log10p is None:
        return "--"
    if log10p <= CAP_LOG10:
        return f"$<10^{{{CAP_LOG10}}}$"
    mant = 10 ** (log10p - math.floor(log10p))
    return f"{mant:.1f}e{int(math.floor(log10p))}"


class Stat:
    __slots__ = ("T_R", "log10_pc", "k", "E", "q_sel", "q_unsel", "ssum")

    def __init__(self, T_R, log10_pc, k, E, q_sel, q_unsel, ssum):
        self.T_R, self.log10_pc, self.k, self.E = T_R, log10_pc, k, E
        self.q_sel, self.q_unsel, self.ssum = q_sel, q_unsel, ssum


def _stat_for(label_csv, wm_txt):
    csv = QR / f"{label_csv}.csv"
    if not csv.exists():
        print(f"  [miss] {label_csv}.csv", flush=True)
        return None
    counts = per_net_qr(csv)
    wm = read_watermark_nets(wm_txt)
    vec = build_qr_vector(counts, wm)
    T_R = observed_T(vec.q, vec.sel)
    log10_pc = exact_tail_log10(vec.q, vec.sel)
    k = vec.k
    m = vec.n_eligible - k
    q_sel = float(vec.q[vec.sel].mean()) if k > 0 else 0.0
    q_unsel = float(vec.q[~vec.sel].mean()) if m > 0 else 0.0
    ssum = float(vec.q[vec.sel].sum())
    st = Stat(T_R, log10_pc, k, vec.n_eligible, q_sel, q_unsel, ssum)
    print(f"  [stat] {label_csv:30} k={k:>5} E={vec.n_eligible:>7} "
          f"T_R={T_R:+.3e} log10 P_cR={log10_pc:.1f} "
          f"({_fmt_pc(log10_pc)})", flush=True)
    return st


def _pc_stage(X, x, p=0.5):
    return sum(math.comb(X, i) * ((1 - p) ** i) * (p ** (X - i))
              for i in range(0, min(x, X) + 1))


def _log10_pc_stage(X, x, p=0.5):
    v = _pc_stage(X, x, p)
    return math.log10(v) if v > 0 else float("-inf")


def compute_ppa():
    rows = []
    for label, nick, did in DESIGNS:
        rres = EXP / "results" / "nangate45" / nick / "pdmarks-r-only"
        ares = EXP / "results" / "nangate45" / nick / "pdmarks-all-stage"
        r_stat = _stat_for(f"ronly_{nick}", rres / "watermark_nets.txt")
        a_stat = _stat_for(f"allstage_{nick}", ares / "watermark_nets.txt")

        j = PHASE1 / f"ppa_nangate45_{nick if nick!='bp_multi' else 'bp_multi_top'}_all_stage.json"
        lpcP = lpcC = None
        if j.exists():
            d = json.loads(j.read_text())
            XP, xP = d.get("X_P"), d.get("x_P")
            XC, xC = d.get("X_C"), d.get("x_C")
            if isinstance(XP, int) and XP > 0:
                lpcP = _log10_pc_stage(XP, xP)
            if isinstance(XC, int) and XC > 0:
                lpcC = _log10_pc_stage(XC, xC)

        lpc_r = r_stat.log10_pc if r_stat else None
        lpc_a_route = a_stat.log10_pc if a_stat else None
        lpc_all = None
        if lpcP is not None and lpcC is not None and lpc_a_route is not None:
            lpc_all = lpcP + lpcC + lpc_a_route

        rows.append({
            "design": label, "nick": nick,
            "r_only": {"T_R": r_stat.T_R if r_stat else None,
                       "log10_P_c": lpc_r, "P_c_cell": _fmt_pc(lpc_r),
                       "k": r_stat.k if r_stat else None,
                       "E_R": r_stat.E if r_stat else None,
                       "q_sel": r_stat.q_sel if r_stat else None,
                       "q_unsel": r_stat.q_unsel if r_stat else None},
            "all_stage": {"T_R": a_stat.T_R if a_stat else None,
                          "log10_P_cR": lpc_a_route,
                          "log10_P_cP": lpcP, "log10_P_cC": lpcC,
                          "log10_P_c": lpc_all, "P_c_cell": _fmt_pc(lpc_all),
                          "k": a_stat.k if a_stat else None,
                          "E_R": a_stat.E if a_stat else None},
        })
    return rows


def compute_survival():
    paper_rPC = {
        "jpeg":          (0.969, 1.000),
        "swerv_wrapper": (0.989, 1.000),
        "ariane136":     (1.000, 1.000),
        "bp_multi":      (1.000, 1.000),
    }
    rows = []
    for label, nick, did in DESIGNS:
        ares = EXP / "results" / "nangate45" / nick / "pdmarks-all-stage"
        st = _stat_for(f"allstage_{nick}", ares / "watermark_nets.txt")
        lpc = st.log10_pc if st else None
        # r_R = 1{p_R <= alpha_R}: exact tail << alpha_R everywhere
        rR = 1.0 if (lpc is not None and lpc <= math.log10(ALPHA_R)) else \
            (0.0 if lpc is not None else None)
        rP, rC = paper_rPC[nick]
        parts = [v for v in (rP, rC, rR) if v is not None]
        r_all = sum(parts) / len(parts) if parts else None
        rows.append({"design": label, "nick": nick,
                     "T_R": st.T_R if st else None, "log10_P_c": lpc,
                     "P_c_cell": _fmt_pc(lpc),
                     "r_R": rR, "r_P": rP, "r_C": rC, "r_all": r_all})
    return rows


def compute_sensitivity():
    base = EXP / "results" / "nangate45" / "swerv_wrapper"
    variants = [
        ("f", "0.025", "sens-r-f-0.025"),
        ("f", "0.05",  "sens-r-f-0.05"),
        ("f", "0.10",  "sens-r-f-0.10"),
        ("lambda_wm", "10",   "sens-r-lambda_wm-10"),
        ("lambda_wm", "100",  "sens-r-lambda_wm-100"),
        ("lambda_wm", "1000", "sens-r-lambda_wm-1000"),
    ]
    rows = []
    r_stat = _stat_for("ronly_swerv_wrapper",
                       base / "pdmarks-r-only" / "watermark_nets.txt")
    if r_stat:
        rows.append({"param": "default(r-only)", "value": "-",
                     "variant": "pdmarks-r-only", "T_R": r_stat.T_R,
                     "log10_P_c": r_stat.log10_pc, "P_c_cell": _fmt_pc(r_stat.log10_pc),
                     "k": r_stat.k, "E_R": r_stat.E})
    for param, value, var in variants:
        st = _stat_for(f"sens_swerv_{var}", base / var / "watermark_nets.txt")
        rows.append({"param": param, "value": value, "variant": var,
                     "T_R": st.T_R if st else None,
                     "log10_P_c": st.log10_pc if st else None,
                     "P_c_cell": _fmt_pc(st.log10_pc) if st else "--",
                     "k": st.k if st else None, "E_R": st.E if st else None})
    return rows


def main():
    print("== ppa ==", flush=True)
    ppa = compute_ppa()
    print("== survival ==", flush=True)
    surv = compute_survival()
    print("== sensitivity ==", flush=True)
    sens = compute_sensitivity()
    (OUT / "route_v2_ppa.json").write_text(json.dumps(ppa, indent=2))
    (OUT / "route_v2_survival.json").write_text(json.dumps(surv, indent=2))
    (OUT / "route_v2_sensitivity.json").write_text(json.dumps(sens, indent=2))

    print("\n" + "=" * 78)
    print(f"tab:ppa  routing coincidence probability (NG45)   [cap 10^{CAP_LOG10}]")
    print("=" * 78)
    print(f"{'design':8} {'k(R)':>6} {'E_R':>7} {'q_sel':>7} {'q_unsel':>8} "
          f"{'T_R(R)':>10} {'log10 P_cR':>11} {'R-only P_c':>12} {'all-stage P_c':>14}")
    for r in ppa:
        ro, al = r["r_only"], r["all_stage"]
        print(f"{r['design']:8} {ro['k']!s:>6} {ro['E_R']!s:>7} "
              f"{ro['q_sel']:.4f} {ro['q_unsel']:>8.4f} {ro['T_R']:>10.3e} "
              f"{ro['log10_P_c']:>11.1f} {ro['P_c_cell']:>12} {al['P_c_cell']:>14}")

    print("\n" + "=" * 78)
    print("tab:survival  post-DRT routing (NG45)")
    print("=" * 78)
    print(f"{'design':8} {'T_R':>11} {'log10P_c':>9} {'r_R':>4} {'r_P':>6} "
          f"{'r_C':>6} {'r_all':>7}")
    for r in surv:
        print(f"{r['design']:8} {r['T_R']:>11.3e} {r['log10_P_c']:>9.1f} "
              f"{r['r_R']!s:>4} {r['r_P']:>6.3f} {r['r_C']:>6.3f} {r['r_all']:>7.4f}")

    print("\n" + "=" * 78)
    print("tab:sensitivity  route rows (SweRV NG45)")
    print("=" * 78)
    print(f"{'param':14} {'value':>6} {'k':>7} {'E_R':>7} {'T_R':>11} "
          f"{'log10P_c':>9} {'P_c':>12}")
    for r in sens:
        tr = f"{r['T_R']:.3e}" if r['T_R'] is not None else "--"
        lp = f"{r['log10_P_c']:.1f}" if r['log10_P_c'] is not None else "--"
        print(f"{r['param']:14} {r['value']:>6} {r['k']!s:>7} {r['E_R']!s:>7} "
              f"{tr:>11} {lp:>9} {r['P_c_cell']:>12}")


if __name__ == "__main__":
    main()
