#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""OpenROAD-python worker for the baseline attack-robustness study.

Two modes (env MODE), parameterized by baseline (env BL):

  dump   -- reconstruct the baseline's public eligible object set from the
            leaked ODB, label the key-selected watermark objects positive
            (from the embed CSV) and the rest negative, and emit observable
            features.  Feeds attacks/targeted/classify.py (reused verbatim).

  mutate -- perturb a set of eligible objects (a random fraction ATK_QS for the
            blind attack, or the explicit WM_OBJECTS_ATTACK list for the
            targeted attack) and write the perturbed ODB:
              * placement carriers (cell_scattering / kahng / icmarks /
                automarks): displace each selected cell by (+1 site, +1 row) and
                re-legalize -- this flips column-, row- and axis-parity at once,
                so it disturbs every positional-parity baseline with one
                operator while pinning the moved cell so the legalizer cannot
                snap the parity back.
              * buffer carrier (buffer_insertion): insert one buffer on each
                selected net (reusing the baseline's own _insert_buffer),
                flipping the BUF-count parity.

The eligible-set and buffer-insertion logic is imported from each baseline's
embed module so the reconstruction is byte-faithful to the embedder.

Env:
  BL                 cell_scattering | kahng | icmarks | automarks | buffer_insertion
  MODE               dump | mutate
  WM_ODB             leaked baseline ODB (3_place_<m>.odb / 4_cts_bufins.odb)
  WM_EMBED_CSV       baseline embed CSV (positive-label / committed source)
  WM_OUT_FEATURES    (dump) output features CSV
  WM_OUT_ODB         (mutate) output ODB
  ATK_QS             (mutate, blind) fraction of eligible objects to perturb
  ATK_SEED           (mutate, blind) PRNG seed (default 1)
  WM_OBJECTS_ATTACK  (mutate, targeted) file with one object id per line
  PLATFORM           platform (for buffer master selection)
"""
import csv
import os
import random
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_BASELINES = _HERE.parents[1]                 # .../experiments/baselines
_EXP = _HERE.parents[2]                        # .../experiments
for p in (str(_BASELINES), str(_EXP)):
    if p not in sys.path:
        sys.path.insert(0, p)

import openroad as ord_  # type: ignore

CARRIER = {
    "cell_scattering": "place", "kahng": "place",
    "icmarks": "place", "automarks": "place",
    "buffer_insertion": "buffer",
}
ID_COL = {"buffer_insertion": "net"}           # default "inst"


def _embed_mod(bl):
    import importlib
    return importlib.import_module(f"{bl}.embed")


def _marked_ids(embed_csv, bl):
    col = ID_COL.get(bl, "inst")
    out = set()
    with open(embed_csv) as f:
        for r in csv.DictReader(f):
            if str(r.get("satisfied", "True")).strip().lower() in ("true", "1"):
                v = (r.get(col) or "").strip()
                if v:
                    out.add(v)
    return out


def _fanout(inst):
    tot = 0
    for it in inst.getITerms():
        try:
            if not it.isOutputSignal():
                continue
        except Exception:
            continue
        net = it.getNet()
        if net is None:
            continue
        tot = max(tot, max(0, net.getITermCount() - 1))
    return tot


# ---------------------------------------------------------------------------
# Eligible-set reconstruction
# ---------------------------------------------------------------------------

def _eligible_cells(be, block):
    return [c for c in block.getInsts() if be._is_eligible(c)]


def _eligible_nets(be, block):
    return [n for n in block.getNets() if be._is_eligible_net(n)]


def _site_row(block):
    rows = list(block.getRows())
    sw = rows[0].getSite().getWidth()
    sh = rows[0].getSite().getHeight()
    return sw, sh


# ---------------------------------------------------------------------------
# DUMP
# ---------------------------------------------------------------------------

PLACE_HEADER = ["tile_density", "x_norm", "y_norm",
                "cell_w_dbu", "cell_h_dbu", "area_dbu2", "fanout"]
BUF_HEADER = ["n_buf", "fanout", "degree", "bbox_hpwl_dbu"]


def _count_buf(net):
    c = 0
    for it in net.getITerms():
        inst = it.getInst()
        if inst is None:
            continue
        if "BUF" in inst.getMaster().getName().upper():
            c += 1
    return c


def _remove_one_buffer(block, net):
    """De-buffer: drop one BUF instance that sits on `net` (input iterm on the
    net -> driver..->net->buf->Nout->sinks), reconnecting Nout's sinks directly
    to `net`.  Decrements the net's BUF count by one (flips count parity).
    Returns True on success.  This is the natural attack on a buffer-count
    watermark and, unlike a second insertion, reliably flips parity regardless
    of the net's existing buffering."""
    buf_it = None
    for it in net.getITerms():
        inst = it.getInst()
        if inst is None:
            continue
        if "BUF" not in inst.getMaster().getName().upper():
            continue
        if str(it.getIoType()).upper() != "INPUT":
            continue
        buf_it = it
        break
    if buf_it is None:
        return False
    buf = buf_it.getInst()
    out_net = None
    for it in buf.getITerms():
        if str(it.getIoType()).upper() == "OUTPUT":
            out_net = it.getNet()
            break
    import odb  # type: ignore
    try:
        # move the buffer's downstream sinks back onto `net`
        if out_net is not None:
            for sink in list(out_net.getITerms()):
                if sink.getInst() is buf:
                    continue
                if str(sink.getIoType()).upper() != "INPUT":
                    continue
                sink.disconnect()
                sink.connect(net)
        for it in buf.getITerms():
            it.disconnect()
        # Destroy the now-floating buffer instance and its emptied output net.
        # Leaving an unconnected instance / dangling net behind breaks the
        # downstream legalizer and global router (DPL-0038 / GRT-0116).
        try:
            odb.dbInst.destroy(buf)
        except Exception:
            pass
        if out_net is not None:
            try:
                odb.dbNet.destroy(out_net)
            except Exception:
                pass
        return True
    except Exception as e:
        sys.stderr.write(f"[bl_mutate] debuffer failed on {net.getName()}: {e}\n")
        return False


def dump(bl, block, out_csv):
    be = _embed_mod(bl)
    marked = _marked_ids(os.environ["WM_EMBED_CSV"], bl)
    rows = []
    if CARRIER[bl] == "place":
        ca = block.getCoreArea()
        x0, y0, x1, y1 = ca.xMin(), ca.yMin(), ca.xMax(), ca.yMax()
        W = max(1.0, float(x1 - x0)); H = max(1.0, float(y1 - y0))
        sw, sh = _site_row(block)
        # coarse tile density over a 24x24 grid
        nx = ny = 24
        tw = W / nx; th = H / ny
        cells = _eligible_cells(be, block)
        dens = {}
        for c in cells:
            bb = c.getBBox()
            tx = min(nx - 1, max(0, int((bb.xMin() - x0) / tw)))
            ty = min(ny - 1, max(0, int((bb.yMin() - y0) / th)))
            dens[(tx, ty)] = dens.get((tx, ty), 0) + 1
        for c in cells:
            bb = c.getBBox()
            tx = min(nx - 1, max(0, int((bb.xMin() - x0) / tw)))
            ty = min(ny - 1, max(0, int((bb.yMin() - y0) / th)))
            m = c.getMaster()
            w, h = m.getWidth(), m.getHeight()
            rows.append((c.getName(), 1 if c.getName() in marked else 0, [
                float(dens.get((tx, ty), 0)),
                (bb.xMin() - x0) / W, (bb.yMin() - y0) / H,
                float(w), float(h), float(w * h), float(_fanout(c)),
            ]))
        header = PLACE_HEADER
    else:
        nets = _eligible_nets(be, block)
        for n in nets:
            fan = sum(1 for it in n.getITerms()
                      if str(it.getIoType()).upper() == "INPUT")
            deg = n.getITermCount()
            bb = n.getTermBBox() if hasattr(n, "getTermBBox") else None
            hpwl = 0.0
            try:
                r = n.getTermBBox()
                hpwl = float((r.xMax() - r.xMin()) + (r.yMax() - r.yMin()))
            except Exception:
                hpwl = 0.0
            rows.append((n.getName(), 1 if n.getName() in marked else 0, [
                float(_count_buf(n)), float(fan), float(deg), hpwl,
            ]))
        header = BUF_HEADER
    Path(out_csv).parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["object_id", "label"] + header)
        for oid, lab, feats in rows:
            wr.writerow([oid, lab] + ["%.6g" % v for v in feats])
    npos = sum(1 for _, l, _ in rows if l == 1)
    sys.stderr.write(f"[bl_dump {bl}] eligible={len(rows)} marked={npos} -> {out_csv}\n")


