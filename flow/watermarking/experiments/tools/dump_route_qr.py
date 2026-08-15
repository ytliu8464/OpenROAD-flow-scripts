# SPDX-License-Identifier: BSD-3-Clause
"""OpenROAD-python script: dump per-net *canonical* wrong-way / total planar
wirelength (paper Eq. eq:routing_net_fraction) plus observable geometric
features used by the targeted-attack classifier.

Wirelength is used rather than per-net *segment counts*, which are
representation-dependent (splitting/merging collinear route records changes
them).  Here, for each signal net we canonicalize the routed geometry by
merging overlapping/contiguous collinear wire intervals on the same routing
layer, then measure the maximal intervals once.

Definitions (paper Sec. Routing Watermarking / Carrier):
  * A planar wire interval is *wrong-way* if its orientation is orthogonal to
    the preferred routing direction of its layer.
  * l_ww(n)  = canonical wrong-way planar wirelength of net n
  * l_tot(n) = canonical total planar wirelength of net n
  * q_R(n)   = l_ww(n) / l_tot(n)
  * Vias are excluded (no routing direction).  Nets with l_tot(n)=0 are kept in
    the CSV with tot_len=0 so the pure-Python filter can drop them uniformly.

Run with:
    openroad -python -exit dump_route_qr.py
(usually via tools/dump_route_qr.sh, which wraps the singularity invocation)

Required env vars:
    WM_ODB          .odb to read
    WM_QR_CSV       output CSV path

Output columns:
    net, ww_len, tot_len, degree, vias, bbox_w, bbox_h, <L_<layer> ...>
  lengths are integer DBU; L_<layer> is that layer's canonical total planar
  wirelength (the layer-usage distribution feature).
"""

import csv
import os
import sys

import odb  # type: ignore


def _routing_layers(tech):
    """Return {layer_name: is_horizontal_pref} for ROUTING layers, plus order."""
    pref = {}
    order = []
    for layer in tech.getLayers():
        if str(layer.getType()) != "ROUTING":
            continue
        name = layer.getName()
        pref[name] = (str(layer.getDirection()) == "HORIZONTAL")
        order.append(name)
    return pref, order


def _net_is_signal(net):
    if net.isSpecial():
        return False
    sig = str(net.getSigType())
    return sig not in ("POWER", "GROUND", "CLOCK")


def _merge_len(intervals):
    """Total length of the union of a list of [lo, hi] integer intervals."""
    if not intervals:
        return 0
    intervals.sort()
    total = 0
    clo, chi = intervals[0]
    for lo, hi in intervals[1:]:
        if lo > chi:            # disjoint (touching endpoints still merge: lo==chi)
            total += chi - clo
            clo, chi = lo, hi
        elif hi > chi:
            chi = hi
    total += chi - clo
    return total


