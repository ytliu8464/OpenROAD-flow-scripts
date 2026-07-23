#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Buffer-insertion baseline (Sun et al. ISQED'06) -- embed phase.

Algorithm
---------
1. Load 4_cts.odb (post-CTS; buffer counts on signal nets are now stable).
2. K = capacity_for(platform, design, variant).
3. Eligible nets: signal nets (not power/ground, not clock), fanout >= 1.
4. Select K nets: HMAC-SHA256(seed_routing, b"bufins\\0" + net_name) -> u32;
   keep top-K by ascending score (same convention as PDMarks routing selection).
5. Per net: count instances whose master name starts with "BUF" (case-insensitive)
   that drive or are on this net.  Target bit = HMAC(..., b"bufins\\0bit\\0" + net)[0] & 1.
   If (count & 1) != target_bit, insert one extra buffer of the platform's default:
     NG45 -> BUF_X1
     ASAP7 -> BUFx2_ASAP7_75t_R
6. Buffer insertion in DB:
   a. Create new dbInst with BUF master.
   b. Split net: original net retains driver + new buffer input; new net carries
      original sinks + new buffer output.
   c. Place buffer at midpoint of net's driver bbox; run detailed_placement.
7. Write buffer_insertion_embed.csv + 4_cts_bufins.odb.

Run as:
  openroad -python -exit embed.py \\
      --odb /path/to/4_cts.odb \\
      --seed-hex /path/to/seed_routing.hex \\
      --platform nangate45 --design aes --variant watermarking-test1 \\
      [--k INT]
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))          # baselines/
sys.path.insert(0, str(_HERE.parent.parent))   # experiments/

from _common import (  # noqa: E402
    capacity_for,
    load_seed_hex,
    select_top_k,
    target_bit,
    pc_stage,
    FLOW_HOME,
)

_T0 = time.time()

# Monotonic counter for flat, SPEF-safe inserted instance/net names.
_BUF_IDX = 0


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')} +{time.time()-_T0:6.1f}s] [bufins:embed] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Platform-specific buffer master name
# ---------------------------------------------------------------------------

_PLATFORM_BUF: dict[str, str] = {
    "nangate45":  "BUF_X1",
    "asap7":      "BUFx2_ASAP7_75t_R",
}


def _buf_master_name(platform: str) -> str:
    return _PLATFORM_BUF.get(platform, "BUF_X1")


# ---------------------------------------------------------------------------
# ODB helpers
# ---------------------------------------------------------------------------

# This OpenROAD build's odb does not export the dbSigType / dbIoType enums to
# Python (`from odb import dbSigType` raises ImportError, same as
# dbPlacementStatus).  The getSigType()/getIoType() accessors return objects
# whose str() yields "CLOCK"/"POWER"/"OUTPUT"/... so we compare as strings,
# matching the working PDMarks code in cts_v2/place_ordering.

def _is_clock_net(net) -> bool:
    """True if net carries a clock signal (SigType == CLOCK)."""
    return str(net.getSigType()).upper() == "CLOCK"


def _is_special(net) -> bool:
    # POWER and GROUND (and tie nets) are "special"
    return str(net.getSigType()).upper() in ("POWER", "GROUND", "TIEHI", "TIELO")


def _is_eligible_net(net) -> bool:
    if net.isSpecial():
        return False
    if _is_special(net):
        return False
    if _is_clock_net(net):
        return False
    # Need at least one driver (output ITerms)
    drivers = [it for it in net.getITerms()
               if str(it.getIoType()).upper() == "OUTPUT"]
    return len(drivers) >= 1


def _count_buf_instances(net) -> int:
    """Count instances whose master name starts with BUF on this net."""
    count = 0
    for iterm in net.getITerms():
        mname = iterm.getInst().getMaster().getName().upper()
        if mname.startswith("BUF"):
            count += 1
    return count


def _net_driver_bbox(net):
    """Return (cx, cy) centroid of the driver cell's bbox."""
    for iterm in net.getITerms():
        if str(iterm.getIoType()).upper() == "OUTPUT":
            inst = iterm.getInst()
            bb = inst.getBBox()
            return (bb.xMin() + bb.xMax()) // 2, (bb.yMin() + bb.yMax()) // 2
    return None


