# SPDX-License-Identifier: BSD-3-Clause
"""Blind CTS attacker (openroad -python).

For q_s fraction of *leaf clock buffers* (LCBs), randomly remove or duplicate
one of the fanout sinks (re-attach to the other LCB at random distance).  This
perturbs the fanout-parity signal used by the CTS watermark.
"""

import os
import random
import sys

import odb  # type: ignore


_CLKBUF_HINTS = ("CLKBUF", "CLKINV", "CLKGATE", "CTSBUF")


def is_lcb(inst):
    try:
        mname = inst.getMaster().getName().upper()
    except Exception:
        return False
    return any(h in mname for h in _CLKBUF_HINTS)


def main():
    in_odb = os.environ["WM_ODB"]
    out_odb = os.environ["WM_OUT_ODB"]
    q_s = float(os.environ.get("ATK_QS", "0.05"))
    seed = int(os.environ.get("ATK_SEED", "1"))

    db = odb.dbDatabase.create()
    odb.read_db(db, in_odb)
    block = db.getChip().getBlock()
    rng = random.Random(seed)

    lcbs = [i for i in block.getInsts() if is_lcb(i)]
    if not lcbs:
        odb.write_db(db, out_odb)
        return 0
    n_target = max(1, int(round(q_s * len(lcbs))))
    rng.shuffle(lcbs)
    perturbed = 0
    for lcb in lcbs[:n_target]:
        # Walk the buffer's output pin's net's iterms (sinks).
        out_pin = None
        for it in lcb.getITerms():
            if str(it.getIoType()) == "OUTPUT":
                out_pin = it
                break
        if out_pin is None:
            continue
        net = out_pin.getNet()
        if net is None:
            continue
        sinks = [it for it in net.getITerms()
                 if str(it.getIoType()) == "INPUT"]
        if len(sinks) < 2:
            continue
        # Drop a random sink and reattach it to another random LCB's output net.
        victim = rng.choice(sinks)
        # Find another LCB with an output net.
        other = rng.choice([l for l in lcbs if l is not lcb])
        other_out = None
        for it in other.getITerms():
            if str(it.getIoType()) == "OUTPUT":
                other_out = it
                break
        if other_out is None or other_out.getNet() is None:
            continue
        victim.disconnect()
        victim.connect(other_out.getNet())
        perturbed += 1
    odb.write_db(db, out_odb)
    sys.stderr.write(f"[atk_c] moved {perturbed} sinks among {len(lcbs)} LCBs "
                     f"(q_s={q_s})\n")
    return 0


sys.exit(main())
