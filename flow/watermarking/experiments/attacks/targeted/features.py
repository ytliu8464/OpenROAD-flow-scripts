# SPDX-License-Identifier: BSD-3-Clause
"""Public-information feature vectors per stage for the targeted attacker
(paper §7.2).

Every feature here can be computed from the leaked post-routing layout +
embed CSV; nothing depends on the owner key.  Feature sets follow the
paper text:

Placement (~9 features):
  - local placement density (cells per tile)
  - row index (row_y_dbu)
  - cell width, height
  - tuple span (max(x) - min(x) over A/B/C)
  - estimated HPWL delta (hpwl_delta_dbu)
  - fanout statistic (max fanout over tuple members)
  - timing slack (best-effort 0 default if unavailable)
  - displacement from reference (disp_max_dbu)
  - kind (pair vs triple)

CTS (~10 features):
  - LCB total fanout (post-embed)
  - sequential-sink count
  - non-sequential fanout count
  - clock-tree depth proxy (channel: pure/quasi_leaf encoded)
  - capacitance, max capacitance
  - boundary FF moves attempted (num_reassigned)
  - repair-fanout target / other
  - is_target_lcb_a (which of {L_A, L_B} carried the bit)

Routing (~9 features):
  - net degree (sinks proxy via routed segments)
  - bounding-box size (proxy: total / wrong_way ratio bucket)
  - routed wirelength (total)
  - via count (n/a in counts CSV -> 0)
  - wrong-way segment count
  - wrong-way fraction (= wrong / total)
  - layer histogram (n/a -> 0)
  - congestion / timing criticality (n/a -> 0)

Where a feature isn't directly available in the embed CSV / route_counts
CSV we record 0.0 so the RandomForestClassifier can learn its
information-content from the remaining columns rather than crashing.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Tuple


def _f(row: dict, key: str, default: float = 0.0) -> float:
    try:
        v = row.get(key, default)
        if v is None or v == "":
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def _tile_density(rows: List[dict]) -> Dict[Tuple[int, int], int]:
    counts: Dict[Tuple[int, int], int] = {}
    for r in rows:
        try:
            tx = int(_f(r, "tile_tx"))
            ty = int(_f(r, "tile_ty"))
        except Exception:
            continue
        counts[(tx, ty)] = counts.get((tx, ty), 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------

def placement_features(embed_csv: Path) -> Tuple[List[str], List[List[float]],
                                                  List[str]]:
    """Return (header, feature_rows, object_ids) for placement tuples.

    object_id matches the CSV's 'id' column when present, else
    'A_name|B_name[|C_name]'.
    """
    header = [
        "tile_density", "row_y_dbu", "cell_w_dbu", "cell_h_dbu", "tuple_span_dbu",
        "hpwl_delta_dbu", "fanout_max", "slack_ns", "disp_max_dbu", "kind_triple",
    ]
    rows: List[List[float]] = []
    ids:  List[str]         = []

    # Pre-parse CSV so we can compute tile density.
    raw = list(csv.DictReader(open(embed_csv)))
    density = _tile_density(raw)

    for r in raw:
        ids.append(r.get("id") or
                   (r.get("A_name", "") + "|" + r.get("B_name", "") +
                    (("|" + r.get("C_name", "")) if r.get("C_name") else "")))
        tx = int(_f(r, "tile_tx"))
        ty = int(_f(r, "tile_ty"))
        # tuple_span: we don't have cell xs here; use disp_max_dbu as a proxy
        # (it's the largest swap-distance the embedder considered).
        tuple_span = _f(r, "disp_max_dbu")
        rows.append([
            float(density.get((tx, ty), 0)),
            _f(r, "row_y_dbu"),
            _f(r, "cell_w_dbu", 0.0),    # not present today -> 0
            _f(r, "cell_h_dbu", 0.0),
            tuple_span,
            _f(r, "hpwl_delta_dbu"),
            _f(r, "fanout_max",   0.0),
            _f(r, "slack_ns",     0.0),
            _f(r, "disp_max_dbu"),
            1.0 if r.get("kind") == "triple" else 0.0,
        ])
    return header, rows, ids


# ---------------------------------------------------------------------------
# CTS
# ---------------------------------------------------------------------------

_CHANNEL_CODE = {"": 0.0, "pure": 1.0, "quasi_leaf": 2.0, "pure_quasi": 3.0}


def cts_features(embed_csv: Path) -> Tuple[List[str], List[List[float]], List[str]]:
    header = [
        "fanout_target_after", "fanout_other_after",
        "seq_fanout_target_after", "seq_fanout_other_after",
        "channel_code", "cap_target", "cap_max_target",
        "num_reassigned", "repair_fanout_target", "repair_fanout_other",
        "target_is_A",
    ]
    rows: List[List[float]] = []
    ids:  List[str]         = []

    for r in csv.DictReader(open(embed_csv)):
        ids.append(r.get("pair_key") or
                   (r.get("L_A", "") + "+" + r.get("L_B", "")))
        # target_is_A: 1 if target_lcb == L_A
        try:
            target_is_a = 1.0 if r.get("target_lcb") == r.get("L_A") else 0.0
        except Exception:
            target_is_a = 0.0
        rows.append([
            _f(r, "fanout_target_after"),
            _f(r, "fanout_other_after"),
            _f(r, "seq_fanout_target_after"),
            _f(r, "seq_fanout_other_after"),
            _CHANNEL_CODE.get(r.get("channel", ""), 0.0),
            _f(r, "cap_target"),
            _f(r, "cap_max_target"),
            _f(r, "num_reassigned"),
            _f(r, "repair_fanout_target"),
            _f(r, "repair_fanout_other"),
            target_is_a,
        ])
    return header, rows, ids


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def routing_features(route_counts_csv: Path) -> Tuple[List[str], List[List[float]],
                                                       List[str]]:
    header = [
        "total", "wrong_way", "wrong_way_ratio",
        "log_total", "is_short", "is_long", "via_count",
        "layer_usage_diversity", "congestion_proxy",
    ]
    rows: List[List[float]] = []
    ids:  List[str]         = []

    import math
    for r in csv.DictReader(open(route_counts_csv)):
        try:
            tot = int(r["total"]); ww = int(r["wrong_way"])
        except Exception:
            continue
        if tot <= 0:
            continue
        ids.append(r["net"])
        ratio = ww / tot
        rows.append([
            float(tot),
            float(ww),
            ratio,
            math.log1p(tot),
            1.0 if tot <= 4 else 0.0,
            1.0 if tot >= 32 else 0.0,
            0.0,            # via_count - not in counts CSV; placeholder
            0.0,            # layer_usage_diversity - placeholder
            0.0,            # congestion_proxy - placeholder
        ])
    return header, rows, ids


__all__ = ["placement_features", "cts_features", "routing_features"]
