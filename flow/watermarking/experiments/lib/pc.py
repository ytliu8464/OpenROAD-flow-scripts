# SPDX-License-Identifier: BSD-3-Clause
"""Coincidence-probability helpers (Eqs. eq:pc_stage / eq:pc_total in main.tex).

P_{c,s} = sum_{i=0}^{x_s} C(X_s, i) * (1-p_s)^i * p_s^{X_s-i}

For placement and CTS (parity bit): p_s = 0.5.
For routing: p_R is estimated empirically from the wrong-key null distribution
of p_R (one-sided p-value uniform under H0 on the wrong-key run); we use the
empirical fraction of wrong-key p_R values below the observed p_R.
"""

from __future__ import annotations

import math
from typing import Iterable


def pc_stage(big_x: int, small_x: int, p_s: float = 0.5) -> float:
    """Eq. eq:pc_stage from main.tex."""
    if big_x <= 0:
        return 1.0
    q = 1.0 - p_s
    log_total = -math.inf
    # Sum P[mismatch_count <= small_x] under H0 (each claim Bernoulli p_s).
    # Equivalently: sum_{i=0..x} C(X,i) (1-p)^i p^(X-i)
    total = 0.0
    for i in range(0, min(small_x, big_x) + 1):
        term = math.comb(big_x, i) * (q ** i) * (p_s ** (big_x - i))
        total += term
    return total


def pc_routing_empirical(p_R_observed: float, p_R_wrong_key: Iterable[float]) -> float:
    """Empirical coincidence probability for the routing stage.

    Returns the fraction of wrong-key p_R values <= the observed p_R.  This is
    the natural empirical estimator of P[H0 accepts]; if no wrong-key samples
    are given we fall back to p_R itself (the analytic one-sided p-value).
    """
    samples = [float(x) for x in p_R_wrong_key]
    if not samples:
        return float(p_R_observed)
    n_le = sum(1 for v in samples if v <= p_R_observed)
    return n_le / len(samples)


def pc_total(*pcs: float) -> float:
    out = 1.0
    for v in pcs:
        out *= float(v)
    return out


__all__ = ["pc_stage", "pc_routing_empirical", "pc_total"]