# ---------------------------------------------------------------------------
# MUTATE
# ---------------------------------------------------------------------------

def _select(ids, marked_pool):
    """Return the object ids to perturb: explicit list (targeted) or a random
    fraction (blind)."""
    sel_file = os.environ.get("WM_OBJECTS_ATTACK", "").strip()
    if sel_file:
        try:
            wanted = [ln.strip() for ln in open(sel_file) if ln.strip()]
        except OSError:
            wanted = []
        idset = set(ids)
        return [w for w in wanted if w in idset]
    q = float(os.environ.get("ATK_QS", "0.1"))
    rng = random.Random(int(os.environ.get("ATK_SEED", "1")))
    pool = list(ids)
    rng.shuffle(pool)
    return pool[:int(round(q * len(pool)))]


def _legalize(design):
    rows = list(design.getBlock().getRows())
    if not rows:
        return
    sw = rows[0].getSite().getWidth()
    dpl = design.getOpendp()
    try:
        dpl.detailedPlacement(max(1, int(5 * 2000 / sw)), 5, "", True)
    except Exception as e:
        sys.stderr.write(f"[bl_mutate] detailedPlacement raised: {e}\n")


def mutate(bl, design, out_odb):
    import odb  # type: ignore
    be = _embed_mod(bl)
    block = design.getBlock()
    if CARRIER[bl] == "place":
        cells = _eligible_cells(be, block)
        by_name = {c.getName(): c for c in cells}
        chosen = _select(list(by_name.keys()), None)
        sw, sh = _site_row(block)
        ca = block.getCoreArea()
        moved = []
        for nm in chosen:
            c = by_name.get(nm)
            if c is None:
                continue
            x, y = c.getOrigin()
            nx = x + sw if (x + 2 * sw) <= ca.xMax() else x - sw
            ny = y + sh if (y + 2 * sh) <= ca.yMax() else y - sh
            try:
                c.setOrigin(int(nx), int(ny))
                moved.append(c)
            except Exception as e:
                sys.stderr.write(f"[bl_mutate] setOrigin failed {nm}: {e}\n")
        try:
            locked = odb.dbPlacementStatus.LOCKED
            placed = odb.dbPlacementStatus.PLACED
        except Exception:
            locked = placed = None
        if locked is not None:
            for c in moved:
                try: c.setPlacementStatus(locked)
                except Exception: pass
        _legalize(design)
        if placed is not None:
            for c in moved:
                try: c.setPlacementStatus(placed)
                except Exception: pass
        design.writeDb(out_odb)
        sys.stderr.write(f"[bl_mutate {bl}] perturbed={len(moved)}/{len(chosen)} -> {out_odb}\n")
    else:
        nets = _eligible_nets(be, block)
        by_name = {n.getName(): n for n in nets}
        chosen = _select(list(by_name.keys()), None)
        plat = os.environ.get("PLATFORM", "nangate45")
        buf_master_name = be._buf_master_name(plat)
        buf_master = None
        for m in block.getDb().getLibs():
            mm = m.findMaster(buf_master_name)
            if mm is not None:
                buf_master = mm; break
        n_flip = 0
        for i, nm in enumerate(chosen):
            n = by_name.get(nm)
            if n is None:
                continue
            # Toggle BUF-count parity: remove a buffer if the net has one
            # (de-buffering -- reliable parity flip), else insert one.
            if _count_buf(n) >= 1:
                if _remove_one_buffer(block, n):
                    n_flip += 1
            elif buf_master is not None:
                try:
                    be._insert_buffer(block, n, buf_master, f"atk_buf_{i}")
                    n_flip += 1
                except Exception as e:
                    sys.stderr.write(f"[bl_mutate] insert failed {nm}: {e}\n")
        design.writeDb(out_odb)
        sys.stderr.write(f"[bl_mutate {bl}] flipped={n_flip}/{len(chosen)} -> {out_odb}\n")


def main():
    bl = os.environ["BL"]
    mode = os.environ["MODE"]
    tech = ord_.Tech()
    design = ord_.Design(tech)
    design.readDb(os.environ["WM_ODB"])
    block = design.getBlock()
    if mode == "dump":
        dump(bl, block, os.environ["WM_OUT_FEATURES"])
    elif mode == "mutate":
        mutate(bl, design, os.environ["WM_OUT_ODB"])
    else:
        sys.stderr.write(f"unknown MODE {mode}\n"); return 2
    return 0


sys.exit(main())
