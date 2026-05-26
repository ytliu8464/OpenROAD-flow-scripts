#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""AutoMarks placement watermark (ZhangRPK24 / ZhangRPK25TODAES) -- embed.

AutoMarks differs from ICMarks by replacing the sliding-window region
search with a Graph-Neural-Network that scores every cell as a candidate
region center, then picks argmin. The downstream Detailed Watermarking
(DW) step is the same as ICMarks.

Faithful re-implementation
--------------------------
Training and running the GNN model from the original paper requires
DREAMPlace-formatted inputs, S-BERT cell-name embeddings, and several
days of label collection -- not feasible as an inline baseline in the
OpenROAD flow used by every other method here. We therefore replace the
GNN with a *node-centered heuristic score* that captures the features the
GNN was trained to predict (per Table 1 of the AutoMarks paper:
cell location, cell size, cell name semantics; per Equation 2 the score
is a normalized inverse-WL-improvement signal).

Per-node score (lower is better):
  s(c) = w_macro * d_macro(c)^(-1)            # nearer macros -> worse
       + w_density * local_cell_count(c, R)   # denser neighborhood -> worse
       - w_room * maneuvering_room(c)         # more x/y room -> better
       + w_cong * pin_count(c)                # bigger pin count -> worse

Region centered at argmin-score cell, of size W_w x H_w. The K selected
cells inside the region are watermarked with DW (same axis shift as
ICMarks). This gives a comparable signal -- a *cell-centered low-impact
region* -- without the GNN training infrastructure, and uses only ODB
information that any post-DP layout can provide.

Verification uses the same parity convention as ICMarks (Pc = 0.5^K).

Run as:
  openroad -python -exit embed.py \\
      --odb /path/to/3_place.odb \\
      --seed-hex /path/to/seed_placement.hex \\
      --platform nangate45 --design aes --variant watermarking-test1 \\
      [--k INT]
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path
from typing import List, Tuple

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent.parent))

from _common import (  # noqa: E402
    capacity_for,
    load_seed_hex,
    select_top_k,
    target_bit,
    hmac_u32,
    pc_stage,
)

_T0 = time.time()


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')} +{time.time()-_T0:6.1f}s] [automarks:embed] {msg}", flush=True)


def _is_buffer_or_clock(inst) -> bool:
    name = inst.getMaster().getName().upper()
    return any(t in name for t in ("CLKBUF", "CLKINV", "TIEH", "TIEL", "FILL"))


def _is_eligible(inst) -> bool:
    from odb import dbPlacementStatus
    status = inst.getPlacementStatus()
    if status == dbPlacementStatus.FIRM or status == dbPlacementStatus.LOCKED:
        return False
    if inst.isFixed():
        return False
    if not inst.isPlaced():
        return False
    master = inst.getMaster()
    if master.isBlock():
        return False
    if _is_buffer_or_clock(inst):
        return False
    return True


def _core_bbox(block) -> Tuple[int, int, int, int]:
    bb = block.getCoreArea()
    return bb.xMin(), bb.yMin(), bb.xMax(), bb.yMax()


def _site_width(block) -> int:
    for row in block.getRows():
        s = row.getSite()
        if s is not None:
            return s.getWidth()
    raise RuntimeError("no rows")


def _row_height(block) -> int:
    for row in block.getRows():
        s = row.getSite()
        if s is not None:
            return s.getHeight()
    raise RuntimeError("no rows")