def _snap_to_site(x: int, y: int, block) -> tuple[int, int]:
    """Snap (x,y) to nearest row origin (site-aligned)."""
    best_y = None
    best_dist = 10 ** 18
    for row in block.getRows():
        ry = row.getBBox().yMin()
        d = abs(ry - y)
        if d < best_dist:
            best_dist = d
            best_y = ry
    if best_y is None:
        best_y = y
    # snap x to site width
    site_w = 1
    for row in block.getRows():
        s = row.getSite()
        if s:
            site_w = s.getWidth()
            break
    rx_min = block.getCoreArea().xMin()
    col = (x - rx_min) // site_w
    snapped_x = rx_min + col * site_w
    return snapped_x, best_y


def _insert_buffer(block, net, buf_master, inst_name_prefix: str):
    """Insert a buffer on `net` in the ODB, splitting the net.

    Returns (new_buf_inst, new_net) or raises on failure.
    """
    import odb

    # Locate driver ITerms
    driver_iterms = [it for it in net.getITerms()
                     if str(it.getIoType()).upper() == "OUTPUT"]
    if not driver_iterms:
        raise RuntimeError(f"net {net.getName()} has no driver ITerms")

    # Create new instance.  Use a flat, counter-based name: deriving the name
    # from the (hierarchical) net name injects '.'/'[' into an INSTANCE name,
    # which OpenROAD's RCX then writes as a malformed SPEF -> STA-1670 syntax
    # error in the finish report (no WNS/TNS/power produced).  verify.py finds
    # buffers by master prefix on the original net, so the name is free to change.
    global _BUF_IDX
    _BUF_IDX += 1
    unique_id = f"{inst_name_prefix}_buf{_BUF_IDX}"
    new_inst = odb.dbInst.create(block, buf_master, unique_id)

    # Find buf input/output pins
    buf_in_mterm = None
    buf_out_mterm = None
    for mterm in buf_master.getMTerms():
        io = str(mterm.getIoType()).upper()
        if io == "INPUT":
            buf_in_mterm = mterm
        elif io == "OUTPUT":
            buf_out_mterm = mterm
    if buf_in_mterm is None or buf_out_mterm is None:
        raise RuntimeError(f"cannot find input/output pins of {buf_master.getName()}")

    # New sink net (carries original sinks).  Flat counter-based name for the
    # same SPEF-safety reason as the instance above.
    new_net_name = f"{inst_name_prefix}_net{_BUF_IDX}"
    new_net = odb.dbNet.create(block, new_net_name)

    # Disconnect all sink ITERMs from original net and reconnect to new net
    sink_iterms = [it for it in list(net.getITerms())
                   if str(it.getIoType()).upper() != "OUTPUT"]
    for it in sink_iterms:
        it.disconnect()
        it.connect(new_net)

    # Buffer input on original net (driver side), output on new net (sink side)
    buf_in_iterm = new_inst.findITerm(buf_in_mterm.getName())
    buf_out_iterm = new_inst.findITerm(buf_out_mterm.getName())
    buf_in_iterm.connect(net)
    buf_out_iterm.connect(new_net)

    # Place buffer near driver
    cx, cy = _net_driver_bbox(net) or (
        block.getCoreArea().xMin(),
        block.getCoreArea().yMin(),
    )
    sx, sy = _snap_to_site(cx, cy, block)
    new_inst.setOrigin(sx, sy)
    new_inst.setPlacementStatus("PLACED")

    return new_inst, new_net


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _parse_args(argv: List[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Buffer-insertion baseline embedder")
    ap.add_argument("--odb",       required=True)
    ap.add_argument("--out-odb",   default="")
    ap.add_argument("--seed-hex",  required=True, help="path to seed_routing.hex")
    ap.add_argument("--platform",  required=True)
    ap.add_argument("--design",    required=True)
    ap.add_argument("--variant",   required=True)
    ap.add_argument("--k",         type=int, default=0)
    ap.add_argument("--out-csv",   default="")
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

    # Locate buffer master.  In this OpenROAD build masters live in dbLib
    # objects (dbTech has no findMaster); scan every lib.
    db = block.getDataBase()

    def _find_master(nm):
        for lib in db.getLibs():
            m = lib.findMaster(nm)
            if m is not None:
                return m
        return None

    buf_mname = _buf_master_name(args.platform)
    buf_master = _find_master(buf_mname)
    if buf_master is None:
        # Fall back to the first BUF-prefixed master available in any lib.
        _log(f"WARNING: master {buf_mname!r} not found; scanning for a BUF* master")
        for lib in db.getLibs():
            for m in lib.getMasters():
                if m.getName().upper().startswith("BUF"):
                    buf_master = m
                    break
            if buf_master is not None:
                break
    if buf_master is None:
        raise RuntimeError(f"buffer master {buf_mname!r} not found in design DB")
    _log(f"buffer master: {buf_master.getName()}")

    # Collect eligible nets
    eligible: List[str] = []
    net_by_name: dict[str, object] = {}
    for net in block.getNets():
        if _is_eligible_net(net):
            eligible.append(net.getName())
            net_by_name[net.getName()] = net

    _log(f"eligible signal nets: {len(eligible)}")
    if len(eligible) < K:
        _log(f"WARNING: only {len(eligible)} eligible nets; reducing K to {len(eligible)}")
        K = len(eligible)

    selected = select_top_k(seed, b"bufins", eligible, K)
    _log(f"selected {len(selected)} nets")

    # Embed
    DOMAIN_BIT = b"bufins"
    rows: List[dict] = []
    miss = 0
    inserted = 0

    for net_name in sorted(selected):
        net = net_by_name.get(net_name)
        if net is None:
            miss += 1
            rows.append({"net": net_name, "target_bit": -1,
                         "n_buf_before": -1, "n_buf_after": -1,
                         "satisfied": False, "note": "net_not_found"})
            continue

        tbit = target_bit(seed, DOMAIN_BIT, net_name)
        n_before = _count_buf_instances(net)
        cur_parity = n_before & 1

        satisfied = False
        n_after = n_before
        note = ""

        if cur_parity == tbit:
            satisfied = True
        else:
            # Insert one buffer to flip parity
            try:
                new_inst, new_net = _insert_buffer(block, net, buf_master, "wm_bufins")
                n_after = n_before + 1
                satisfied = True
                inserted += 1
            except Exception as e:
                note = f"insert_failed:{e}"
                miss += 1

        rows.append({
            "net": net_name,
            "target_bit": tbit,
            "n_buf_before": n_before,
            "n_buf_after": n_after,
            "satisfied": satisfied,
            "note": note,
        })

    _log(f"inserted {inserted} buffers  satisfied={K-miss}/{K}  miss={miss}")

    # Legalize
    _log("running detailed_placement to legalize inserted buffers ...")
    try:
        design.getOpendp().detailedPlacement(0, 0, "", False)
    except Exception as e:
        _log(f"WARNING: detailed_placement raised: {e}")

    # Re-count after legalization
    missed_post = 0
    for row in rows:
        if not row["satisfied"]:
            missed_post += 1
            continue
        net = net_by_name.get(row["net"])
        if net is None:
            missed_post += 1
            row["satisfied"] = False
            continue
        n_post = _count_buf_instances(net)
        row["n_buf_after"] = n_post
        if (n_post & 1) != row["target_bit"]:
            row["satisfied"] = False
            missed_post += 1

    x = K - missed_post
    pc = pc_stage(K, K - x, 0.5)
    _log(f"post-legalization: accepted={x}/{K}  Pc={pc:.2e}")

    # Write CSV
    out_dir = Path(args.odb).parent
    csv_path = args.out_csv or str(out_dir / "buffer_insertion_embed.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["net", "target_bit", "n_buf_before", "n_buf_after", "satisfied", "note"])
        w.writeheader()
        w.writerows(rows)
    _log(f"embed CSV -> {csv_path}")

    # Write ODB
    odb_out = args.out_odb or str(out_dir / "4_cts_bufins.odb")
    design.writeDb(odb_out)
    _log(f"watermarked ODB -> {odb_out}")

    print(f"BUFINS_EMBED: K={K} accepted={x} Pc={pc:.4e}", flush=True)


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    main(argv)
