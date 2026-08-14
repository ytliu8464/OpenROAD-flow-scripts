#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Embed pairwise / small-group relative-order placement watermarks (OpenROAD Python)."""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from openroad import Design, Tech

import watermark_common as wc


_T0 = time.time()


def _elapsed() -> str:
    return f"{time.time() - _T0:8.2f}s"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [wm_embed +{_elapsed()}] {msg}", flush=True)


def _pair_bit_from_positions(a, b) -> int:
    ax, _ = wc.inst_bottom_left(a)
    bx, _ = wc.inst_bottom_left(b)
    return 0 if ax < bx else 1


def _collect_candidates_same_row(
    buckets: Dict[wc.BucketKey, List[object]],
    bbox: Tuple[int, int, int, int],
    tw: float,
    th: float,
    nx: int,
    ny: int,
    pair_dist_dbu: int,
    eps_pair: int,
    eps_group: int,
    dense_tiles: Set[Tuple[int, int]],
    include_groups: bool,
    *,
    hpwl_cache: Optional[wc.HPWLCache],
    fanout_map: Dict[int, int],
    fanout_diff_max: int,
    neighbor_min_slack: Dict[int, float],
    slack_floor_s: float,
    pairs_per_tile: int,
    tile_oversample: int,
    pair_neighbor_k: int,
) -> Tuple[List[dict], List[dict], Dict[str, int]]:
    """Tile-local cheap-filter cascade with bounded enumeration and HPWL cache.

    Returns ``(pair_rows, triple_rows, counters)``. The cascade order is:
      bucket -> bounded-K window -> distance -> neighbor-slack -> fanout-diff
      -> dense-tile gate (precomputed) -> swap-HPWL (cached, last & most expensive).
    Per-tile early stop kicks in once ``pairs_per_tile * tile_oversample`` pair
    candidates have been accepted in that tile.
    """
    pair_rows: List[dict] = []
    triple_rows: List[dict] = []
    counters = {
        "raw_enumerated": 0,
        "dist_rejects": 0,
        "slack_rejects": 0,
        "fanout_rejects": 0,
        "hpwl_rejects": 0,
        "accepted": 0,
        "tiles_capped": 0,
        "raw_triples": 0,
        "triple_hpwl_rejects": 0,
        "triple_accepted": 0,
    }

    cap_per_tile = max(1, pairs_per_tile * max(1, tile_oversample))

    # Group buckets by tile so we can enforce a per-tile budget.
    by_tile: Dict[Tuple[int, int], List[wc.BucketKey]] = {}
    for k in buckets.keys():
        by_tile.setdefault((k[0][0], k[0][1]), []).append(k)

    pos = hpwl_cache.pos if hpwl_cache is not None else None

    def _x_of(inst) -> int:
        if pos is not None:
            try:
                return pos[inst.getId()][0]
            except Exception:
                pass
        return wc.inst_bottom_left(inst)[0]

    def _swap_dhp(a, b) -> int:
        if hpwl_cache is not None:
            return hpwl_cache.swap_delta_hpwl(a, b)
        return wc.swap_delta_hpwl(a, b)

    capped_tiles: Set[Tuple[int, int]] = set()
    for tid, bucket_keys in by_tile.items():
        if tid in dense_tiles:
            continue
        accepted_pairs_here = 0
        for bk in bucket_keys:
            if accepted_pairs_here >= cap_per_tile:
                if tid not in capped_tiles:
                    counters["tiles_capped"] += 1
                    capped_tiles.add(tid)
                break
            cells = buckets[bk]
            if len(cells) < 2:
                continue
            # Sort by cached x.
            sorted_cells = sorted(cells, key=_x_of)

            for a, b in wc.enumerate_close_pairs_neighbor_k(
                sorted_cells, pair_dist_dbu, pair_neighbor_k, pos=pos,
            ):
                counters["raw_enumerated"] += 1
                ax = _x_of(a)
                bx = _x_of(b)
                if bx - ax > pair_dist_dbu:
                    counters["dist_rejects"] += 1
                    continue
                # Neighbor-slack guard.
                if neighbor_min_slack:
                    try:
                        aid = a.getId()
                        bid = b.getId()
                    except Exception:
                        aid = bid = None
                    ms_a = neighbor_min_slack.get(aid, slack_floor_s)
                    ms_b = neighbor_min_slack.get(bid, slack_floor_s)
                    if ms_a < slack_floor_s or ms_b < slack_floor_s:
                        counters["slack_rejects"] += 1
                        continue
                # Fanout-diff cheap filter.
                if fanout_diff_max >= 0 and fanout_map:
                    try:
                        fa = fanout_map.get(a.getId(), 0)
                        fb = fanout_map.get(b.getId(), 0)
                    except Exception:
                        fa = fb = 0
                    if abs(fa - fb) > fanout_diff_max:
                        counters["fanout_rejects"] += 1
                        continue
                # HPWL gate (most expensive).
                dhp = _swap_dhp(a, b)
                if abs(dhp) > eps_pair:
                    counters["hpwl_rejects"] += 1
                    continue
                pair_rows.append({
                    "kind": "pair",
                    "tile_id": tid,
                    "a": a,
                    "b": b,
                    "dhp": dhp,
                    "eps_pair": eps_pair,
                })
                accepted_pairs_here += 1
                counters["accepted"] += 1
                if accepted_pairs_here >= cap_per_tile:
                    if tid not in capped_tiles:
                        counters["tiles_capped"] += 1
                        capped_tiles.add(tid)
                    break

            if include_groups and len(sorted_cells) >= 3:
                # Triples are expensive to enumerate; stop early if we already
                # have enough pair surplus.
                if accepted_pairs_here >= cap_per_tile:
                    continue
                for a, b, c in wc.enumerate_close_triples_sorted_row(
                    sorted_cells, pair_dist_dbu,
                ):
                    counters["raw_triples"] += 1
                    costs = wc.triple_all_perm_hpwl_costs(a, b, c)
                    spread = max(costs) - min(costs)
                    if spread > eps_group:
                        counters["triple_hpwl_rejects"] += 1
                        continue
                    triple_rows.append({
                        "kind": "triple",
                        "tile_id": tid,
                        "a": a,
                        "b": b,
                        "c": c,
                        "costs": costs,
                        "spread": spread,
                    })
                    counters["triple_accepted"] += 1

    return pair_rows, triple_rows, counters


