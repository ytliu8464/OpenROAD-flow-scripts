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
    macro_index: wc.MacroIndex,
    dense_tiles: Set[Tuple[int, int]],
    include_groups: bool,
) -> Tuple[List[dict], List[dict]]:
    pair_rows: List[dict] = []
    triple_rows: List[dict] = []

    for key, cells in buckets.items():
        tile_xy = (key[0][0], key[0][1])
        if tile_xy in dense_tiles:
            continue
        if len(cells) < 2:
            continue
        sorted_cells = wc.sort_instances_by_x(cells)
        for a, b in wc.enumerate_close_pairs_sorted_row(sorted_cells, pair_dist_dbu):
            if macro_index.near_blockage(a) or macro_index.near_blockage(b):
                continue
            dhp = wc.swap_delta_hpwl(a, b)
            if abs(dhp) > eps_pair:
                continue
            tid = wc.tile_of(a, bbox, tw, th, nx, ny)
            pair_rows.append({
                "kind": "pair",
                "tile_id": tid,
                "a": a,
                "b": b,
                "dhp": dhp,
            })

        if include_groups and len(sorted_cells) >= 3:
            for a, b, c in wc.enumerate_close_triples_sorted_row(sorted_cells, pair_dist_dbu):
                if macro_index.near_blockage(a) or macro_index.near_blockage(b):
                    continue
                if macro_index.near_blockage(c):
                    continue
                costs = wc.triple_all_perm_hpwl_costs(a, b, c)
                spread = max(costs) - min(costs)
                tid = wc.tile_of(a, bbox, tw, th, nx, ny)
                triple_rows.append({
                    "kind": "triple",
                    "tile_id": tid,
                    "a": a,
                    "b": b,
                    "c": c,
                    "costs": costs,
                    "spread": spread,
                })

    return pair_rows, triple_rows


def _pair_key_fn(seed: bytes, tid: Tuple[int, int], r: dict) -> bytes:
    return wc.pair_sort_key(seed, tid, r["a"].getName(), r["b"].getName())


def _group_key_fn(seed: bytes, tid: Tuple[int, int], r: dict) -> bytes:
    return wc.group_sort_key(
        seed, tid, (r["a"].getName(), r["b"].getName(), r["c"].getName())
    )


