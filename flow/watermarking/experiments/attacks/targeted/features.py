# SPDX-License-Identifier: BSD-3-Clause
"""Per-stage feature extraction for the targeted attacker (Sec. 6.3).

For each watermark-bearing object (placement tuple, CTS pair, routing net) we
extract a small, *local*, key-independent feature vector that an attacker
could compute from a leaked layout.  These features are deliberately the same
quantities the watermark embedding modifies, plus a few statistical
neighbours, so that a classifier can pick out watermarked objects without the
key:

Placement-tuple features (per tuple in wm_place_order_embed_v2.csv):
    - hpwl_delta_dbu
    - disp_max_dbu
    - row_y_dbu
    - tile_tx, tile_ty
    - kind in {0=pair, 1=triple}

CTS-pair features (per pair in wm_cts_pairs_embed.csv):
    - fanout_target_after, fanout_other_after
    - seq_fanout_target_after, seq_fanout_other_after
    - cap_target, cap_max_target
    - num_reassigned

Routing-net features (per net in route_counts.csv):
    - total segments
    - wrong-way ratio (w/m)
    - log(degree) (sinks), if available
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Tuple


def _safe(row: dict, key: str, default=0.0):
    try:
        return float(row.get(key, default) or default)
    except Exception:
        return float(default)


def placement_features(embed_csv: Path) -> Tuple[List[str], List[List[float]],
                                                  List[str]]:
    """Return (header, feature_rows, object_ids)."""
    header = ["hpwl_delta_dbu", "disp_max_dbu", "row_y_dbu",
              "tile_tx", "tile_ty", "kind_triple"]
    rows = []
    ids = []
    for r in csv.DictReader(open(embed_csv)):
        ids.append(r.get("id", r.get("tuple_id", "")))
        rows.append([
            _safe(r, "hpwl_delta_dbu"),
            _safe(r, "disp_max_dbu"),
            _safe(r, "row_y_dbu"),
            _safe(r, "tile_tx"), _safe(r, "tile_ty"),
            1.0 if r.get("kind") == "triple" else 0.0,
        ])
    return header, rows, ids


def cts_features(embed_csv: Path) -> Tuple[List[str], List[List[float]], List[str]]:
    header = ["fanout_target_after", "fanout_other_after",
              "seq_fanout_target_after", "seq_fanout_other_after",
              "cap_target", "cap_max_target", "num_reassigned"]
    rows = []
    ids = []
    for r in csv.DictReader(open(embed_csv)):
        ids.append(r.get("pair_key", r.get("target_lcb", "")))
        rows.append([
            _safe(r, "fanout_target_after"), _safe(r, "fanout_other_after"),
            _safe(r, "seq_fanout_target_after"),
            _safe(r, "seq_fanout_other_after"),
            _safe(r, "cap_target"), _safe(r, "cap_max_target"),
            _safe(r, "num_reassigned"),
        ])
    return header, rows, ids


def routing_features(route_counts_csv: Path) -> Tuple[List[str], List[List[float]],
                                                       List[str]]:
    header = ["total", "wrong_way_ratio"]
    rows = []
    ids = []
    for r in csv.DictReader(open(route_counts_csv)):
        try:
            tot = int(r["total"]); ww = int(r["wrong_way"])
        except Exception:
            continue
        if tot <= 0:
            continue
        ids.append(r["net"])
        rows.append([float(tot), ww / tot])
    return header, rows, ids