def _pair_key_fn(seed: bytes, tid: Tuple[int, int], r: dict) -> bytes:
    return wc.pair_sort_key(seed, tid, r["a"].getName(), r["b"].getName())


def _group_key_fn(seed: bytes, tid: Tuple[int, int], r: dict) -> bytes:
    return wc.group_sort_key(
        seed, tid, (r["a"].getName(), r["b"].getName(), r["c"].getName())
    )


def _pair_candidate_key(r: dict) -> str:
    return wc.pair_id_str(r["a"].getName(), r["b"].getName())


def _select_non_overlap(
    tile_id: Tuple[int, int],
    rows: Sequence[dict],
    quota: int,
    used: Set[str],
    key_fn: Callable[[Tuple[int, int], dict], bytes],
    spread_max: Optional[int] = None,
    tile_touch_used: Optional[Dict[Tuple[int, int], int]] = None,
    tile_touch_cap: Optional[int] = None,
) -> List[dict]:
    """Greedy selection by PRF order.

    ``tile_touch_used``/``tile_touch_cap`` (when provided) cap the number of
    cells in this tile that can be perturbed (sum across pairs and triples).
    """
    pool = [r for r in rows if r["tile_id"] == tile_id]
    if spread_max is not None:
        pool = [r for r in pool if r.get("spread", 0) <= spread_max]
    pool.sort(key=lambda r: key_fn(tile_id, r), reverse=True)
    picked: List[dict] = []
    cur_touch = (tile_touch_used or {}).get(tile_id, 0)
    for r in pool:
        if r["kind"] == "pair":
            na, nb = r["a"].getName(), r["b"].getName()
            if na in used or nb in used:
                continue
            cells_added = 2
            if (
                tile_touch_cap is not None
                and tile_touch_cap >= 0
                and cur_touch + cells_added > tile_touch_cap
            ):
                continue
            used.add(na)
            used.add(nb)
            cur_touch += cells_added
            picked.append(r)
        else:
            na = r["a"].getName()
            nb = r["b"].getName()
            nc = r["c"].getName()
            if na in used or nb in used or nc in used:
                continue
            cells_added = 3
            if (
                tile_touch_cap is not None
                and tile_touch_cap >= 0
                and cur_touch + cells_added > tile_touch_cap
            ):
                continue
            used.add(na)
            used.add(nb)
            used.add(nc)
            cur_touch += cells_added
            picked.append(r)
        if len(picked) >= quota:
            break
    if tile_touch_used is not None:
        tile_touch_used[tile_id] = cur_touch
    return picked


def _triple_apply(
    seed: bytes,
    a, b, c,
    tid: Tuple[int, int],
    balance: wc.TileBalanceTracker,
    eps_group: int,
) -> Tuple[bool, int, Tuple[str, str, str], str, int]:
    """Return (applied, perm_idx, expected_order_tuple, skip_reason, hpwl_delta_dbu)."""
    insts_sorted = sorted((a, b, c), key=lambda i: i.getName())
    names_t = tuple(i.getName() for i in insts_sorted)
    tgt_perm = wc.target_perm_index(seed, tid, names_t)
    expected = wc.permuted_order_names(names_t, tgt_perm)
    obs = wc.order_names_left_to_right((a, b, c))
    if obs == expected:
        return False, tgt_perm, expected, "already_satisfied", 0

    dhp = wc.triple_perm_hpwl_delta(a, b, c, tgt_perm)
    if abs(dhp) > eps_group:
        return False, tgt_perm, expected, "hpwl_perm", dhp

    xs = sorted([wc.inst_bottom_left(a)[0], wc.inst_bottom_left(b)[0], wc.inst_bottom_left(c)[0]])
    perm = wc.PERMUTATIONS3[tgt_perm % 6]
    moves: List[Tuple[int, int]] = []
    for slot_k in range(3):
        inst = insts_sorted[perm[slot_k]]
        ox, _oy = wc.inst_bottom_left(inst)
        nx = xs[slot_k]
        moves.append((ox, nx))

    if not balance.try_moves(tid, moves):
        return False, tgt_perm, expected, "balance_cap", dhp

    for slot_k in range(3):
        inst = insts_sorted[perm[slot_k]]
        _ox, oy = wc.inst_bottom_left(inst)
        inst.setLocation(xs[slot_k], oy)

    return True, tgt_perm, expected, "", dhp


