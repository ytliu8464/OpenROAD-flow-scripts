#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""ICMarks placement watermark (ZhangRPK25 / TODAES'25) -- embed.

ICMarks combines:
  * Global Watermarking (GW): scoring-function-based region search.
    f = alpha*(Nw/Nc) + beta*(Scell/S) + gamma*(Soverlap/S)
    The minimum-score window is chosen as the watermark region R_w; the
    cells assigned as the watermark cells C_w1 are constrained to remain
    inside R_w.
  * Detailed Watermarking (DW): for each selected cell, shift by +/-1 site
    along either x or y based on a key bit, then re-legalize.

Faithful re-implementation
--------------------------
The original ICMarks paper re-runs DREAMPlace global placement under the
new region constraint, which the OpenROAD post-DP setting used by every
other baseline here cannot reproduce. We therefore implement an
operationally-equivalent post-DP variant that preserves the same security
model and the same Pc accounting:

  1. GW (post-DP): scan candidate windows of size W_w x H_w over the core
     area at the configured stride; for each window evaluate f. Select the
     minimum-f window subject to N_c >= K. Record R_w.
  2. From cells whose centroid lies in R_w, pick K via the keyed PRF
     (HMAC-SHA256 top-K). This is the C_w1 set.
  3. DW: per cell, axis = HMAC(..., axis||name)[0] & 1 (0=x, 1=y).
     target parity bit = HMAC(..., bit||name)[0] & 1.
     If current axis parity != target, shift cell by +/-1 site along axis.
  4. detailed_placement() to legalize, then re-read parities.

