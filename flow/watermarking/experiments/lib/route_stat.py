# SPDX-License-Identifier: BSD-3-Clause
r"""Routing-stage statistical verification.

The carrier is the per-net *wrong-way wirelength fraction*

    q_R(n) = l_ww(n) / l_tot(n)

and the observed statistic is the difference in mean q_R between the
watermarked net set WM_R and the rest of the eligible set E_R:

    T_R = mean_{n in WM_R} q_R(n) - mean_{n in E_R \ WM_R} q_R(n)

More negative T_R = stronger ownership evidence.  Significance is assessed
with a *net-level* randomization test: draw B uniform k-subsets of E_R
(k = |WM_R|) from a stream seeded by the public design id, and report

    p_R = (1 + #{ b : T_R^{(b)} <= T_R }) / (B + 1).

The null depends only on k and the fixed q_R vector, so wrong-key trials with
the same k reuse one null table.  The minimum nonzero p_R is 1/(B+1); use
``exact_tail_log10`` when the Monte-Carlo floor is not small enough.

Input CSVs come from ``tools/dump_route_qr.py``; everything here is pure numpy
and runs in the harness.
"""

from __future__ import annotations

import csv
import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Set, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def per_net_qr(path: Path) -> Dict[str, Tuple[int, int]]:
    """Read {net -> (ww_len, tot_len)} from a ``dump_route_qr.py`` CSV.

    Nets with ``tot_len == 0`` (no planar wirelength) are *kept* here; the
    public key-independent filter is applied in ``build_qr_vector`` so that one
    rule governs both WM_R and E_R.
    """
    out: Dict[str, Tuple[int, int]] = {}
    with open(path) as f:
        rd = csv.DictReader(f)
        for row in rd:
            try:
                out[row["net"]] = (int(row["ww_len"]), int(row["tot_len"]))
            except (KeyError, TypeError, ValueError):
                continue
    return out


def read_watermark_nets(path: Path) -> Set[str]:
    """Read ``watermark_nets.txt`` (one net name per line)."""
    if not Path(path).exists():
        return set()
    return {ln.strip() for ln in Path(path).read_text().splitlines() if ln.strip()}


# ---------------------------------------------------------------------------
# Vector construction
# ---------------------------------------------------------------------------

@dataclass
class QRVector:
    names: np.ndarray          # (E,) net names, filtered to tot_len>0
    q: np.ndarray              # (E,) per-net wrong-way fraction q_R(n)
    sel: np.ndarray            # (E,) bool mask of WM_R membership
    k: int                     # |WM_R ∩ E_R|
    n_eligible: int            # |E_R|

    @property
    def selected_names(self) -> Set[str]:
        return set(self.names[self.sel].tolist())


def build_qr_vector(counts: Dict[str, Tuple[int, int]],
                    wm_set: Set[str]) -> QRVector:
    """Filter to eligible nets (tot_len>0), compute q_R, and mark WM_R."""
    names = []
    q = []
    sel = []
    for name, (ww, tot) in counts.items():
        if tot <= 0:
            continue
        names.append(name)
        q.append(ww / tot)
        sel.append(name in wm_set)
    names = np.array(names, dtype=object)
    q = np.asarray(q, dtype=np.float64)
    sel = np.asarray(sel, dtype=bool)
    return QRVector(names=names, q=q, sel=sel, k=int(sel.sum()),
                    n_eligible=int(names.size))


# ---------------------------------------------------------------------------
# Observed statistic
# ---------------------------------------------------------------------------

def observed_T(q: np.ndarray, sel: np.ndarray) -> float:
    """T_R = mean q over selected - mean q over unselected."""
    k = int(sel.sum())
    m = q.size - k
    if k == 0 or m == 0:
        return 0.0
    tot = q.sum()
    ssum = q[sel].sum()
    return float(ssum / k - (tot - ssum) / m)


# ---------------------------------------------------------------------------
# Net-level randomization test
# ---------------------------------------------------------------------------

def design_seed(design_id: str) -> int:
    """Deterministic 64-bit PRNG seed = first 8 bytes of SHA256(design_id).

    Each null draw is a uniform k-subset of E_R, reproducible from public
    inputs alone.  We realize that null with a PCG64 stream seeded from the
    design id; the p-value depends only on the null being uniform k-subsets,
    not on the particular hash used to serialize the draw.
    """
    return int.from_bytes(hashlib.sha256(design_id.encode("utf-8")).digest()[:8],
                          "big", signed=False)


def randomization_pvalue(q: np.ndarray,
                         seed: int,
                         k: int,
                         T_obs: float,
                         B: int = 100_000,
                         chunk: int = 1024,
                         return_null: bool = False):
    """One-sided net-level randomization p-value.

    For each of B trials, draw a uniform k-subset WM_R^{(b)} of E_R and compute
    T_R^{(b)}.  The selected-subset sum fully determines T_R^{(b)} because the
    total sum is fixed, so only the top-k partition per trial is needed.

    Returns p_R (and, if ``return_null``, the length-B array of T_R^{(b)}).
    """
    E = q.size
    m = E - k
    if k == 0 or m == 0:
        return (1.0, np.array([])) if return_null else 1.0
    tot = float(q.sum())
    a = 1.0 / k + 1.0 / m       # T^{(b)} = ssum*a - tot/m
    b_const = tot / m
    rng = np.random.default_rng(seed)

    le = 0                      # #{ T^{(b)} <= T_obs }
    null = np.empty(B, dtype=np.float64) if return_null else None
    done = 0
    while done < B:
        t = min(chunk, B - done)
        r = rng.random((t, E))
        idx = np.argpartition(r, k - 1, axis=1)[:, :k]
        ssum = q[idx].sum(axis=1)                        # (t,)
        Tb = ssum * a - b_const
        le += int(np.count_nonzero(Tb <= T_obs + 1e-15))
        if return_null:
            null[done:done + t] = Tb
        done += t
    p = (1 + le) / (B + 1)
    return (p, null) if return_null else p


