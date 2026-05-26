# SPDX-License-Identifier: BSD-3-Clause
"""One-shot: convert LOCKED instances in an attacked placement ODB to PLACED.

The blind placement attack (attacks/blind/attack_placement.py) used to leave
its perturbed cells LOCKED on disk so its own internal legalizer call
honored the swaps.  That worked for the attack's r_P measurement but made
the back-end PPA continuation impossible: CTS's check_placement refuses to
resolve any residual overlaps when the offending cells are locked.

attack_placement.py has been patched to unlock before write_db, but ODBs
written by older revisions still carry LOCKED cells.  This script rewrites
those ODBs in place: every LOCKED instance becomes PLACED; everything else
(FIRM_PLACED, COVER, UNPLACED) is left alone.

Run via openroad -python:

    WM_ODB=/path/atk_p_*.odb \
    openroad -python -exit \
       experiments/tools/unlock_atk_placement_odb.py

When WM_OUT_ODB is unset, rewrites in place.
"""
from __future__ import annotations

import os
import sys

import odb  # type: ignore


def main() -> int:
    in_odb = os.environ.get("WM_ODB")
    if not in_odb:
        sys.stderr.write("WM_ODB must be set\n")
        return 2
    out_odb = os.environ.get("WM_OUT_ODB", in_odb)
    ref_odb = os.environ.get("WM_REF_ODB", "")

    # If a reference (un-attacked) ODB is provided, collect the names of
    # instances that the embedder/design legitimately LOCKED -- those stay
    # LOCKED.  Everything else that is LOCKED in the attacked ODB was added
    # by the attack and is safe to unlock.  Without the reference we fall
    # back to unlocking every LOCKED non-macro/pad instance.
    ref_locked: set = set()
    if ref_odb and os.path.isfile(ref_odb):
        ref_db = odb.dbDatabase.create()
        odb.read_db(ref_db, ref_odb)
        for i in ref_db.getChip().getBlock().getInsts():
            if str(i.getPlacementStatus()) == "LOCKED":
                ref_locked.add(i.getName())
        sys.stderr.write(
            f"[unlock] reference {ref_odb}: {len(ref_locked)} LOCKED insts kept\n"
        )

    db = odb.dbDatabase.create()
    odb.read_db(db, in_odb)
    block = db.getChip().getBlock()

    try:
        placed_status = odb.dbPlacementStatus.PLACED
    except Exception:
        placed_status = "PLACED"

    def _is_macro_or_pad(m) -> bool:
        try:
            t = str(m.getType()).upper()
        except Exception:
            return False
        return ("BLOCK" in t) or ("PAD" in t) or ("COVER" in t)

    unlocked = 0
    kept_locked = 0
    skipped = 0
    for inst in block.getInsts():
        if str(inst.getPlacementStatus()) != "LOCKED":
            continue
        if inst.getName() in ref_locked:
            kept_locked += 1
            continue
        master = inst.getMaster()
        if ref_locked == set() and master is not None and _is_macro_or_pad(master):
            # Fallback heuristic (no ref ODB given): preserve macros/pads.
            kept_locked += 1
            continue
        try:
            inst.setPlacementStatus(placed_status)
            unlocked += 1
        except Exception as e:
            sys.stderr.write(f"[unlock] {inst.getName()} failed: {e}\n")
            skipped += 1

    odb.write_db(db, out_odb)
    sys.stderr.write(
        f"[unlock] LOCKED -> PLACED: {unlocked}, "
        f"kept LOCKED (ref / macro / pad): {kept_locked}, "
        f"skipped {skipped} -> {out_odb}\n"
    )
    return 0


sys.exit(main())