def main() -> int:
    p = argparse.ArgumentParser(description="Embed ordering-based placement watermark")
    p.add_argument("--input", default=os.environ.get("WM_INPUT"))
    p.add_argument("--output-odb", default=os.environ.get("WM_OUTPUT_ODB"))
    p.add_argument("--output-def", default=os.environ.get("WM_OUTPUT_DEF"))
    p.add_argument("--output-cell-list", default=os.environ.get("WM_OUTPUT_CELL_LIST"))
    p.add_argument("--seed-hex", default=os.environ.get("WM_SEED_HEX"))
    p.add_argument("--grid-nx", type=int, default=int(os.environ.get("WM_GRID_NX", "8")))
    p.add_argument("--grid-ny", type=int, default=int(os.environ.get("WM_GRID_NY", "8")))
    p.add_argument(
        "--pair-dist-um", type=float,
        default=float(os.environ.get("WM_PAIR_DIST_UM", "1")),
    )
    p.add_argument(
        "--pairs-per-tile", type=int,
        default=int(os.environ.get("WM_PAIRS_PER_TILE", "4")),
    )
    p.add_argument(
        "--groups-per-tile", type=int,
        default=int(os.environ.get("WM_GROUPS_PER_TILE", "2")),
    )
    p.add_argument(
        "--use-groups", type=int,
        default=int(os.environ.get("WM_USE_GROUPS", "0")),
    )
    p.add_argument(
        "--hpwl-eps-pair-dbu", type=int,
        default=int(os.environ.get("WM_HPWL_EPS_PAIR_DBU", "100")),
    )
    p.add_argument(
        "--hpwl-eps-group-dbu", type=int,
        default=int(os.environ.get("WM_HPWL_EPS_GROUP_DBU", "100")),
    )
    p.add_argument(
        "--fanout-max", type=int,
        default=int(os.environ.get("WM_FANOUT_MAX", "16")),
    )
    p.add_argument(
        "--slack-threshold-ns", type=float,
        default=float(os.environ.get("WM_SLACK_THRESHOLD_NS", "0.20")),
    )
    p.add_argument(
        "--crit-bin-ns", type=float,
        default=float(os.environ.get("WM_CRIT_BIN_NS", "0.05")),
    )
    p.add_argument(
        "--crit-bin-relaxed-ns", type=float,
        default=float(os.environ.get("WM_CRIT_BIN_RELAXED_NS", "0.20")),
    )
    p.add_argument(
        "--tile-density-max", type=float,
        default=float(os.environ.get("WM_TILE_DENSITY_MAX", "1.2")),
    )
    p.add_argument(
        "--tile-disp-cap-um", type=float,
        default=float(os.environ.get("WM_TILE_DISP_CAP_UM", "200")),
    )
    p.add_argument(
        "--blockage-margin-sites", type=int,
        default=int(os.environ.get("WM_BLOCKAGE_MARGIN_SITES", "4")),
    )
    p.add_argument(
        "--pair-neighbor-k", type=int,
        default=int(os.environ.get("WM_PAIR_NEIGHBOR_K", "2")),
    )
    p.add_argument(
        "--tile-oversample", type=int,
        default=int(os.environ.get("WM_TILE_OVERSAMPLE", "4")),
    )
    p.add_argument(
        "--fanout-diff-max", type=int,
        default=int(os.environ.get("WM_FANOUT_DIFF_MAX", "4")),
    )
    p.add_argument(
        "--hpwl-cache", type=int,
        default=int(os.environ.get("WM_HPWL_CACHE", "1")),
    )
    p.add_argument(
        "--hpwl-net-fanout-max", type=int,
        default=int(os.environ.get("WM_HPWL_NET_FANOUT_MAX", "64")),
    )
    p.add_argument(
        "--neighbor-slack-margin-ns", type=float,
        default=float(os.environ.get("WM_NEIGHBOR_SLACK_MARGIN_NS", "0.10")),
    )
    p.add_argument(
        "--tile-touch-frac-max", type=float,
        default=float(os.environ.get("WM_TILE_TOUCH_FRAC_MAX", "0.05")),
    )
    p.add_argument(
        "--tile-touch-floor-pairs", type=int,
        default=int(os.environ.get("WM_TILE_TOUCH_FLOOR_PAIRS", "4")),
    )
    p.add_argument(
        "--post-guard", type=int,
        default=int(os.environ.get("WM_POST_GUARD", "1")),
    )
    p.add_argument(
        "--post-guard-final-check", type=int,
        default=int(os.environ.get("WM_POST_GUARD_FINAL_CHECK", "1")),
    )
    p.add_argument(
        "--guard-degrade-ns", type=float,
        default=float(os.environ.get("WM_GUARD_DEGRADE_NS", "0.02")),
    )
    p.add_argument(
        "--min-pairs-total", type=int,
        default=int(os.environ.get("WM_MIN_PAIRS_TOTAL", "64")),
    )
    p.add_argument(
        "--pair-neighbor-k-relaxed", type=int,
        default=int(os.environ.get("WM_PAIR_NEIGHBOR_K_RELAXED", "8")),
    )
    p.add_argument(
        "--hpwl-eps-pair-relaxed-dbu", type=int,
        default=int(os.environ.get("WM_HPWL_EPS_PAIR_RELAXED_DBU", "200")),
    )
    p.add_argument("--sdc", default=os.environ.get("WM_SDC", ""))
    p.add_argument(
        "--max-disp-x-um", type=float,
        default=float(os.environ.get("WM_MAX_DISP_X", "5")),
        help="Incremental DPL max displacement in x (microns)",
    )
    p.add_argument(
        "--max-disp-y-um", type=float,
        default=float(os.environ.get("WM_MAX_DISP_Y", "5")),
        help="Incremental DPL max displacement in y (microns)",
    )
    args = p.parse_args(wc.argv_after_openroad_driver())

    if not args.input or not args.output_odb:
        p.error("--input and --output-odb required")
    if not args.seed_hex:
        p.error("--seed-hex required")

    _log("start")
    _log(f"input={args.input}")
    _log(f"output_odb={args.output_odb}")
    _log(
        "params "
        f"grid={args.grid_nx}x{args.grid_ny} pair_dist={args.pair_dist_um}um "
        f"pairs_per_tile={args.pairs_per_tile} groups_per_tile={args.groups_per_tile} "
        f"use_groups={args.use_groups} slack_threshold={args.slack_threshold_ns}ns"
    )

    seed = wc.load_seed_hex(args.seed_hex)
    tech = Tech()
    design = Design(tech)
    t_phase = time.time()
    _log("reading input ODB")
    design.readDb(args.input)
    block = design.getBlock()
    if block is None:
        print("No block", file=sys.stderr)
        return 1
    _log(f"read ODB done in {time.time() - t_phase:.2f}s")

    rows_o = list(block.getRows())
    site = rows_o[0].getSite()
    sw, sh = site.getWidth(), site.getHeight()
    row_bottoms = wc.sorted_row_bottoms(block)
    try:
        dbu_per_um = float(design.getDbUnitsPerMicron())
    except Exception:
        dbu_per_um = float(tech.getDbUnitsPerMicron()) if hasattr(tech, "getDbUnitsPerMicron") else 1000.0

    pair_dist_dbu = int(round(args.pair_dist_um * dbu_per_um))
    margin_dbu = int(args.blockage_margin_sites * sw)

    bbox, tw, th = wc.make_tile_grid(block, args.grid_nx, args.grid_ny)
    nx, ny = max(1, args.grid_nx), max(1, args.grid_ny)

    clk_ids = wc.build_clock_net_ids(block)

    t_phase = time.time()
    _log("collecting and pre-filtering movable core cells")
    cells_all = wc.collect_movable_core_cells(block)
    cells_h = [c for c in cells_all if wc.inst_size(c)[1] == sh]
    pre: List[object] = []
    for inst in cells_h:
        m = inst.getMaster()
        if wc.is_filler_tap_endcap(m):
            continue
        if wc.is_clock_cell(inst, clk_ids):
            continue
        if wc.fanout_of(inst) > args.fanout_max:
            continue
        pre.append(inst)
    _log(
        f"pre-filter done in {time.time() - t_phase:.2f}s: "
        f"movable={len(cells_all)} single_row={len(cells_h)} pre_sta={len(pre)}"
    )

    t_phase = time.time()
    _log("starting STA slack filter (read_liberty/read_sdc/estimate_parasitics/pin slack)")
    kept, slack_map, sta_info = wc.filter_by_slack(
        design, pre,
        slack_threshold_s=args.slack_threshold_ns * 1e-9,
        sdc_path=(args.sdc or None),
    )
    _log(
        f"STA slack filter done in {time.time() - t_phase:.2f}s: "
        f"mode={sta_info.get('mode')} kept={len(kept)} "
        f"dropped={sta_info.get('dropped', '?')}"
    )

    t_phase = time.time()
    _log("building macro/obstruction index and buckets")
    macro_index = wc.MacroIndex(block, bbox, nx, ny, tw, th, margin_dbu)

    buckets: Dict[wc.BucketKey, List[object]] = {}
    safe_kept: List[object] = []
    for inst in kept:
        if macro_index.near_blockage(inst):
            continue
        safe_kept.append(inst)
        y = wc.inst_bottom_left(inst)[1]
        ry = wc.snap_row_y(y, row_bottoms)
        mw = wc.inst_size(inst)[0]
        slack = slack_map.get(inst.getName(), args.slack_threshold_ns * 1e-9)
        tid = wc.tile_of(inst, bbox, tw, th, nx, ny)
        key = wc.make_bucket_key(tid, ry, mw, slack, args.crit_bin_ns)
        buckets.setdefault(key, []).append(inst)

    density = wc.compute_tile_density(kept, bbox, nx, ny, tw, th)
    dense_tiles = {t for t, d in density.items() if d > args.tile_density_max}
    # Per-tile total movable cell count for the touch-fraction cap. Using the
    # total movable population (not just the STA-safe subset) ensures the cap
    # represents perturbation as a fraction of the actual placement.
    tile_total_count: Dict[Tuple[int, int], int] = {}
    for inst in cells_all:
        tid = wc.tile_of(inst, bbox, tw, th, nx, ny)
        tile_total_count[tid] = tile_total_count.get(tid, 0) + 1
    _log(
        f"bucket build done in {time.time() - t_phase:.2f}s: "
        f"buckets={len(buckets)} dense_tiles={len(dense_tiles)} "
        f"safe_kept={len(safe_kept)}"
    )

    # ---- HPWL cache + per-instance fanout / neighbor-slack maps ----
    hpwl_cache: Optional[wc.HPWLCache] = None
    fanout_map: Dict[int, int] = {}
    neighbor_min_slack: Dict[int, float] = {}
    id_to_name: Dict[int, str] = {}
    name_to_inst: Dict[str, object] = {}
    for inst in safe_kept:
        try:
            iid = inst.getId()
            nm = inst.getName()
        except Exception:
            continue
        id_to_name[iid] = nm
        name_to_inst[nm] = inst
        try:
            fanout_map[iid] = wc.fanout_of(inst)
        except Exception:
            fanout_map[iid] = 0

    if args.hpwl_cache:
        t_phase = time.time()
        _log(
            f"building HPWL cache (net_fanout_max={args.hpwl_net_fanout_max})"
        )
        hpwl_cache = wc.HPWLCache(
            safe_kept, net_fanout_max=args.hpwl_net_fanout_max,
        )
        _log(
            f"HPWL cache done in {time.time() - t_phase:.2f}s: "
            f"insts={len(hpwl_cache.pos)} nets={len(hpwl_cache.net_pins)} "
            f"skipped_high_fanout_nets={len(hpwl_cache.skipped_nets)}"
        )

    slack_floor_s = args.slack_threshold_ns * 1e-9
    if hpwl_cache is not None and slack_map:
        t_phase = time.time()
        margin_s = args.neighbor_slack_margin_ns * 1e-9
        floor_with_margin = slack_floor_s + margin_s
        neighbor_min_slack = hpwl_cache.neighbor_min_slack(
            slack_map, id_to_name, default=floor_with_margin,
        )
        n_below = sum(1 for v in neighbor_min_slack.values() if v < floor_with_margin)
        _log(
            f"neighbor-slack map built in {time.time() - t_phase:.2f}s: "
            f"insts={len(neighbor_min_slack)} below_margin={n_below} "
            f"floor_with_margin={floor_with_margin*1e9:.3f}ns"
        )

    t_phase = time.time()
    _log(
        "enumerating local pair/triple candidates "
        f"(neighbor_k={args.pair_neighbor_k} oversample={args.tile_oversample})"
    )
    pair_cands, triple_cands, enum_counters = _collect_candidates_same_row(
        buckets, bbox, tw, th, nx, ny,
        pair_dist_dbu, args.hpwl_eps_pair_dbu, args.hpwl_eps_group_dbu,
        dense_tiles,
        include_groups=bool(args.use_groups),
        hpwl_cache=hpwl_cache,
        fanout_map=fanout_map,
        fanout_diff_max=args.fanout_diff_max,
        neighbor_min_slack=neighbor_min_slack,
        slack_floor_s=slack_floor_s + args.neighbor_slack_margin_ns * 1e-9,
        pairs_per_tile=args.pairs_per_tile,
        tile_oversample=args.tile_oversample,
        pair_neighbor_k=args.pair_neighbor_k,
    )
    _log(
        f"candidate enumeration done in {time.time() - t_phase:.2f}s: "
        f"pair_candidates={len(pair_cands)} triple_candidates={len(triple_cands)}"
    )
    _log(
        "  cascade counters: "
        f"raw_pairs={enum_counters['raw_enumerated']} "
        f"dist_rej={enum_counters['dist_rejects']} "
        f"slack_rej={enum_counters['slack_rejects']} "
        f"fanout_rej={enum_counters['fanout_rejects']} "
        f"hpwl_rej={enum_counters['hpwl_rejects']} "
        f"accepted={enum_counters['accepted']} "
        f"tiles_capped={enum_counters['tiles_capped']}"
    )
    if args.use_groups:
        _log(
            "  triples: "
            f"raw={enum_counters['raw_triples']} "
            f"hpwl_rej={enum_counters['triple_hpwl_rejects']} "
            f"accepted={enum_counters['triple_accepted']}"
        )

    pk = lambda tid, r: _pair_key_fn(seed, tid, r)
    gk = lambda tid, r: _group_key_fn(seed, tid, r)

    all_tiles_set = {(tx, ty) for tx in range(nx) for ty in range(ny)}

    def _select_current_candidates() -> Tuple[List[dict], List[dict]]:
        used_names: Set[str] = set()
        tile_touch_used: Dict[Tuple[int, int], int] = {}
        out_pairs: List[dict] = []
        out_groups: List[dict] = []
        floor_cells = max(2, 2 * max(0, args.tile_touch_floor_pairs))
        for tid in sorted(all_tiles_set):
            total_in_tile = tile_total_count.get(tid, 0)
            touch_cap: Optional[int] = None
            if args.tile_touch_frac_max > 0 and total_in_tile > 0:
                touch_cap = max(
                    floor_cells,
                    int(args.tile_touch_frac_max * total_in_tile),
                )
            out_pairs.extend(
                _select_non_overlap(
                    tid, pair_cands, args.pairs_per_tile, used_names, pk,
                    tile_touch_used=tile_touch_used,
                    tile_touch_cap=touch_cap,
                )
            )
            if args.use_groups:
                out_groups.extend(
                    _select_non_overlap(
                        tid,
                        triple_cands,
                        args.groups_per_tile,
                        used_names,
                        gk,
                        spread_max=args.hpwl_eps_group_dbu,
                        tile_touch_used=tile_touch_used,
                        tile_touch_cap=touch_cap,
                    )
                )
        return out_pairs, out_groups

    t_phase = time.time()
    _log(
        "selecting non-overlapping keyed constraints "
        f"(tile_touch_frac_max={args.tile_touch_frac_max})"
    )
    selected_pairs, selected_groups = _select_current_candidates()

    # Capacity fallback: the strict pass is deliberately conservative. If it
    # yields too few bits, run a second pass with broader local neighborhoods,
    # wider criticality bins, and a modestly relaxed HPWL threshold, then
    # reselect from the merged candidate pool.
    if args.min_pairs_total > 0 and len(selected_pairs) < args.min_pairs_total:
        _log(
            "capacity fallback: selected_pairs below target "
            f"({len(selected_pairs)} < {args.min_pairs_total}); "
            f"relaxing neighbor_k={args.pair_neighbor_k_relaxed}, "
            f"crit_bin={args.crit_bin_relaxed_ns}ns, "
            f"hpwl_eps={args.hpwl_eps_pair_relaxed_dbu}"
        )
        relaxed_buckets: Dict[wc.BucketKey, List[object]] = {}
        for inst in safe_kept:
            y = wc.inst_bottom_left(inst)[1]
            ry = wc.snap_row_y(y, row_bottoms)
            mw = wc.inst_size(inst)[0]
            slack = slack_map.get(inst.getName(), args.slack_threshold_ns * 1e-9)
            tid = wc.tile_of(inst, bbox, tw, th, nx, ny)
            key = wc.make_bucket_key(tid, ry, mw, slack, args.crit_bin_relaxed_ns)
            relaxed_buckets.setdefault(key, []).append(inst)

        rt_phase = time.time()
        relaxed_pairs, relaxed_triples, relaxed_counters = _collect_candidates_same_row(
            relaxed_buckets, bbox, tw, th, nx, ny,
            pair_dist_dbu, args.hpwl_eps_pair_relaxed_dbu, args.hpwl_eps_group_dbu,
            dense_tiles,
            include_groups=bool(args.use_groups),
            hpwl_cache=hpwl_cache,
            fanout_map=fanout_map,
            fanout_diff_max=args.fanout_diff_max,
            neighbor_min_slack=neighbor_min_slack,
            slack_floor_s=slack_floor_s + args.neighbor_slack_margin_ns * 1e-9,
            pairs_per_tile=args.pairs_per_tile,
            tile_oversample=args.tile_oversample,
            pair_neighbor_k=args.pair_neighbor_k_relaxed,
        )
        seen_pairs = {_pair_candidate_key(r) for r in pair_cands}
        added_pairs = 0
        for r in relaxed_pairs:
            k = _pair_candidate_key(r)
            if k in seen_pairs:
                continue
            seen_pairs.add(k)
            pair_cands.append(r)
            added_pairs += 1
        if args.use_groups:
            triple_cands.extend(relaxed_triples)
        selected_pairs, selected_groups = _select_current_candidates()
        _log(
            f"capacity fallback done in {time.time() - rt_phase:.2f}s: "
            f"relaxed_pair_candidates={len(relaxed_pairs)} "
            f"added_pairs={added_pairs} selected_pairs={len(selected_pairs)}"
        )
        _log(
            "  relaxed counters: "
            f"raw_pairs={relaxed_counters['raw_enumerated']} "
            f"slack_rej={relaxed_counters['slack_rejects']} "
            f"fanout_rej={relaxed_counters['fanout_rejects']} "
            f"hpwl_rej={relaxed_counters['hpwl_rejects']} "
            f"accepted={relaxed_counters['accepted']} "
            f"tiles_capped={relaxed_counters['tiles_capped']}"
        )

    _log(
        f"selection done in {time.time() - t_phase:.2f}s: "
        f"selected_pairs={len(selected_pairs)} selected_groups={len(selected_groups)}"
    )

    tile_disp_cap_dbu = int(args.tile_disp_cap_um * dbu_per_um)
    balance = wc.TileBalanceTracker(tile_disp_cap_dbu)

    csv_rows: List[List[object]] = []
    header = [
        "kind", "id", "tile_tx", "tile_ty", "row_y_dbu",
        "A_name", "B_name", "C_name",
        "target_bit", "target_perm",
        "orig_order", "wm_order",
        "hpwl_delta_dbu", "disp_max_dbu",
        "satisfied", "skipped_reason",
    ]

    pair_sat = 0
    pair_changed = 0
    applied_pairs: List[dict] = []  # for post-guard revert
    t_phase = time.time()
    _log("applying pair swaps")
    for r in selected_pairs:
        a, b = r["a"], r["b"]
        tid = r["tile_id"]
        tgt = wc.target_bit_for_pair(seed, tid, a.getName(), b.getName())
        cur = _pair_bit_from_positions(a, b)
        ax, ay = wc.inst_bottom_left(a)
        bx, by = wc.inst_bottom_left(b)
        row_y = wc.snap_row_y(ay, row_bottoms)
        orig_order = "|".join(wc.order_names_left_to_right((a, b)))

        if cur == tgt:
            pair_sat += 1
            csv_rows.append([
                "pair", wc.pair_id_str(a.getName(), b.getName()),
                tid[0], tid[1], row_y,
                a.getName(), b.getName(), "",
                tgt, "", orig_order, orig_order,
                0, 0, True, "already_satisfied",
            ])
            continue

        if abs(r["dhp"]) > int(r.get("eps_pair", args.hpwl_eps_pair_dbu)):
            csv_rows.append([
                "pair", wc.pair_id_str(a.getName(), b.getName()),
                tid[0], tid[1], row_y,
                a.getName(), b.getName(), "",
                tgt, "", orig_order, "",
                r["dhp"], abs(ax - bx), False, "hpwl_precheck",
            ])
            continue

        if not balance.try_moves(tid, [(ax, bx), (bx, ax)]):
            csv_rows.append([
                "pair", wc.pair_id_str(a.getName(), b.getName()),
                tid[0], tid[1], row_y,
                a.getName(), b.getName(), "",
                tgt, "", orig_order, "",
                r["dhp"], abs(ax - bx), False, "balance_cap",
            ])
            continue

        a.setLocation(bx, ay)
        b.setLocation(ax, by)
        pair_changed += 1
        if hpwl_cache is not None:
            try:
                hpwl_cache.update_pos_after_swap(a, b)
            except Exception:
                pass
        wm_order = "|".join(wc.order_names_left_to_right((a, b)))
        sat = _pair_bit_from_positions(a, b) == tgt
        if sat:
            pair_sat += 1
        csv_rows.append([
            "pair", wc.pair_id_str(a.getName(), b.getName()),
            tid[0], tid[1], row_y,
            a.getName(), b.getName(), "",
            tgt, "", orig_order, wm_order,
            r["dhp"], abs(ax - bx), sat, "" if sat else "wrong_bit_after_swap",
        ])
        applied_pairs.append({
            "a": a, "b": b,
            "orig_ax": ax, "orig_ay": ay,
            "orig_bx": bx, "orig_by": by,
            "csv_idx": len(csv_rows) - 1,
            "tgt": tgt, "orig_order": orig_order,
        })

    group_sat = 0
    group_changed = 0
    applied_triples: List[dict] = []
    _log(f"pair apply done in {time.time() - t_phase:.2f}s: changed={pair_changed}")
    t_phase = time.time()
    _log("applying triple permutations")
    for r in selected_groups:
        a, b, c = r["a"], r["b"], r["c"]
        tid = r["tile_id"]
        ay = wc.inst_bottom_left(a)[1]
        row_y = wc.snap_row_y(ay, row_bottoms)
        names = sorted([a.getName(), b.getName(), c.getName()])
        gid = wc.group_id_str(names)
        insts_sorted = sorted((a, b, c), key=lambda i: i.getName())
        names_t = tuple(i.getName() for i in insts_sorted)
        tgt_perm = wc.target_perm_index(seed, tid, names_t)
        orig_order = "|".join(wc.order_names_left_to_right((a, b, c)))
        orig_pos = {
            a.getName(): wc.inst_bottom_left(a),
            b.getName(): wc.inst_bottom_left(b),
            c.getName(): wc.inst_bottom_left(c),
        }

        applied, _perm_idx, exp_tuple, reason, dhp_est = _triple_apply(
            seed, a, b, c, tid, balance, args.hpwl_eps_group_dbu,
        )

        if not applied and reason == "already_satisfied":
            group_sat += 1
            csv_rows.append([
                "triple", gid, tid[0], tid[1], row_y,
                a.getName(), b.getName(), c.getName(),
                "", tgt_perm, orig_order, orig_order,
                0, 0, True, "already_satisfied",
            ])
            continue
        if not applied:
            csv_rows.append([
                "triple", gid, tid[0], tid[1], row_y,
                a.getName(), b.getName(), c.getName(),
                "", tgt_perm, orig_order, "",
                dhp_est, 0, False, reason,
            ])
            continue

        wm_t = wc.order_names_left_to_right((a, b, c))
        group_changed += 1
        wm_order = "|".join(wm_t)
        sat = wm_t == exp_tuple
        if sat:
            group_sat += 1
        csv_rows.append([
            "triple", gid, tid[0], tid[1], row_y,
            a.getName(), b.getName(), c.getName(),
            "", tgt_perm, orig_order, wm_order,
            dhp_est, 0, sat, "" if sat else "order_mismatch_after_apply",
        ])
        applied_triples.append({
            "a": a, "b": b, "c": c,
            "orig_pos": orig_pos,
            "csv_idx": len(csv_rows) - 1,
            "tgt_perm": tgt_perm,
            "orig_order": orig_order,
        })

    _log(f"triple apply done in {time.time() - t_phase:.2f}s: changed={group_changed}")

    # ---------- Post-embed STA guard: revert worst-degrading swaps ----------
    reverted_pairs = 0
    reverted_triples = 0
    if args.post_guard and (applied_pairs or applied_triples):
        t_phase = time.time()
        _log(
            "post-guard: running single STA pass over swapped cells "
            f"(degrade_threshold={args.guard_degrade_ns}ns)"
        )
        guard_insts: Dict[str, object] = {}
        for rec in applied_pairs:
            guard_insts[rec["a"].getName()] = rec["a"]
            guard_insts[rec["b"].getName()] = rec["b"]
        for rec in applied_triples:
            for inst in (rec["a"], rec["b"], rec["c"]):
                guard_insts[inst.getName()] = inst
        post_slacks = wc.compute_worst_slacks(design, list(guard_insts.values()))
        _log(
            f"post-guard: STA done in {time.time() - t_phase:.2f}s "
            f"insts_scored={len(post_slacks)}"
        )

        degrade_s = args.guard_degrade_ns * 1e-9
        bad_floor = slack_floor_s - degrade_s

        def _is_bad(name: str) -> bool:
            pre = slack_map.get(name, slack_floor_s)
            post = post_slacks.get(name, pre)
            if post < bad_floor:
                return True
            if (post - pre) < -degrade_s:
                return True
            return False

        # Pairs
        for rec in applied_pairs:
            a = rec["a"]
            b = rec["b"]
            if _is_bad(a.getName()) or _is_bad(b.getName()):
                a.setLocation(rec["orig_ax"], rec["orig_ay"])
                b.setLocation(rec["orig_bx"], rec["orig_by"])
                if hpwl_cache is not None:
                    try:
                        hpwl_cache.update_pos_after_swap(a, b)
                    except Exception:
                        pass
                row = csv_rows[rec["csv_idx"]]
                row[-4] = 0  # zero out hpwl_delta_dbu after revert
                row[-3] = 0  # disp_max_dbu
                row[-2] = False
                row[-1] = "reverted_post_guard"
                row[11] = rec["orig_order"]  # wm_order column
                reverted_pairs += 1

        # Triples
        for rec in applied_triples:
            names = [rec["a"].getName(), rec["b"].getName(), rec["c"].getName()]
            if any(_is_bad(n) for n in names):
                for inst in (rec["a"], rec["b"], rec["c"]):
                    nm = inst.getName()
                    ox, oy = rec["orig_pos"][nm]
                    inst.setLocation(ox, oy)
                row = csv_rows[rec["csv_idx"]]
                row[-4] = 0
                row[-3] = 0
                row[-2] = False
                row[-1] = "reverted_post_guard"
                row[11] = rec["orig_order"]
                reverted_triples += 1

        if args.post_guard_final_check and (reverted_pairs or reverted_triples):
            t_phase = time.time()
            _log(
                "post-guard: optional final STA check "
                f"(reverted_pairs={reverted_pairs} reverted_triples={reverted_triples})"
            )
            final_slacks = wc.compute_worst_slacks(design, list(guard_insts.values()))
            n_below = sum(1 for v in final_slacks.values() if v < bad_floor)
            _log(
                f"post-guard: final STA done in {time.time() - t_phase:.2f}s "
                f"insts_below_floor={n_below}"
            )
        else:
            _log(
                f"post-guard: reverted_pairs={reverted_pairs} "
                f"reverted_triples={reverted_triples}"
            )

    # Free large caches before DPL.
    hpwl_cache = None
    neighbor_min_slack = {}

    dpl = design.getOpendp()
    max_disp_x = max(1, int(design.micronToDBU(args.max_disp_x_um) / sw))
    max_disp_y = max(1, int(design.micronToDBU(args.max_disp_y_um) / sh))
    t_phase = time.time()
    _log(
        f"running incremental detailedPlacement max_disp_x={max_disp_x} sites "
        f"max_disp_y={max_disp_y} rows"
    )
    dpl.detailedPlacement(max_disp_x, max_disp_y, "", True)
    try:
        dpl.checkPlacement(False)
    except Exception as e:
        _log(f"checkPlacement warning: {e}")
    _log(f"incremental detailedPlacement done in {time.time() - t_phase:.2f}s")

    n_pairs = len(selected_pairs)
    n_groups = len(selected_groups)
    n_pair_ok = sum(
        1 for row in csv_rows
        if row[0] == "pair" and row[-2] is True
    )
    n_group_ok = sum(
        1 for row in csv_rows
        if row[0] == "triple" and row[-2] is True
    )
    _log(
        f"pairs={n_pairs} groups={n_groups} "
        f"csv_pair_ok={n_pair_ok} csv_group_ok={n_group_ok} "
        f"changed_pairs={pair_changed} changed_groups={group_changed} "
        f"(pre-dpl embed counts pair_sat={pair_sat} group_sat={group_sat})"
    )

    pair_fail = n_pairs - n_pair_ok
    group_fail = n_groups - n_group_ok
    pc_p = wc.binomial_pc(n_pairs, pair_fail, 0.5) if n_pairs else 1.0
    pc_g = wc.binomial_pc(n_groups, group_fail, 1.0 / 6.0) if n_groups else 1.0
    _log(f"Pc pairs (p=1/2)<={pc_p:.3e}  groups (p=1/6)<={pc_g:.3e}")

    t_phase = time.time()
    _log("writing ODB/DEF/CSV")
    design.writeDb(args.output_odb)
    if args.output_def:
        design.writeDef(args.output_def)

    if args.output_cell_list:
        with open(args.output_cell_list, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(csv_rows)
        _log(f"cell list -> {args.output_cell_list}")

    _log(f"write outputs done in {time.time() - t_phase:.2f}s")
    _log("done")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
