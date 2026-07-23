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
        inst_size,
        is_filler_tap_endcap,
        fanout_of,
        make_tile_grid,
        tile_of,
        swap_delta_hpwl,
        triple_all_perm_hpwl_costs,
        filter_by_slack,
        MacroIndex,
        make_bucket_key,
        snap_row_y,
        sorted_row_bottoms,
        build_clock_net_ids,
        is_clock_cell,
        compute_tile_density,
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


def reconstruct_placement_pool_tight(block, *,
                                     max_dx_dbu: int,
                                     k: int = 2,
                                     include_triples: bool = True,
                                     grid_nx: int = 8,
                                     grid_ny: int = 8,
                                     eps_pair_dbu: int = 100,
                                     eps_group_dbu: int = 100,
                                     fanout_diff_max: int = 4
                                     ) -> List[Tuple[str, Tuple[object, ...]]]:
    """Return the *embedder-faithful* eligible placement pool.

    ``reconstruct_placement_pool`` returns every close co-row tuple, which is a
    strict superset of what the embedder actually treats as eligible.  The
    watermark set WM_P is drawn from the embedder's candidate cascade, which
    only admits tuples that are:

      * in the same tile / row **and the same master width** (the bucket key is
        ``((tx,ty), row_y, width, crit_bin)``: cells are compared only inside a
        bucket, so a legal X-swap never changes the row's width profile);
      * within ``max_dx_dbu`` horizontally;
      * **HPWL-neutral**: ``|swap_delta_hpwl| <= eps_pair`` for pairs and
        permutation spread ``<= eps_group`` for triples;
      * **fanout-balanced**: ``|fanout(a)-fanout(b)| <= fanout_diff_max``.

    Reconstructing the *loose* pool as the attacker's eligible set contaminates
    the negatives with un-embeddable tuples (different width, HPWL-disturbing),
    which a classifier separates trivially -- inflating AUC via an *eligibility*
    signal rather than any key-dependent watermark footprint.  This function
    reproduces the public structural gates so WM_P and the negatives live in the
    same structural class.  The criticality bin and STA-dependent slack guard
    are dropped (they need timing the keyless attacker may not have); dropping
    them only *merges* buckets, i.e. yields a conservative superset within each
    width class -- it never reintroduces the width/HPWL artifact.
    """
    if not _PLACE_OK:
        raise RuntimeError("place_ordering.watermark_common not importable")
    cells = [c for c in collect_movable_core_cells(block)
             if not is_filler_tap_endcap(c.getMaster())]
    bbox, tw, th = make_tile_grid(block, grid_nx, grid_ny)
    fan = {}
    for c in cells:
        try:
            fan[c.getId()] = fanout_of(c)
        except Exception:
            fan[c.getId()] = 0

    # Bucket by (tile, row_y, master_width) -- mirrors the embedder bucket key
    # minus the criticality bin.
    buckets: "dict[tuple, List[object]]" = {}
    for c in cells:
        try:
            x, y = inst_bottom_left(c)
            w, _h = inst_size(c)
            tx, ty = tile_of(c, bbox, tw, th, grid_nx, grid_ny)
        except Exception:
            continue
        buckets.setdefault((tx, ty, int(y), int(w)), []).append(c)

    pool: List[Tuple[str, Tuple[object, ...]]] = []
    for _bk, bcells in buckets.items():
        if len(bcells) < 2:
            continue
        bcells.sort(key=lambda c: inst_bottom_left(c)[0])
        for a, b in enumerate_close_pairs_neighbor_k(bcells, max_dx_dbu, k):
            if fanout_diff_max >= 0:
                if abs(fan.get(a.getId(), 0) - fan.get(b.getId(), 0)) > fanout_diff_max:
                    continue
            if eps_pair_dbu >= 0:
                try:
                    if abs(swap_delta_hpwl(a, b)) > eps_pair_dbu:
                        continue
                except Exception:
                    continue
            pool.append(("pair", (a, b)))
        if include_triples and len(bcells) >= 3:
            for a, b, c in enumerate_close_triples_sorted_row(bcells, max_dx_dbu):
                if eps_group_dbu >= 0:
                    try:
                        costs = triple_all_perm_hpwl_costs(a, b, c)
                        if (max(costs) - min(costs)) > eps_group_dbu:
                            continue
                    except Exception:
                        continue
                pool.append(("triple", (a, b, c)))
    return pool