def exact_tail_log10(q: np.ndarray, sel: np.ndarray) -> float:
    """log10 of a rigorous upper bound on the randomization tail
    P[T_R^{(b)} <= T_R], EXACT when the selected nets carry zero wrong-way
    wirelength.

    Because q_R(n) >= 0, the event ssum <= ssum_obs (equivalently
    T_R <= T_obs) implies every selected net has q_R <= ssum_obs.  Hence

        P[ssum <= ssum_obs] <= C(n_le, k) / C(E, k),
        n_le = #{ n in E_R : q_R(n) <= ssum_obs }.

    When ssum_obs = 0 (all watermarked nets are wrong-way-free), n_le is
    exactly the count of zero-q_R nets and the bound is the *exact* tail:

        P_{c,R} = C(n_0, k) / C(E, k) = prod_{i=0}^{k-1} (n_0 - i)/(E - i).

    This is the assumption-free coincidence probability of the routing stage
    (the direct analog of the placement/CTS exact Bernoulli tail 0.5^{X_s});
    a Monte-Carlo estimate would merely floor it at 1/(B+1).
    """
    E = q.size
    k = int(sel.sum())
    if k == 0 or k >= E:
        return 0.0
    ssum_obs = float(q[sel].sum())
    n_le = int(np.count_nonzero(q <= ssum_obs + 1e-15))
    if n_le < k:
        return float("-inf")
    lp = 0.0
    for i in range(k):
        lp += math.log10((n_le - i) / (E - i))
    return lp


def randomization_pvalue_normal(q: np.ndarray, k: int, T_obs: float) -> float:
    """Analytic normal approximation of the randomization p-value.

    Under sampling k nets without replacement from E_R the randomization null
    of T_R is asymptotically normal with

        E[T]      = 0
        Var(ssum) = k*(E-k)/(E-1) * Var_pop(q)          (finite-population)
        Var(T)    = a^2 * Var(ssum),   a = 1/k + 1/(E-k)

    so p_R ~= Phi(T_obs / sd_T).  Used for large wrong-key sweeps where a full
    B=100k randomization per key is infeasible; validated against the exact
    test in the bulk (the regime that sets the wrong-key mean/max).  Clamping
    to the empirical floor 1/(B+1) is the caller's responsibility.
    """
    E = q.size
    m = E - k
    if k == 0 or m == 0:
        return 1.0
    var_pop = float(q.var())                    # population variance (ddof=0)
    var_ssum = k * (E - k) / (E - 1) * var_pop
    a = 1.0 / k + 1.0 / m
    sd_T = math.sqrt(a * a * var_ssum) if var_ssum > 0 else 0.0
    if sd_T == 0.0:
        return 1.0
    z = T_obs / sd_T
    # lower tail: more-negative T => stronger evidence => smaller p
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


# ---------------------------------------------------------------------------
# Convenience wrapper
# ---------------------------------------------------------------------------

@dataclass
class RouteStat:
    T_R: float
    p_R: float
    q_sel_mean: float
    q_unsel_mean: float
    k: int
    n_eligible: int
    design_id: str
    B: int

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def route_stat_from_qr(counts: Dict[str, Tuple[int, int]],
                       wm_set: Set[str],
                       design_id: str,
                       B: int = 100_000,
                       return_null: bool = False):
    """End-to-end: per-net (ww_len, tot_len) + WM_R -> RouteStat(T_R, p_R)."""
    vec = build_qr_vector(counts, wm_set)
    T_obs = observed_T(vec.q, vec.sel)
    res = randomization_pvalue(vec.q, design_seed(design_id), vec.k, T_obs, B=B,
                               return_null=return_null)
    if return_null:
        p_R, null = res
    else:
        p_R, null = res, None
    k = vec.k
    m = vec.n_eligible - k
    q_sel = float(vec.q[vec.sel].mean()) if k > 0 else 0.0
    q_unsel = float(vec.q[~vec.sel].mean()) if m > 0 else 0.0
    stat = RouteStat(T_R=T_obs, p_R=p_R, q_sel_mean=q_sel,
                     q_unsel_mean=q_unsel, k=k,
                     n_eligible=vec.n_eligible, design_id=design_id, B=B)
    return (stat, null) if return_null else stat


__all__ = [
    "per_net_qr", "read_watermark_nets", "QRVector", "build_qr_vector",
    "observed_T", "design_seed", "randomization_pvalue",
    "randomization_pvalue_normal", "exact_tail_log10",
    "RouteStat", "route_stat_from_qr",
]
