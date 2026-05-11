# SPDX-License-Identifier: BSD-3-Clause
"""Routing-stage statistical verification (Eqs. eq:routing_p1_p0..eq:routing_pvalue).

Two pieces:

1) `count_segments(block, ...)` -- walks all routable signal nets in an OpenROAD
   dbBlock and returns, per net, the total number of routed segments and the
   number of wrong-way segments (those routed in the non-preferred direction of
   the segment's layer).  This is an in-process function that must run from
   inside an OpenROAD Python session.

2) `route_stat_from_counts(...)` -- given per-net (w(n), m(n)) and the
   WM_R set, computes p_hat_1, p_hat_0, p_hat, Z_R, p_R per the paper.

The first piece is invoked via the helper Tcl/Py script
`tools/dump_route_counts.py` (also in this package) which is launched under
OpenROAD; the second piece is pure Python and runs in the harness.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Set, Tuple


@dataclass
class RouteStat:
    p_hat_1: float          # wrong-way fraction over WM_R
    p_hat_0: float          # wrong-way fraction over E_R \ WM_R
    p_hat: float            # pooled wrong-way fraction over E_R
    M_1: int                # total segments in WM_R
    M_0: int                # total segments in E_R \ WM_R
    Z_R: float
    p_R: float
    n_selected: int         # |WM_R|
    n_unselected: int       # |E_R \ WM_R|

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _normal_sf(z: float) -> float:
    """1 - Phi(z) using math.erfc."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def route_stat_from_counts(
    counts: Dict[str, Tuple[int, int]],
    wm_set: Set[str],
) -> RouteStat:
    """counts: {net_name -> (w(n), m(n))}; wm_set: net names selected as WM_R."""
    w1 = m1 = w0 = m0 = 0
    n_sel = n_unsel = 0
    for name, (w, m) in counts.items():
        if m <= 0:
            continue
        if name in wm_set:
            w1 += w
            m1 += m
            n_sel += 1
        else:
            w0 += w
            m0 += m
            n_unsel += 1

    p_hat_1 = (w1 / m1) if m1 > 0 else 0.0
    p_hat_0 = (w0 / m0) if m0 > 0 else 0.0
    p_hat = ((w1 + w0) / (m1 + m0)) if (m1 + m0) > 0 else 0.0

    denom = p_hat * (1.0 - p_hat) * (1.0 / max(m1, 1) + 1.0 / max(m0, 1))
    if denom > 0:
        Z_R = (p_hat_0 - p_hat_1) / math.sqrt(denom)
    else:
        Z_R = 0.0
    p_R = _normal_sf(Z_R)

    return RouteStat(p_hat_1, p_hat_0, p_hat, m1, m0, Z_R, p_R, n_sel, n_unsel)


def read_counts_csv(path: Path) -> Dict[str, Tuple[int, int]]:
    """Read a CSV {net,wrong_way,total} dumped by tools/dump_route_counts.py."""
    out: Dict[str, Tuple[int, int]] = {}
    with open(path) as f:
        rd = csv.DictReader(f)
        for row in rd:
            try:
                out[row["net"]] = (int(row["wrong_way"]), int(row["total"]))
            except Exception:
                continue
    return out


def read_watermark_nets(path: Path) -> Set[str]:
    """Read flow/results/.../watermark_nets.txt (one net name per line)."""
    if not path.exists():
        return set()
    return {ln.strip() for ln in path.read_text().splitlines() if ln.strip()}


__all__ = [
    "RouteStat",
    "route_stat_from_counts",
    "read_counts_csv",
    "read_watermark_nets",
]
