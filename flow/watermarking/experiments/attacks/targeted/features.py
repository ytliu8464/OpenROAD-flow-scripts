# SPDX-License-Identifier: BSD-3-Clause
"""Observable feature vectors for the targeted attacker (paper §7.2).

Placement and CTS features are now computed *inside* OpenROAD-python by
``attacks/targeted/dump_features.py`` (they need the leaked ODB to reconstruct
the public eligible object set E_s and to read per-object layout structure).
This module keeps only the **routing** feature builder, which is derivable from
the post-DRT ``route_counts`` CSV alone and therefore needs no OpenROAD context
-- so it stays import-safe for the plain-Python driver.

Every feature here is computable from the leaked post-routing layout; nothing
depends on the owner key or on the embedder's private attempt log.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import List, Tuple


def routing_features(route_counts_csv: Path) -> Tuple[List[str], List[List[float]],
                                                      List[str]]:
    """Return (header, feature_rows, net_ids) over every routed signal net.

    The per-net ``route_counts`` CSV (wrong_way, total) is the observable
    post-route statistic.  The eligible population is *all* routed nets
    (total>0), which is the correct denominator for the routing watermark --
    unlike placement/CTS, the routing attacker already saw the full set, which
    is why routing AUC was the only one previously computed over the right
    population.
    """
    header = [
        "total", "wrong_way", "wrong_way_ratio",
        "log_total", "is_short", "is_long",
    ]
    rows: List[List[float]] = []
    ids: List[str] = []
    for r in csv.DictReader(open(route_counts_csv)):
        try:
            tot = int(r["total"]); ww = int(r["wrong_way"])
        except Exception:
            continue
        if tot <= 0:
            continue
        ids.append(r["net"])
        rows.append([
            float(tot),
            float(ww),
            ww / tot,
            math.log1p(tot),
            1.0 if tot <= 4 else 0.0,
            1.0 if tot >= 32 else 0.0,
        ])
    return header, rows, ids


__all__ = ["routing_features"]
