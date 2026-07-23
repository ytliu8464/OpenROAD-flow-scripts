#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Dump the targeted-attack training dataset (paper §7.2), run *inside*
OpenROAD-python so it can read the leaked layout's ODB.

The previous targeted attacker trained on the *embed CSV* alone, which is
methodologically wrong:

  * Placement: the embed CSV contains only committed watermark tuples, so the
    label column is single-class (all positives) and no classifier can be
    trained -> AUC reported as n/a, which looks like "perfect security" but is
    really a dataset-construction bug.
  * CTS: the embed CSV is the embedder's candidate-*attempt* trail (committed
    pairs + internally-rejected candidates with reasons no_boundary_ff /
    lcb_already_used), and many of its columns (num_reassigned, *_after,
    target_is_A) are embed-time bookkeeping that leak the key / the embedder's
    actions.  Training on it answers "can I separate committed pairs from the
    embedder's own rejects using its private log?" (nearly tautological) rather
    than the Kerckhoffs question.

This script implements the correct construction:

  1. Reconstruct the *public eligible* object set E_s from the leaked ODB using
     the same public rules the embedder/verifier use (lib.eligibility for
     placement; the embedder's pure/quasi classification + proximity pairing
     for CTS).
  2. Label positive = key-selected watermark objects WM_s (the committed
     tuples/pairs recorded in the embed CSV), negative = E_s \\ WM_s.  Because a
     keyless attacker reconstructs E_s from the *post-embed* leaked layout while
     the embedder enumerated on the *pre-embed* layout, a few committed
     positives may not reappear in the reconstruction (an LCB reclassified after
     sink reassignment, a swapped tuple now wider than the close-pair window).
     We therefore take E_s = reconstruction ∪ WM_committed so that WM_s ⊆ E_s
     holds exactly (matching the paper's dataset definition), looking the missing
     positives up by instance name and computing their features the same way.
  3. Compute *observable* features only -- everything derivable from the leaked
     layout, nothing keyed and nothing from the embedder's private log.  In
     particular CTS features are keyed on the deterministic name order
     (L_A < L_B), never on the key-derived "target"/"other" roles.

Output: a CSV with header  object_id,label,<feature columns...>  one row per
eligible object.  ``run_targeted_attack.py`` reads it, trains the classifier,
and ranks objects for the removal attack.

Env:
    STAGE              "placement" | "cts"
    WM_ODB             leaked layout ODB (3_place_order_wm.odb / 4_cts_wm.odb)
    WM_EMBED_CSV       embed CSV (committed-object source for positive labels)
    WM_OUT_FEATURES    output features+labels CSV path
    WM_PAIR_DIST_UM    placement close-pair window in microns (default 1)
    WM_NEIGHBOR_K      placement bounded-K neighbor enumeration (default 2)
    WM_INCLUDE_TRIPLES "1" to include 3-tuples (default 1)
    WM_CTS_SIBLING_DIST_UM  CTS proximity threshold in microns (default 20)
    WM_CTS_RMAX        CTS quasi-leaf repair-fanout cap (default from common)
"""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_WM_ROOT = _HERE.parents[3]                       # .../flow/watermarking
_EXP_DIR = _WM_ROOT / "experiments"
sys.path.insert(0, str(_WM_ROOT / "place_ordering"))
sys.path.insert(0, str(_WM_ROOT / "cts_v2"))
sys.path.insert(0, str(_EXP_DIR))
sys.path.insert(0, str(_EXP_DIR / "lib"))

import openroad as ord_  # type: ignore


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------

PLACE_HEADER = [
    "tile_density", "row_y_norm", "x_norm",
    "cell_w_mean_dbu", "cell_h_mean_dbu", "area_mean_dbu2",
    "fanout_max", "fanout_mean", "fanout_absdiff",
]


def _grid_dims(block, bbox):
    """Pick a tile grid roughly the size of the watermark embedder's: ~one tile
    per ~40 site-pitches, clamped to a sane range so density is meaningful."""
    import watermark_common as wc  # type: ignore
    x0, y0, x1, y1 = bbox
    try:
        rows = list(block.getRows())
        sw = rows[0].getSite().getWidth() if rows else 0
        sh = rows[0].getSite().getHeight() if rows else 0
    except Exception:
        sw = sh = 0
    tile_um = 20.0
    upm = block.getDbUnitsPerMicron()
    tw = max(1.0, tile_um * upm)
    nx = max(1, min(256, int(round((x1 - x0) / tw))))
    ny = max(1, min(256, int(round((y1 - y0) / tw))))
    return nx, ny


def dump_placement(design):
    import watermark_common as wc  # type: ignore
    from lib.eligibility import reconstruct_placement_pool_embedder
    block = design.getBlock()

    pair_um = float(os.environ.get("WM_PAIR_DIST_UM", "1"))
    k_neigh = int(os.environ.get("WM_NEIGHBOR_K", "2"))
    grid_nx = int(os.environ.get("WM_GRID_NX", "6"))   # run_place_wm.sh default
    grid_ny = int(os.environ.get("WM_GRID_NY", "6"))
    fdiff = int(os.environ.get("WM_FANOUT_DIFF_MAX", "4"))
    fmax = int(os.environ.get("WM_FANOUT_MAX", "16"))
    slack_ns = float(os.environ.get("WM_SLACK_THRESHOLD_NS", "0.10"))
    tile_dmax = float(os.environ.get("WM_TILE_DENSITY_MAX", "1.2"))
    sdc_path = os.environ.get("WM_SDC", "") or None
    upm = block.getDbUnitsPerMicron()
    max_dx_dbu = int(pair_um * upm)

    # --- committed positives (from embed CSV), canonicalized by name-set ------
    embed_csv = Path(os.environ["WM_EMBED_CSV"])
    pos_namesets = set()           # frozenset(names) of each committed tuple
    pos_name_lists = {}            # frozenset -> ordered name list (for lookup)
    if embed_csv.exists():
        for r in csv.DictReader(open(embed_csv)):
            skip = str(r.get("skipped_reason", "")).strip()
            if skip not in ("", "already_satisfied"):
                continue
            sat = str(r.get("satisfied", "True")).strip().lower()
            if sat in ("false", "0", "no"):
                continue
            names = [n for n in (r.get("A_name", ""), r.get("B_name", ""),
                                 r.get("C_name", "")) if n]
            if len(names) < 2:
                continue
            fs = frozenset(names)
            pos_namesets.add(fs)
            pos_name_lists[fs] = names
    pos_cell_names = set().union(*pos_namesets) if pos_namesets else set()

    # --- public eligible pool: reproduce the embedder's FULL candidate cascade
    #     (single-row / non-clock / fanout<=max / STA slack / blockage /
    #     dense-tile / width bucket).  Only co-row PAIR swaps are committed by
    #     the embedder across all benches (0 triples), so E_P is the pair pool.
    #     pos_cell_names lets the slack gate adapt so post-route drift never
    #     ejects a marked cell (which would itself become a side channel). -----
    pool = reconstruct_placement_pool_embedder(
        design, max_dx_dbu=max_dx_dbu, k=k_neigh,
        grid_nx=grid_nx, grid_ny=grid_ny, fanout_max=fmax,
        fanout_diff_max=fdiff, slack_threshold_ns=slack_ns,
        tile_density_max=tile_dmax, sdc_path=sdc_path,
        pos_cell_names=pos_cell_names)

    # --- tile-density grid ----------------------------------------------------
    bbox, tw, th = wc.make_tile_grid(block, *_grid_dims(block, wc.core_bbox(block)))
    nx = max(1, int(round((bbox[2] - bbox[0]) / tw))) if tw > 0 else 1
    ny = max(1, int(round((bbox[3] - bbox[1]) / th))) if th > 0 else 1
    cells_all = [c for c in wc.collect_movable_core_cells(block)
                 if not wc.is_filler_tap_endcap(c.getMaster())]
    density = wc.compute_tile_density(cells_all, bbox, nx, ny, tw, th)
    x0, y0, x1, y1 = bbox
    h_core = max(1.0, float(y1 - y0))
    w_core = max(1.0, float(x1 - x0))

    by_name = {c.getName(): c for c in block.getInsts()}

    def feat_for(kind, cells):
        xs = [wc.inst_bottom_left(c)[0] for c in cells]
        ys = [wc.inst_bottom_left(c)[1] for c in cells]
        sizes = [wc.inst_size(c) for c in cells]
        ws = [s[0] for s in sizes]
        hs = [s[1] for s in sizes]
        fos = [wc.fanout_of(c) for c in cells]
        tx, ty = wc.tile_of(cells[0], bbox, tw, th, nx, ny)
        dens = float(density.get((tx, ty), 0.0))
        n = len(cells)
        # NB: tuple_span (max(x)-min(x)) is intentionally *not* a feature.  For a
        # same-width pair it proxies the swap HPWL delta, i.e. the embedder's
        # HPWL-neutrality eligibility gate; using it as a feature without
        # reproducing that (expensive) gate in E_P lets the classifier separate
        # on eligibility rather than the key.  The remaining features are all
        # key-independent observables.
        return [
            dens,
            (sum(ys) / n - y0) / h_core,
            (sum(xs) / n - x0) / w_core,
            sum(ws) / n,
            sum(hs) / n,
            sum(w * h for w, h in sizes) / n,
            float(max(fos)),
            sum(fos) / n,
            float(max(fos) - min(fos)),
        ]

    rows_out = []
    seen_fs = set()
    for kind, cells in pool:
        names = [c.getName() for c in cells]      # x-order (matches mutator key)
        fs = frozenset(names)
        seen_fs.add(fs)
        oid = "|".join(names)
        label = 1 if fs in pos_namesets else 0
        rows_out.append((oid, label, feat_for(kind, list(cells))))

    # union: committed positives the post-embed reconstruction did not surface
    added = 0
    for fs in pos_namesets:
        if fs in seen_fs:
            continue
        names = pos_name_lists[fs]
        cells = [by_name[n] for n in names if n in by_name]
        if len(cells) < 2:
            continue
        # order by x so the object_id matches the mutator's pool key convention
        cells.sort(key=lambda c: wc.inst_bottom_left(c)[0])
        kind = "triple" if len(cells) == 3 else "pair"
        oid = "|".join(c.getName() for c in cells)
        rows_out.append((oid, 1, feat_for(kind, cells)))
        added += 1

    sys.stderr.write(
        f"[dump_p] pool={len(pool)} positives={len(pos_namesets)} "
        f"union_added={added} total_rows={len(rows_out)}\n")
    return PLACE_HEADER, rows_out


# ---------------------------------------------------------------------------
# CTS
# ---------------------------------------------------------------------------

CTS_HEADER = [
    # Strictly replay-free + carrier-free feature set.
    # - No parity_*  (the watermark carrier itself)
    # - No channel_* (the embedder's pure/quasi/cross classification)
    # - No mutable_sinks_* with dont_touch filter (embedder feasibility leak)
    # - No STA-derived slew/cap/latency (these proxy the embedder's electrical
    #   filter and would re-introduce eligibility leakage)
    # Per-LCB structural features only, plus pair-level geometry.
    "tot_fanout_a", "tot_fanout_b",
    "seq_fanout_a", "seq_fanout_b",
    "nonseq_fanout_a", "nonseq_fanout_b",
    "depth_a", "depth_b",
    "dist_dbu",
    "boundary_ff_a", "boundary_ff_b",
]

_CLKBUF_HINTS = ("CLKBUF", "CLKINV", "CLKGATE", "CTSBUF")
_REPAIR_NAME_HINTS = ("rebuffer", "wire", "hold", "max_cap", "max_slew",
                      "fanout", "load_slew", "clkload", "clk_load")
_CHANNEL_CODE = {"none": 0.0, "pure": 1.0, "quasi_leaf": 2.0}


def _is_lcb_inst(inst):
    try:
        mname = inst.getMaster().getName().upper()
    except Exception:
        return False
    return any(h in mname for h in _CLKBUF_HINTS)


def _dont_touch(inst):
    try:
        if inst.getDoNotTouch():
            return True
    except Exception:
        pass
    try:
        name = inst.getName().lower()
    except Exception:
        return False
    return any(h in name for h in _REPAIR_NAME_HINTS)


def _count_mutable_sinks(cc, lcb):
    """Sinks on the LCB output net that the CTS mutator could legally move
    (parent not dont_touch and not itself an LCB).  A public, observable proxy
    for 'has a movable boundary FF' -- the property the embedder selects on."""
    try:
        net = cc._inst_single_output_net(lcb)
    except Exception:
        net = None
    if net is None:
        return 0
    cnt = 0
    try:
        for it in net.getITerms():
            if it.getIoType() != "INPUT":
                continue
            inst = it.getInst()
            if inst is None or _dont_touch(inst) or _is_lcb_inst(inst):
                continue
            cnt += 1
    except Exception:
        pass
    return cnt


def dump_cts(design):
    import cts_watermark_common as cc  # type: ignore
    block = design.getBlock()

    # Defaults mirror run_cts_wm.sh (the all-stage embed path): sibling<=50um,
    # R_MAX=5.  Using the embed-time parameters keeps the reconstructed E_C
    # faithful so positives are not pushed outside the proximity threshold.
    sib_um = float(os.environ.get("WM_CTS_SIBLING_DIST_UM", "50"))
    r_max = int(os.environ.get("WM_CTS_RMAX", "5"))
    upm = block.getDbUnitsPerMicron()
    max_dist_dbu = float(sib_um) * upm

    # --- public eligible pool: mirror the embedder (pure ∪ quasi ∪ mixed) -----
    classified = cc.classify_lcbs(block, r_max=r_max)
    pure = classified["pure"]
    quasi = classified["quasi_leaf"]
    pairs = []
    pairs += cc.build_proximity_pairs(pure, max_dist_dbu)
    pairs += cc.build_proximity_pairs(quasi, max_dist_dbu)
    # cross-channel (pure × quasi) proximity pairs
    for p in sorted(pure, key=lambda c: c.getName()):
        cp = cc.inst_center(p)
        for q in sorted(quasi, key=lambda c: c.getName()):
            if cc.manhattan(cp, cc.inst_center(q)) > max_dist_dbu:
                continue
            na, nb = p.getName(), q.getName()
            pk = f"{na}+{nb}" if na < nb else f"{nb}+{na}"
            pairs.append((pk, p, q) if na < nb else (pk, q, p))

    chan = {}
    for c in pure:
        chan[c.getName()] = "pure"
    for c in quasi:
        chan[c.getName()] = "quasi_leaf"

    # --- committed positives from embed CSV -----------------------------------
    embed_csv = Path(os.environ["WM_EMBED_CSV"])
    pos_keys = set()
    pos_lcbs = {}     # pair_key -> (name_a, name_b)
    if embed_csv.exists():
        for r in csv.DictReader(open(embed_csv)):
            skip = str(r.get("skipped_reason", "")).strip()
            if skip not in ("", "ok", "already_satisfied"):
                continue
            la = (r.get("L_A") or "").strip()
            lb = (r.get("L_B") or "").strip()
            pk = (r.get("pair_key") or "").strip() or (f"{la}+{lb}" if la and lb else "")
            if not pk:
                continue
            pos_keys.add(pk)
            if la and lb:
                pos_lcbs[pk] = (la, lb)

    by_name = {}
    for inst in block.getInsts():
        if _is_lcb_inst(inst):
            by_name[inst.getName()] = inst

    # Clock-tree depth: number of buffer hops from this LCB up to a non-LCB
    # parent (root or sequential).  Bounded to avoid pathological loops.
    _DEPTH_CAP = 64

    def _lcb_depth(lcb):
        d = 0
        cur = lcb
        while d < _DEPTH_CAP:
            parent = cc.lcb_parent(cur)
            if parent is None or not _is_lcb_inst(parent):
                return d
            cur = parent
            d += 1
        return d

    # "Nearby boundary FFs" without the embedder's dont_touch filter: count of
    # sequential sinks on ``lcb_a``'s output net whose Manhattan distance to
    # ``lcb_b``'s centroid is less than the public sibling-distance window.
    # Uses only public geometry; no carrier, no internal feasibility check.
    def _nearby_boundary_ff(la, lb):
        try:
            net = cc.lcb_output_net(la)
        except Exception:
            return 0
        if net is None:
            return 0
        cb_xy = cc.inst_center(lb)
        cnt = 0
        try:
            for it in cc.lcb_fanout_ff_iterms(la):
                inst = it.getInst()
                if inst is None:
                    continue
                if cc.manhattan(cc.inst_center(inst), cb_xy) <= max_dist_dbu:
                    cnt += 1
        except Exception:
            pass
        return cnt

    def feat_for(la_inst, lb_inst):
        sa = int(cc.lcb_sink_breakdown(la_inst)["seq"])
        sb = int(cc.lcb_sink_breakdown(lb_inst)["seq"])
        try:
            ta = cc.lcb_fanout(la_inst)
        except Exception:
            ta = sa
        try:
            tb = cc.lcb_fanout(lb_inst)
        except Exception:
            tb = sb
        ca = cc.inst_center(la_inst)
        cb = cc.inst_center(lb_inst)
        dist = cc.manhattan(ca, cb)
        da = _lcb_depth(la_inst)
        db = _lcb_depth(lb_inst)
        ba = _nearby_boundary_ff(la_inst, lb_inst)
        bb = _nearby_boundary_ff(lb_inst, la_inst)
        return [
            float(ta), float(tb),
            float(sa), float(sb),
            float(ta - sa), float(tb - sb),
            float(da), float(db),
            float(dist),
            float(ba), float(bb),
        ]

    # Replay-free + carrier-free run: no _feasible filter (which would have
    # leaked the embedder's boundary-FF availability check) -- every pair in
    # the reconstructed E_C survives to the classifier.
    rows_out = []
    seen = set()
    dropped_infeasible = 0
    for pk, la, lb in pairs:
        seen.add(pk)
        feats = feat_for(la, lb)
        is_pos = pk in pos_keys
        rows_out.append((pk, 1 if is_pos else 0, feats))

    added = 0
    for pk in pos_keys:
        if pk in seen:
            continue
        names = pos_lcbs.get(pk)
        if not names:
            continue
        la_inst = by_name.get(names[0])
        lb_inst = by_name.get(names[1])
        if la_inst is None or lb_inst is None:
            continue
        rows_out.append((pk, 1, feat_for(la_inst, lb_inst)))
        added += 1

    sys.stderr.write(
        f"[dump_c] pool={len(pairs)} positives={len(pos_keys)} "
        f"dropped_infeasible={dropped_infeasible} union_added={added} "
        f"total_rows={len(rows_out)}\n")
    return CTS_HEADER, rows_out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    stage = os.environ["STAGE"]
    in_odb = os.environ["WM_ODB"]
    out_csv = Path(os.environ["WM_OUT_FEATURES"])

    tech = ord_.Tech()
    design = ord_.Design(tech)
    design.readDb(in_odb)
    block = design.getBlock()

    if stage == "placement":
        header, rows = dump_placement(design)
    elif stage == "cts":
        header, rows = dump_cts(design)
    else:
        sys.stderr.write(f"[dump] unknown stage {stage}\n")
        return 2

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["object_id", "label"] + header)
        for oid, label, feats in rows:
            wr.writerow([oid, label] + [f"{v:.6g}" for v in feats])
    n_pos = sum(1 for _, l, _ in rows if l == 1)
    sys.stderr.write(
        f"[dump] stage={stage} wrote {len(rows)} rows ({n_pos} pos) -> {out_csv}\n")
    return 0


sys.exit(main())