def _macro_centers(block) -> List[Tuple[int, int]]:
    out = []
    for inst in block.getInsts():
        if inst.getMaster().isBlock():
            bb = inst.getBBox()
            out.append(((bb.xMin() + bb.xMax()) // 2, (bb.yMin() + bb.yMax()) // 2))
    return out


def _grid_index(cells, cell_size):
    """Bucket cells into (col, row) grid. Returns dict cell -> (col, row) and grid -> list."""
    grid: dict = {}
    cell_to_bucket: dict = {}
    for c in cells:
        cx, cy, _w, _h, name = c
        col = cx // cell_size
        row = cy // cell_size
        cell_to_bucket[name] = (col, row)
        grid.setdefault((col, row), []).append(c)
    return grid, cell_to_bucket


def _local_density(grid, col, row, radius=1) -> int:
    n = 0
    for dc in range(-radius, radius + 1):
        for dr in range(-radius, radius + 1):
            n += len(grid.get((col + dc, row + dr), ()))
    return n


def _nearest_macro_dist(cx, cy, macro_centers) -> float:
    if not macro_centers:
        return 1e18
    best = 1e18
    for (mx, my) in macro_centers:
        d = ((cx - mx) ** 2 + (cy - my) ** 2) ** 0.5
        if d < best:
            best = d
    return best


def _parse_args(argv: List[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="AutoMarks embedder")
    ap.add_argument("--odb",       required=True)
    ap.add_argument("--out-odb",   default="")
    ap.add_argument("--seed-hex",  required=True, help="path to seed_placement.hex")
    ap.add_argument("--platform",  required=True)
    ap.add_argument("--design",    required=True)
    ap.add_argument("--variant",   required=True)
    ap.add_argument("--k",         type=int, default=0)
    ap.add_argument("--out-csv",   default="")
    ap.add_argument("--window-w-sites", type=int, default=80)
    ap.add_argument("--window-h-rows",  type=int, default=20)
    # Score weights (Eq.2-style heuristic; tunable but not critical for Pc)
    ap.add_argument("--w-macro",   type=float, default=1.0)
    ap.add_argument("--w-density", type=float, default=0.01)
    ap.add_argument("--w-room",    type=float, default=0.5)
    ap.add_argument("--w-pin",     type=float, default=0.02)
    return ap.parse_args(argv)


def main(argv: List[str]) -> None:
    args = _parse_args(argv)

    from openroad import Design, Tech
    tech = Tech()
    design = Design(tech)
    _log(f"loading {args.odb}")
    design.readDb(args.odb)
    block = design.getBlock()

    seed = load_seed_hex(args.seed_hex)
    K = args.k if args.k > 0 else capacity_for(args.platform, args.design, args.variant)
    _log(f"K={K}  seed={seed[:4].hex()}...")

    site_w = _site_width(block)
    row_h = _row_height(block)
    cx_min, cy_min, cx_max, cy_max = _core_bbox(block)
    W_w = args.window_w_sites * site_w
    W_h = args.window_h_rows * row_h

    # -------- collect eligible cells with features --------
    eligible_recs: List[Tuple[int, int, int, int, str]] = []  # (cx, cy, w, h, name)
    pin_count: dict = {}
    for inst in block.getInsts():
        if not _is_eligible(inst):
            continue
        bb = inst.getBBox()
        cx = (bb.xMin() + bb.xMax()) // 2
        cy = (bb.yMin() + bb.yMax()) // 2
        eligible_recs.append((cx, cy, bb.xMax() - bb.xMin(), bb.yMax() - bb.yMin(), inst.getName()))
        try:
            pin_count[inst.getName()] = len(list(inst.getITerms()))
        except Exception:
            pin_count[inst.getName()] = 0
    _log(f"eligible cells: {len(eligible_recs)}")

    macro_centers = _macro_centers(block)
    # bucket cells for local density at scale ~ window
    grid_size = max(W_w, W_h) // 2 or 1
    grid, cell_bucket = _grid_index(eligible_recs, grid_size)

    # -------- score each eligible cell as a candidate region center --------
    # We only score candidates that have room for a W_w x W_h window centered on them.
    half_w, half_h = W_w // 2, W_h // 2
    best_name, best_score = None, float("inf")
    for (cx, cy, _w, _h, name) in eligible_recs:
        # Window must fit inside core area
        x0 = cx - half_w
        y0 = cy - half_h
        if x0 < cx_min or x0 + W_w > cx_max or y0 < cy_min or y0 + W_h > cy_max:
            continue

        d_macro = _nearest_macro_dist(cx, cy, macro_centers)
        col, row = cell_bucket[name]
        dens = _local_density(grid, col, row, radius=1)
        # maneuvering room (cheap proxy: distance from cell to window edges)
        room = (min(cx - x0, x0 + W_w - cx) + min(cy - y0, y0 + W_h - cy))
        pins = pin_count.get(name, 0)

        score = (args.w_macro * (1.0 / max(1.0, d_macro))
                 + args.w_density * dens
                 - args.w_room * (room / max(W_w, W_h))
                 + args.w_pin * pins)
        if score < best_score:
            best_score = score
            best_name = name

    if best_name is None:
        raise RuntimeError(
            f"no eligible cell can host a {W_w}x{W_h} window inside the core; "
            "shrink --window-w-sites / --window-h-rows."
        )

    # -------- define region centered on best cell --------
    name_to_inst = {inst.getName(): inst for inst in block.getInsts()}
    anchor = name_to_inst[best_name]
    abb = anchor.getBBox()
    acx = (abb.xMin() + abb.xMax()) // 2
    acy = (abb.yMin() + abb.yMax()) // 2
    rx0 = max(cx_min, acx - half_w)
    ry0 = max(cy_min, acy - half_h)
    rx1 = min(cx_max, rx0 + W_w)
    ry1 = min(cy_max, ry0 + W_h)
    _log(f"AM: anchor={best_name}  score={best_score:.4f}  region=({rx0},{ry0})-({rx1},{ry1})")

    # cells inside region
    names_inside = [
        nm for (cx, cy, _w, _h, nm) in eligible_recs
        if rx0 <= cx < rx1 and ry0 <= cy < ry1
    ]
    _log(f"cells inside region: {len(names_inside)}")
    if len(names_inside) < K:
        _log(f"WARNING: only {len(names_inside)} cells in region; reducing K to {len(names_inside)}")
        K = len(names_inside)

    selected = select_top_k(seed, b"automarks_dw", names_inside, K)
    _log(f"DW: selected {len(selected)} cells")

    DOMAIN_BIT = b"automarks_dw"
    DOMAIN_AXIS = b"automarks_axis"
    rows: List[dict] = []
    miss = 0

    for inst_name in sorted(selected):
        inst = name_to_inst.get(inst_name)
        if inst is None:
            continue
        axis = hmac_u32(seed, DOMAIN_AXIS, inst_name) & 1
        tbit = target_bit(seed, DOMAIN_BIT, inst_name)

        ox, oy = inst.getOrigin()
        if axis == 0:
            cur_bit = ((ox - cx_min) // site_w) & 1
        else:
            cur_bit = (oy // row_h) & 1

        satisfied = False
        skipped = ""
        new_x, new_y = ox, oy

        if cur_bit == tbit:
            satisfied = True
        else:
            if axis == 0:
                for delta in (site_w, -site_w, 3 * site_w, -3 * site_w):
                    cand = ox + delta
                    if cx_min <= cand and (cand + inst.getMaster().getWidth()) <= cx_max:
                        new_x = cand
                        satisfied = True
                        break
            else:
                for delta in (row_h, -row_h, 3 * row_h, -3 * row_h):
                    cand = oy + delta
                    if ry0 <= cand and (cand + inst.getMaster().getHeight()) <= ry1:
                        new_y = cand
                        satisfied = True
                        break
            if not satisfied:
                skipped = "no_room"
                miss += 1

        if (new_x, new_y) != (ox, oy):
            inst.setOrigin(new_x, new_y)

        rows.append({
            "inst":       inst_name,
            "axis":       axis,
            "target_bit": tbit,
            "anchor":     best_name,
            "rw_x0":      rx0,
            "rw_y0":      ry0,
            "rw_x1":      rx1,
            "rw_y1":      ry1,
            "satisfied":  satisfied,
            "skipped":    skipped,
        })

    _log(f"pre-legalization: satisfied={len(rows)-miss}/{K}  miss={miss}")

    _log("running detailed_placement to legalize DW shifts...")
    try:
        design.getOpendp().detailedPlacement(0, 0, "", False)
    except Exception as e:
        _log(f"WARNING: detailed_placement raised: {e}")

    in_region = 0
    missed_post = 0
    for row in rows:
        if not row["satisfied"]:
            missed_post += 1
            continue
        inst = name_to_inst.get(row["inst"])
        if inst is None:
            row["satisfied"] = False
            missed_post += 1
            continue
        ox_post, oy_post = inst.getOrigin()
        cx_post = ox_post + inst.getMaster().getWidth() // 2
        cy_post = oy_post + inst.getMaster().getHeight() // 2
        if rx0 <= cx_post < rx1 and ry0 <= cy_post < ry1:
            in_region += 1
        if row["axis"] == 0:
            cur_bit = ((ox_post - cx_min) // site_w) & 1
        else:
            cur_bit = (oy_post // row_h) & 1
        if cur_bit != row["target_bit"]:
            row["satisfied"] = False
            missed_post += 1

    x = K - missed_post
    pc = pc_stage(K, K - x, 0.5)
    _log(f"post-legalization: accepted={x}/{K}  Pc={pc:.2e}  in_region={in_region}/{K}")

    out_dir = Path(args.odb).parent
    csv_path = args.out_csv or str(out_dir / "automarks_embed.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["inst", "axis", "target_bit", "anchor",
                                          "rw_x0", "rw_y0", "rw_x1", "rw_y1",
                                          "satisfied", "skipped"])
        w.writeheader()
        w.writerows(rows)
    _log(f"embed CSV -> {csv_path}")

    odb_out = args.out_odb or str(out_dir / "3_place_automarks.odb")
    design.writeDb(odb_out)
    _log(f"watermarked ODB -> {odb_out}")

    print(f"AUTOMARKS_EMBED: K={K} accepted={x} Pc={pc:.4e} in_region={in_region}", flush=True)


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    main(argv)
