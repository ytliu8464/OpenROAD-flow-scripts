# SPDX-License-Identifier: BSD-3-Clause
"""Public-rule reconstruction of the per-stage *eligible* object sets.

The watermark embedders pick a key-dependent subset out of a key-INDEPENDENT
eligible pool.  These helpers reconstruct that pool from the routed ODB (and
the post-DRT route_counts CSV for routing) without any knowledge of the key,
matching what a paper-§7 attacker is allowed to do.

The placement and CTS reconstructions delegate to the same enumeration
helpers used by the embedders, so they yield bit-identical pools by
construction.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Iterator, List, Optional, Sequence, Tuple

# Hook the embedder modules onto sys.path so their helpers import cleanly.
_HERE = Path(__file__).resolve().parents[2]   # .../flow/watermarking
sys.path.insert(0, str(_HERE / "place_ordering"))
sys.path.insert(0, str(_HERE / "cts_v2"))

try:
    from watermark_common import (   # type: ignore
        collect_movable_core_cells,
        enumerate_close_pairs_neighbor_k,
        enumerate_close_triples_sorted_row,
        inst_bottom_left,
        is_filler_tap_endcap,
    )
    _PLACE_OK = True
except Exception:
    _PLACE_OK = False

try:
    from cts_watermark_common import (   # type: ignore
        build_proximity_pairs,
        collect_lcbs,
        site_pitch_dbu,
    )
    _CTS_OK = True
except Exception:
    _CTS_OK = False


# ---------------------------------------------------------------------------
# Placement: co-row cell tuples (pairs / triples)
# ---------------------------------------------------------------------------

def _bucket_cells_by_row(cells: Sequence[object]) -> "dict[int, List[object]]":
    """Bucket movable cells by row_y (DBU) and sort each bucket by x.

    The embedder enumerates eligible pairs / triples within the same row,
    so attacker-side enumeration must match exactly.
    """
    by_row: "dict[int, List[object]]" = {}
    for c in cells:
        try:
            x, y = inst_bottom_left(c)
        except Exception:
            continue
        by_row.setdefault(int(y), []).append(c)
    for y, lst in by_row.items():
        lst.sort(key=lambda c: inst_bottom_left(c)[0])
    return by_row


def reconstruct_placement_pool(block, *,
                               max_dx_dbu: int,
                               k: int = 2,
                               include_triples: bool = True
                               ) -> List[Tuple[str, Tuple[object, ...]]]:
    """Return the public eligible placement pool.

    Output: list of ('pair' | 'triple', (cell_a, cell_b[, cell_c])) tuples.

    Filters out filler/tap/endcap masters (these are not eligible in the
    embedder either).
    """
    if not _PLACE_OK:
        raise RuntimeError("place_ordering.watermark_common not importable")
    cells = [c for c in collect_movable_core_cells(block)
             if not is_filler_tap_endcap(c.getMaster())]
    pool: List[Tuple[str, Tuple[object, ...]]] = []
    for _row_y, row_cells in _bucket_cells_by_row(cells).items():
        for a, b in enumerate_close_pairs_neighbor_k(row_cells, max_dx_dbu, k):
            pool.append(("pair", (a, b)))
        if include_triples:
            for a, b, c in enumerate_close_triples_sorted_row(row_cells, max_dx_dbu):
                pool.append(("triple", (a, b, c)))
    return pool


# ---------------------------------------------------------------------------
# CTS: compatible LCB pairs
# ---------------------------------------------------------------------------

def reconstruct_cts_pool(block, *,
                         max_dist_dbu: float
                         ) -> List[Tuple[str, object, object]]:
    """Return the public eligible CTS pool: (pair_key, L_A, L_B) per LCB pair."""
    if not _CTS_OK:
        raise RuntimeError("cts_v2.cts_watermark_common not importable")
    lcbs = collect_lcbs(block)
    return build_proximity_pairs(lcbs, max_dist_dbu)


# ---------------------------------------------------------------------------
# Routing: routable signal nets
# ---------------------------------------------------------------------------

def reconstruct_routing_pool(route_counts_csv: Path) -> List[str]:
    """Return the list of routable signal net names (those with total>0).

    Reads the per-net (wrong_way, total) CSV dumped by tools/dump_route_counts.py.
    """
    out: List[str] = []
    if not Path(route_counts_csv).exists():
        return out
    for row in csv.DictReader(open(route_counts_csv)):
        try:
            total = int(row.get("total", 0))
        except (TypeError, ValueError):
            continue
        if total > 0:
            out.append(row.get("net", ""))
    return [n for n in out if n]


__all__ = [
    "reconstruct_placement_pool",
    "reconstruct_cts_pool",
    "reconstruct_routing_pool",
]