def reconstruct_placement_pool_embedder(design, *,
                                        max_dx_dbu: int,
                                        k: int = 2,
                                        grid_nx: int = 6,
                                        grid_ny: int = 6,
                                        fanout_max: int = 16,
                                        fanout_diff_max: int = 4,
                                        slack_threshold_ns: float = 0.10,
                                        tile_density_max: float = 1.2,
                                        blockage_margin_sites: int = 4,
                                        sdc_path: "Optional[str]" = None,
                                        pos_cell_names: "Optional[set]" = None
                                        ) -> List[Tuple[str, Tuple[object, ...]]]:
    """Reconstruct the eligible co-row PAIR pool *exactly* as the placement
    embedder (place_ordering/watermark_embed.py) builds its candidate set, so
    the targeted classifier's negatives are precisely E_P\\WM_P.

    Mirrors watermark_embed.py:460-535 cascade:
      single-row-height -> not filler/tap/endcap -> not clock cell ->
      fanout<=fanout_max -> **STA worst-pin slack >= slack_threshold** ->
      not near a macro/blockage -> bucket by (tile,row_y,width,crit_bin) ->
      drop dense tiles (tile cell-area density > tile_density_max) ->
      close-pair enumeration within bucket (<=max_dx, neighbor-K) ->
      |fanout(a)-fanout(b)| <= fanout_diff_max.

    The STA slack gate is the decisive one for Kerckhoffs fidelity: the
    watermark only ever marks timing-safe, non-dense, fanout-bounded cells, so
    reconstructing the eligible set *without* it leaves the negatives full of
    timing-critical / dense-region cells whose intrinsic cell properties differ
    systematically -- a classifier then separates on eligibility, not on the
    key.  Requires liberty already discoverable (WM_LIB_FILES env, read inside
    ``filter_by_slack``) and the placement ``sdc_path``.

    **Adaptive slack threshold.** The embedder gated on *placement-stage* worst
    slack; we can only recompute slack on the saved layout, and routing/parasitic
    drift shifts some marked cells below a fixed 0.10 ns floor.  Excluding a
    marked cell would push it into the union-fallback as a low-slack outlier and
    *create* a side channel.  So when ``pos_cell_names`` is given we lower the
    effective threshold to ``min(default, min slack over the marked cells)`` --
    guaranteeing every marked cell stays eligible while still excluding the
    genuinely timing-critical cells the embedder would never have marked.  The
    crit-bin sub-bucketing is dropped for the same drift-robustness reason (it
    would split a marked pair across bins when the two cells' slacks diverge
    post-route).

    The HPWL-neutrality gate and the per-tile capacity cap are intentionally
    *not* applied: the former is matched by simply omitting the tuple_span
    feature, and the latter only bounds how many candidates the embedder
    *looked at*, not which objects are eligible.
    """
    if not _PLACE_OK:
        raise RuntimeError("place_ordering.watermark_common not importable")
    block = design.getBlock()
    rows_o = list(block.getRows())
    site = rows_o[0].getSite()
    sw, sh = site.getWidth(), site.getHeight()
    row_bottoms = sorted_row_bottoms(block)
    bbox, tw, th = make_tile_grid(block, grid_nx, grid_ny)
    nx, ny = max(1, grid_nx), max(1, grid_ny)
    margin_dbu = int(blockage_margin_sites * sw)
    clk_ids = build_clock_net_ids(block)

    cells_all = collect_movable_core_cells(block)
    pre = []
    for inst in cells_all:
        if inst_size(inst)[1] != sh:          # single-row-height only
            continue
        if is_filler_tap_endcap(inst.getMaster()):
            continue
        if is_clock_cell(inst, clk_ids):
            continue
        if fanout_of(inst) > fanout_max:
            continue
        pre.append(inst)

    thr_s = slack_threshold_ns * 1e-9
    # filter_by_slack records the real worst slack for *every* cell in
    # ``slack_map`` (whether or not it clears the threshold), so we can use it
    # to derive an adaptive threshold.
    _kept, slack_map, _info = filter_by_slack(
        design, pre, slack_threshold_s=thr_s, sdc_path=sdc_path)

    eff_thr = thr_s
    if pos_cell_names:
        pos_slacks = [slack_map[c.getName()] for c in pre
                      if c.getName() in pos_cell_names and c.getName() in slack_map]
        if pos_slacks:
            eff_thr = min(thr_s, min(pos_slacks))

    kept = [c for c in pre if slack_map.get(c.getName(), thr_s) >= eff_thr]

    macro_index = MacroIndex(block, bbox, nx, ny, tw, th, margin_dbu)
    buckets = {}
    for inst in kept:
        if macro_index.near_blockage(inst):
            continue
        y = inst_bottom_left(inst)[1]
        ry = snap_row_y(y, row_bottoms)
        mw = inst_size(inst)[0]
        # NB: no crit-bin in the key -- it splits a marked pair across bins
        # under post-route slack drift.  (tile, row, width) matches the
        # embedder's swappability requirement.
        tid = tile_of(inst, bbox, tw, th, nx, ny)
        buckets.setdefault((tid, ry, mw), []).append(inst)

    density = compute_tile_density(kept, bbox, nx, ny, tw, th)
    dense_tiles = {t for t, d in density.items() if d > tile_density_max}

    fan = {c.getId(): fanout_of(c) for c in kept}
    pool: List[Tuple[str, Tuple[object, ...]]] = []
    for (tid, _ry, _mw), bcells in buckets.items():
        if tid in dense_tiles:
            continue
        if len(bcells) < 2:
            continue
        bcells.sort(key=lambda c: inst_bottom_left(c)[0])
        for a, b in enumerate_close_pairs_neighbor_k(bcells, max_dx_dbu, k):
            if fanout_diff_max >= 0:
                if abs(fan.get(a.getId(), 0) - fan.get(b.getId(), 0)) > fanout_diff_max:
                    continue
            pool.append(("pair", (a, b)))
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
    "reconstruct_placement_pool_tight",
    "reconstruct_placement_pool_embedder",
    "reconstruct_cts_pool",
    "reconstruct_routing_pool",
]