def _select_non_overlap(
    tile_id: Tuple[int, int],
    rows: Sequence[dict],
    quota: int,
    used: Set[str],
    key_fn: Callable[[Tuple[int, int], dict], bytes],
    spread_max: Optional[int] = None,
) -> List[dict]:
    pool = [r for r in rows if r["tile_id"] == tile_id]
    if spread_max is not None:
        pool = [r for r in pool if r.get("spread", 0) <= spread_max]
    pool.sort(key=lambda r: key_fn(tile_id, r), reverse=True)
    picked: List[dict] = []
    for r in pool:
        if r["kind"] == "pair":
            na, nb = r["a"].getName(), r["b"].getName()
            if na in used or nb in used:
                continue
            used.add(na)
            used.add(nb)
            picked.append(r)
        else:
            na = r["a"].getName()
            nb = r["b"].getName()
            nc = r["c"].getName()
            if na in used or nb in used or nc in used:
                continue
            used.add(na)
            used.add(nb)
            used.add(nc)
            picked.append(r)
        if len(picked) >= quota:
            break
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
        default=float(os.environ.get("WM_PAIR_DIST_UM", "5")),
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
        default=int(os.environ.get("WM_USE_GROUPS", "1")),
    )
    p.add_argument(
        "--hpwl-eps-pair-dbu", type=int,
        default=int(os.environ.get("WM_HPWL_EPS_PAIR_DBU", "500")),
    )
    p.add_argument(
        "--hpwl-eps-group-dbu", type=int,
        default=int(os.environ.get("WM_HPWL_EPS_GROUP_DBU", "500")),
    )
    p.add_argument(
        "--fanout-max", type=int,
        default=int(os.environ.get("WM_FANOUT_MAX", "16")),
    )
    p.add_argument(
        "--slack-threshold-ns", type=float,
        default=float(os.environ.get("WM_SLACK_THRESHOLD_NS", "0.05")),
    )
    p.add_argument(
        "--crit-bin-ns", type=float,
        default=float(os.environ.get("WM_CRIT_BIN_NS", "0.05")),
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
    p.add_argument("--sdc", default=os.environ.get("WM_SDC", ""))
    p.add_argument("--max-disp-micron", type=float, nargs=2, metavar=("X", "Y"), default=None)
    p.add_argument("--message", default=os.environ.get("WM_MESSAGE", ""))
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
    for inst in kept:
        if macro_index.near_blockage(inst):
            continue
        y = wc.inst_bottom_left(inst)[1]
        ry = wc.snap_row_y(y, row_bottoms)
        mw = wc.inst_size(inst)[0]
        slack = slack_map.get(inst.getName(), args.slack_threshold_ns * 1e-9)
        tid = wc.tile_of(inst, bbox, tw, th, nx, ny)
        key = wc.make_bucket_key(tid, ry, mw, slack, args.crit_bin_ns)
        buckets.setdefault(key, []).append(inst)

    density = wc.compute_tile_density(kept, bbox, nx, ny, tw, th)
    dense_tiles = {t for t, d in density.items() if d > args.tile_density_max}
    _log(
        f"bucket build done in {time.time() - t_phase:.2f}s: "
        f"buckets={len(buckets)} dense_tiles={len(dense_tiles)}"
    )

    t_phase = time.time()
    _log("enumerating local pair/triple candidates")
    pair_cands, triple_cands = _collect_candidates_same_row(
        buckets, bbox, tw, th, nx, ny,
        pair_dist_dbu, args.hpwl_eps_pair_dbu, macro_index, dense_tiles,
        include_groups=bool(args.use_groups),
    )
    _log(
        f"candidate enumeration done in {time.time() - t_phase:.2f}s: "
        f"pair_candidates={len(pair_cands)} triple_candidates={len(triple_cands)}"
    )

    pk = lambda tid, r: _pair_key_fn(seed, tid, r)
    gk = lambda tid, r: _group_key_fn(seed, tid, r)

    t_phase = time.time()
    _log("selecting non-overlapping keyed constraints")
    all_tiles_set = {(tx, ty) for tx in range(nx) for ty in range(ny)}
    used_names: Set[str] = set()
    selected_pairs: List[dict] = []
    selected_groups: List[dict] = []

    for tid in sorted(all_tiles_set):
        selected_pairs.extend(
            _select_non_overlap(tid, pair_cands, args.pairs_per_tile, used_names, pk)
        )
        if args.use_groups:
            selected_groups.extend(
                _select_non_overlap(
                    tid,
                    triple_cands,
                    args.groups_per_tile,
                    used_names,
                    gk,
                    spread_max=args.hpwl_eps_group_dbu,
                )
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

        if abs(r["dhp"]) > args.hpwl_eps_pair_dbu:
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

    group_sat = 0
    group_changed = 0
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

    dpl = design.getOpendp()
    if args.max_disp_micron is None:
        mx = float(os.environ.get("WM_MAX_DISP_X", "50"))
        my = float(os.environ.get("WM_MAX_DISP_Y", "50"))
    else:
        mx, my = float(args.max_disp_micron[0]), float(args.max_disp_micron[1])
    max_disp_x = max(1, int(design.micronToDBU(mx) / sw))
    max_disp_y = max(1, int(design.micronToDBU(my) / sh))
    _log(f"triple apply done in {time.time() - t_phase:.2f}s: changed={group_changed}")
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

    if args.message:
        _log(f"message tag: {args.message!r}")
    _log(f"write outputs done in {time.time() - t_phase:.2f}s")
    _log("done")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
