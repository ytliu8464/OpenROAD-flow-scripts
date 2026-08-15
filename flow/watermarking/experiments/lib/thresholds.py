# SPDX-License-Identifier: BSD-3-Clause
"""Per-stage and combined ownership thresholds (paper Tab. wrong-key).

The paper specifies the following defaults for the ownership decision:

    tau_P    = 0.75    (placement extraction rate threshold)
    tau_C    = 0.75    (CTS extraction rate threshold)
    alpha_R  = 1e-4    (routing p-value threshold)

The ownership claim is accepted iff at least two of the three per-stage tests
pass (paper Eq. eq:ownership_rule).  The combined statistic r_all is the mean
of the available per-stage extraction rates; it is reported as an aggregate
confidence summary and does NOT gate the decision.  tau_all is retained only
as an optional reporting floor (default disabled).
"""
from __future__ import annotations

from typing import Optional, Dict, Iterable

TAU_P:   float = 0.75
TAU_C:   float = 0.75
ALPHA_R: float = 1e-4
TAU_ALL: Optional[float] = None   # r_all is reported, not gated


def _r_all(stage_vals: Iterable[Optional[float]]) -> Optional[float]:
    """Mean of the per-stage extraction rates that are actually available."""
    parts = [v for v in stage_vals if v is not None]
    return (sum(parts) / len(parts)) if parts else None


def ownership_pass(r_P: Optional[float],
                   r_C: Optional[float],
                   p_R: Optional[float],
                   *,
                   tau_P: float = TAU_P,
                   tau_C: float = TAU_C,
                   alpha_R: float = ALPHA_R,
                   tau_all: Optional[float] = TAU_ALL) -> Dict[str, object]:
    """Compute r_R, r_all, per-stage pass flags, and final ownership decision.

    Stages whose extraction rate is None (e.g. an unavailable channel) are
    treated as not contributing.  The final decision uses the paper's rule:
    accept iff at least two of {pass_P, pass_C, pass_R} are True.  The combined
    statistic r_all is reported for context only; if a numeric ``tau_all`` is
    supplied it is recorded in ``pass_all`` but never affects ``accept``.

    Returns:
        dict with keys
            r_R         (0/1 if p_R is given, else None)
            r_all       (mean of available per-stage rates, else None)
            pass_P / pass_C / pass_R    (per-stage threshold passes)
            num_pass    (count of stages that passed)
            pass_all    (r_all >= tau_all if tau_all given, else None)
            accept      (final ownership decision: num_pass >= 2)
    """
    r_R: Optional[float]
    if p_R is None:
        r_R = None
    else:
        r_R = 1.0 if p_R <= alpha_R else 0.0

    r_all = _r_all((r_P, r_C, r_R))

    pass_P = (r_P is not None and r_P >= tau_P)
    pass_C = (r_C is not None and r_C >= tau_C)
    pass_R = (r_R is not None and r_R >= 1.0)   # r_R is 0 or 1
    num_pass = int(pass_P) + int(pass_C) + int(pass_R)

    # r_all is an aggregate confidence summary, not a gate.
    pass_all = None if tau_all is None else (r_all is not None and r_all >= tau_all)
    accept   = bool(num_pass >= 2)

    return {
        "r_R": r_R,
        "r_all": r_all,
        "pass_P": pass_P,
        "pass_C": pass_C,
        "pass_R": pass_R,
        "num_pass": num_pass,
        "pass_all": pass_all,
        "accept": accept,
    }


__all__ = ["TAU_P", "TAU_C", "ALPHA_R", "TAU_ALL", "ownership_pass"]