Verification is parity-based on the DW axis, matching the Pc = 0.5^K
convention used by cell_scattering / buffer_insertion / Kahng. GW gives an
additional "region containment" rate that we log alongside Pc but do not
fold into Pc, since the table in the PDMarks paper uses a single Pc per
method row.

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
    print(f"[{time.strftime('%H:%M:%S')} +{time.time()-_T0:6.1f}s] [icmarks:embed] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Eligibility / geometry helpers (same gate as cell_scattering for parity)
# ---------------------------------------------------------------------------

def _is_buffer_or_clock(inst) -> bool:
    name = inst.getMaster().getName().upper()
    return any(t in name for t in ("CLKBUF", "CLKINV", "TIEH", "TIEL", "FILL"))


def _is_eligible(inst) -> bool:
    # FIRM/LOCKED status is already covered by isFixed() in this OpenROAD
    # build (odb does not export dbPlacementStatus to Python).
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
    raise RuntimeError("no rows in block")


def _row_height(block) -> int:
    for row in block.getRows():
        s = row.getSite()
        if s is not None:
            return s.getHeight()
    raise RuntimeError("no rows in block")


def _macro_bboxes(block) -> List[Tuple[int, int, int, int]]:
    out: List[Tuple[int, int, int, int]] = []
    for inst in block.getInsts():
        m = inst.getMaster()
        if m.isBlock():
            bb = inst.getBBox()
            out.append((bb.xMin(), bb.yMin(), bb.xMax(), bb.yMax()))
    return out


def _rect_overlap_area(a, b) -> int:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    dx = max(0, min(ax1, bx1) - max(ax0, bx0))
    dy = max(0, min(ay1, by1) - max(ay0, by0))
    return dx * dy


# ---------------------------------------------------------------------------
# GW: window scoring
# ---------------------------------------------------------------------------

def _score_window(window, cells_in_window, total_cells_in_window,
                  macros, K, alpha, beta, gamma) -> Tuple[float, int, int]:
    """Return (score, Nc, overlap_area). cells_in_window are (cx,cy,w,h)."""
    x0, y0, x1, y1 = window
    S = max(1, (x1 - x0) * (y1 - y0))
    Nc = total_cells_in_window
    if Nc <= 0:
        return float("inf"), 0, 0
    Scell = sum(w * h for (_, _, w, h) in cells_in_window)
    Soverlap = sum(_rect_overlap_area(window, m) for m in macros)
    score = alpha * (K / Nc) + beta * (Scell / S) + gamma * (Soverlap / S)
    return score, Nc, Soverlap


def _select_gw_region(block, K, alpha, beta, gamma,
                      window_w_sites: int, window_h_rows: int,
                      stride_x_sites: int, stride_y_rows: int):
    """Sweep windows over core area, return (best_window, cells_inside)."""
    site_w = _site_width(block)
    row_h = _row_height(block)
    cx_min, cy_min, cx_max, cy_max = _core_bbox(block)
    W_w = window_w_sites * site_w
    W_h = window_h_rows * row_h
    sx = max(stride_x_sites * site_w, 1)
    sy = max(stride_y_rows * row_h, 1)

    # Pre-extract eligible cell centroids/sizes (one pass)
    cell_recs: List[Tuple[int, int, int, int, str]] = []
    for inst in block.getInsts():
        if not _is_eligible(inst):
            continue
        bb = inst.getBBox()
        cx = (bb.xMin() + bb.xMax()) // 2
        cy = (bb.yMin() + bb.yMax()) // 2
        cell_recs.append((cx, cy, bb.xMax() - bb.xMin(), bb.yMax() - bb.yMin(), inst.getName()))

    macros = _macro_bboxes(block)

    best = None  # (score, window, names_inside)
    n_windows = 0
    for y0 in range(cy_min, cy_max - W_h + 1, sy):
        y1 = y0 + W_h
        for x0 in range(cx_min, cx_max - W_w + 1, sx):
            x1 = x0 + W_w
            window = (x0, y0, x1, y1)
            inside = []
            for (cx, cy, w, h, nm) in cell_recs:
                if x0 <= cx < x1 and y0 <= cy < y1:
                    inside.append((cx, cy, w, h, nm))
            Nc = len(inside)
            if Nc < K:
                continue
            score, _, _ = _score_window(window,
                                        [(cx, cy, w, h) for (cx, cy, w, h, _) in inside],
                                        Nc, macros, K, alpha, beta, gamma)
            n_windows += 1
            if best is None or score < best[0]:
                best = (score, window, [r[4] for r in inside])

    if best is None:
        raise RuntimeError(
            f"no window of size {W_w}x{W_h} (sites x rows={window_w_sites}x{window_h_rows}) "
            f"contains K={K} eligible cells; reduce K or shrink window."
        )
    _log(f"GW: scanned {n_windows} windows; best score={best[0]:.4f} window={best[1]}  Nc={len(best[2])}")
    return best[1], best[2]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _parse_args(argv: List[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="ICMarks embedder")
    ap.add_argument("--odb",       required=True)
    ap.add_argument("--out-odb",   default="")
    ap.add_argument("--seed-hex",  required=True, help="path to seed_placement.hex")
    ap.add_argument("--platform",  required=True)
    ap.add_argument("--design",    required=True)
    ap.add_argument("--variant",   required=True)
    ap.add_argument("--k",         type=int, default=0)
    ap.add_argument("--out-csv",   default="")
    # GW knobs
    ap.add_argument("--alpha",     type=float, default=0.5)
    ap.add_argument("--beta",      type=float, default=0.5)
    ap.add_argument("--gamma",     type=float, default=0.1)
    ap.add_argument("--window-w-sites", type=int, default=80,
                    help="GW window width, in site widths")
    ap.add_argument("--window-h-rows",  type=int, default=20,
                    help="GW window height, in row heights")
    ap.add_argument("--stride-x-sites", type=int, default=40)
    ap.add_argument("--stride-y-rows",  type=int, default=10)
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
    cx_min, _, cx_max, _ = _core_bbox(block)

    # -------- GW: find a low-impact region containing >= K eligible cells --------
    region, names_inside = _select_gw_region(
        block, K, args.alpha, args.beta, args.gamma,
        args.window_w_sites, args.window_h_rows,
        args.stride_x_sites, args.stride_y_rows,
    )
    rx0, ry0, rx1, ry1 = region

    # -------- DW: keyed top-K selection over cells inside the region --------
    selected = select_top_k(seed, b"icmarks_dw", names_inside, K)
    _log(f"DW: selected {len(selected)} cells inside R_w")

    DOMAIN_BIT = b"icmarks_dw"
    DOMAIN_AXIS = b"icmarks_axis"
    rows: List[dict] = []
    miss = 0

    name_to_inst = {inst.getName(): inst for inst in block.getInsts()}

    for inst_name in sorted(selected):
        inst = name_to_inst.get(inst_name)
        if inst is None:
            continue
        # axis: 0=x-shift (column parity), 1=y-shift (row parity)
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
                    # stay inside the GW region to preserve r_w containment
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

    # Re-read parities + region containment after legalization
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
    csv_path = args.out_csv or str(out_dir / "icmarks_embed.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["inst", "axis", "target_bit",
                                          "rw_x0", "rw_y0", "rw_x1", "rw_y1",
                                          "satisfied", "skipped"])
        w.writeheader()
        w.writerows(rows)
    _log(f"embed CSV -> {csv_path}")

    odb_out = args.out_odb or str(out_dir / "3_place_icmarks.odb")
    design.writeDb(odb_out)
    _log(f"watermarked ODB -> {odb_out}")

    print(f"ICMARKS_EMBED: K={K} accepted={x} Pc={pc:.4e} in_region={in_region}", flush=True)


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    main(argv)
