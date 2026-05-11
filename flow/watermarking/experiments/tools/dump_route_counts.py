# SPDX-License-Identifier: BSD-3-Clause
"""OpenROAD-python script: dump per-net (wrong_way, total) routed segments.

Run with:
    openroad -python -exit dump_route_counts.py

Required env vars:
    WM_ODB          .odb to read
    WM_COUNTS_CSV   output CSV path
"""

import csv
import os
import sys

import odb  # type: ignore


def _layer_horizontal_map(tech):
    out = {}
    for layer in tech.getLayers():
        if str(layer.getType()) != "ROUTING":
            continue
        out[layer.getName()] = (str(layer.getDirection()) == "HORIZONTAL")
    return out


def _net_is_signal(net):
    if net.isSpecial():
        return False
    sig = str(net.getSigType())
    return sig not in ("POWER", "GROUND", "CLOCK")


def main():
    odb_path = os.environ.get("WM_ODB")
    out_csv = os.environ.get("WM_COUNTS_CSV")
    if not odb_path or not out_csv:
        sys.stderr.write("WM_ODB and WM_COUNTS_CSV must be set\n")
        return 2

    db = odb.dbDatabase.create()
    odb.read_db(db, odb_path)
    chip = db.getChip()
    block = chip.getBlock()
    tech = db.getTech()
    horiz = _layer_horizontal_map(tech)

    rows = []
    for net in block.getNets():
        if not _net_is_signal(net):
            continue
        wire = net.getWire()
        total = 0
        wrong = 0
        if wire is not None:
            dec = odb.dbWireDecoder()
            dec.begin(wire)
            prev_x = prev_y = None
            cur_h = None  # bool: layer prefers horizontal
            op = dec.next()
            END = odb.dbWireDecoder.END_DECODE
            POINT = odb.dbWireDecoder.POINT
            POINT_EXT = odb.dbWireDecoder.POINT_EXT
            PATH = odb.dbWireDecoder.PATH
            SHORT = odb.dbWireDecoder.SHORT
            JUNCTION = odb.dbWireDecoder.JUNCTION
            VWIRE = odb.dbWireDecoder.VWIRE
            VIA = odb.dbWireDecoder.VIA
            TECH_VIA = odb.dbWireDecoder.TECH_VIA
            while op != END:
                if op == PATH or op == SHORT or op == JUNCTION or op == VWIRE:
                    layer = dec.getLayer()
                    cur_h = horiz.get(layer.getName())
                    prev_x = prev_y = None
                elif op == POINT or op == POINT_EXT:
                    if op == POINT:
                        x, y = dec.getPoint()
                    else:
                        x, y, _ext = dec.getPoint_ext()
                    if prev_x is not None and cur_h is not None:
                        dx = abs(x - prev_x)
                        dy = abs(y - prev_y)
                        if dx + dy > 0:
                            total += 1
                            long_h = dx >= dy
                            if long_h != cur_h:
                                wrong += 1
                    prev_x, prev_y = x, y
                elif op == VIA or op == TECH_VIA:
                    layer = dec.getLayer()
                    cur_h = horiz.get(layer.getName())
                op = dec.next()
        rows.append((net.getName(), wrong, total))

    with open(out_csv, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["net", "wrong_way", "total"])
        wr.writerows(rows)
    sys.stderr.write(f"[dump_route_counts] {len(rows)} rows -> {out_csv}\n")
    return 0


sys.exit(main())