def _canonical_net(net, pref):
    """Return (l_ww, l_tot, per_layer_tot, n_vias) for one net.

    Segments are bucketed by (layer, orientation, fixed-coordinate line) and the
    per-bucket union length is summed.  A horizontal segment (dy==0) is
    wrong-way on a vertical-preferred layer; a vertical segment (dx==0) is
    wrong-way on a horizontal-preferred layer.
    """
    wire = net.getWire()
    n_vias = 0
    # buckets[(layer, is_horiz_seg, fixed)] -> list of [lo, hi]
    buckets = {}
    diag = 0
    xmin = ymin = xmax = ymax = None
    if wire is not None:
        dec = odb.dbWireDecoder()
        dec.begin(wire)
        END = odb.dbWireDecoder.END_DECODE
        POINT = odb.dbWireDecoder.POINT
        POINT_EXT = odb.dbWireDecoder.POINT_EXT
        PATH = odb.dbWireDecoder.PATH
        SHORT = odb.dbWireDecoder.SHORT
        JUNCTION = odb.dbWireDecoder.JUNCTION
        VWIRE = odb.dbWireDecoder.VWIRE
        VIA = odb.dbWireDecoder.VIA
        TECH_VIA = odb.dbWireDecoder.TECH_VIA
        prev_x = prev_y = None
        cur_layer = None
        op = dec.next()
        while op != END:
            if op in (PATH, SHORT, JUNCTION, VWIRE):
                layer = dec.getLayer()
                cur_layer = layer.getName() if layer is not None else None
                prev_x = prev_y = None
            elif op in (POINT, POINT_EXT):
                if op == POINT:
                    x, y = dec.getPoint()
                else:
                    x, y, _ext = dec.getPoint_ext()
                if prev_x is not None and cur_layer in pref:
                    dx = x - prev_x
                    dy = y - prev_y
                    adx, ady = abs(dx), abs(dy)
                    if adx > 0 and ady == 0:
                        lo, hi = (x, prev_x) if dx < 0 else (prev_x, x)
                        buckets.setdefault((cur_layer, True, prev_y), []).append([lo, hi])
                    elif ady > 0 and adx == 0:
                        lo, hi = (y, prev_y) if dy < 0 else (prev_y, y)
                        buckets.setdefault((cur_layer, False, prev_x), []).append([lo, hi])
                    elif adx > 0 and ady > 0:
                        diag += 1  # Manhattan router: should not occur
                if cur_layer in pref:
                    if xmin is None or x < xmin:
                        xmin = x
                    if xmax is None or x > xmax:
                        xmax = x
                    if ymin is None or y < ymin:
                        ymin = y
                    if ymax is None or y > ymax:
                        ymax = y
                prev_x, prev_y = x, y
            elif op in (VIA, TECH_VIA):
                n_vias += 1
                layer = dec.getLayer()   # decoder stays on the wire's current layer
                cur_layer = layer.getName() if layer is not None else cur_layer
            op = dec.next()

    l_ww = 0
    l_tot = 0
    per_layer = {}
    for (layer, is_horiz_seg, _fixed), ivals in buckets.items():
        seg_len = _merge_len(ivals)
        l_tot += seg_len
        per_layer[layer] = per_layer.get(layer, 0) + seg_len
        # wrong-way iff segment orientation != layer preferred orientation
        if is_horiz_seg != pref[layer]:
            l_ww += seg_len
    bw = (xmax - xmin) if xmin is not None else 0
    bh = (ymax - ymin) if ymin is not None else 0
    return l_ww, l_tot, per_layer, n_vias, diag, bw, bh


def main():
    odb_path = os.environ.get("WM_ODB")
    out_csv = os.environ.get("WM_QR_CSV") or os.environ.get("WM_COUNTS_CSV")
    if not odb_path or not out_csv:
        sys.stderr.write("WM_ODB and WM_QR_CSV must be set\n")
        return 2

    db = odb.dbDatabase.create()
    odb.read_db(db, odb_path)
    chip = db.getChip()
    block = chip.getBlock()
    tech = db.getTech()
    pref, layer_order = _routing_layers(tech)

    rows = []
    diag_total = 0
    for net in block.getNets():
        if not _net_is_signal(net):
            continue
        l_ww, l_tot, per_layer, n_vias, diag, bw, bh = _canonical_net(net, pref)
        diag_total += diag
        rows.append((net.getName(), l_ww, l_tot, net.getTermCount(),
                     n_vias, bw, bh, per_layer))

    header = ["net", "ww_len", "tot_len", "degree", "vias", "bbox_w", "bbox_h"]
    header += [f"L_{name}" for name in layer_order]
    with open(out_csv, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(header)
        for name, l_ww, l_tot, deg, vias, bw, bh, per_layer in rows:
            wr.writerow([name, l_ww, l_tot, deg, vias, bw, bh]
                        + [per_layer.get(n, 0) for n in layer_order])
    sys.stderr.write(
        f"[dump_route_qr] {len(rows)} nets -> {out_csv}"
        f" (diag_segments={diag_total})\n")
    return 0


sys.exit(main())
