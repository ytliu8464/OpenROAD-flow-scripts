# SPDX-License-Identifier: BSD-3-Clause
"""Blind CTS attacker (paper §7.1, openroad-python).

Reconstructs the *public* eligible LCB-pair set via
``build_proximity_pairs(collect_lcbs(block), WM_CTS_SIBLING_DIST_UM)``,
randomly picks fraction q_s of those pairs, and for each (L_A, L_B):

    - finds one sequential sink driven by L_A whose master is mutable
      (no LCB / dont_touch / clock buffer / repair buffer)
    - disconnects that sink from L_A's output net
    - reconnects it to L_B's output net

This perturbs the L_A / L_B fanout-parity carriers used by the watermark
verifier.  Reverts the move if it leaves any sink iterm pointing nowhere.

Env:
    WM_ODB         input ODB (4_cts_wm.odb)
    WM_OUT_ODB     output ODB
    ATK_QS         fraction (e.g. 0.10) -- ignored when WM_PAIRS_ATTACK is set
    ATK_SEED       attacker PRNG seed (default 1)
    WM_CTS_SIBLING_DIST_UM  proximity threshold in microns (default 20)
    WM_PAIRS_ATTACK  optional path to a text file with one LCB pair_key per
                     line (the embedder's "L_A+L_B" string).  When set, only
                     the listed pairs are perturbed (in file order); ATK_QS
                     is ignored.  Used by the targeted §7.2 attack to deliver
                     a classifier-ranked top-K subset.
"""

import os
import random
import sys
from pathlib import Path

# Repair-buffer name fragments that ORFS marks dont_touch.  Disconnecting
# a sink whose parent matches one of these calls dbITerm::disconnect()
# inside C++ which abort()s via std::unexpected on dont_touch instances.
_REPAIR_NAME_HINTS = (
    "rebuffer", "wire", "hold", "max_cap", "max_slew", "fanout",
    "load_slew", "clkload", "clk_load",
)
# Clock-buffer-instance master-name fragments (so we can recognize LCBs).
_CLKBUF_HINTS = ("CLKBUF", "CLKINV", "CLKGATE", "CTSBUF")


# Wire the eligibility module + cts_v2 helpers onto sys.path.
_HERE = Path(__file__).resolve()
_WM_ROOT = _HERE.parents[3]
_EXP_DIR = _WM_ROOT / "experiments"
sys.path.insert(0, str(_WM_ROOT / "cts_v2"))
sys.path.insert(0, str(_EXP_DIR))
sys.path.insert(0, str(_EXP_DIR / "lib"))

import odb  # type: ignore


def _is_lcb(inst) -> bool:
    try:
        mname = inst.getMaster().getName().upper()
    except Exception:
        return False
    return any(h in mname for h in _CLKBUF_HINTS)


def _name_is_repair_buffer(inst) -> bool:
    try:
        name = inst.getName().lower()
    except Exception:
        return False
    return any(h in name for h in _REPAIR_NAME_HINTS)


def _dont_touch(inst) -> bool:
    try:
        if inst.getDoNotTouch():
            return True
    except Exception:
        pass
    return _name_is_repair_buffer(inst)


def _output_net_of_lcb(lcb):
    for it in lcb.getITerms():
        try:
            if str(it.getIoType()) == "OUTPUT":
                return it.getNet()
        except Exception:
            continue
    return None


def _mutable_sinks(net):
    """Return iterms whose parent instance is safe to disconnect/connect.

    Skips dont_touch instances and other LCBs (intermediate clock-tree nodes
    are themselves dont_touch and unsafe to mutate)."""
    out = []
    if net is None:
        return out
    for it in net.getITerms():
        if str(it.getIoType()) != "INPUT":
            continue
        inst = it.getInst()
        if inst is None:
            continue
        if _dont_touch(inst):
            continue
        if _is_lcb(inst):
            continue
        out.append(it)
    return out


def main() -> int:
    in_odb  = os.environ["WM_ODB"]
    out_odb = os.environ["WM_OUT_ODB"]
    q_s     = float(os.environ.get("ATK_QS",  "0.10"))
    seed    = int(  os.environ.get("ATK_SEED", "1"))
    sib_um  = float(os.environ.get("WM_CTS_SIBLING_DIST_UM", "20"))

    db = odb.dbDatabase.create()
    odb.read_db(db, in_odb)
    block = db.getChip().getBlock()
    rng = random.Random(seed)

    # Reconstruct the public LCB-pair pool.  We import lazily so that any
    # import failure in cts_watermark_common (which pulls in heavy modules)
    # is reported clearly.
    from lib.eligibility import reconstruct_cts_pool        # noqa: E402

    # Convert sibling-um to DBU using the block's DEF/LEF units.  Default
    # to UNITS=2000 if unavailable.
    try:
        units = block.getDbUnitsPerMicron()
    except Exception:
        units = 2000
    max_dist_dbu = float(sib_um) * units

    pool = reconstruct_cts_pool(block, max_dist_dbu=max_dist_dbu)
    if not pool:
        sys.stderr.write("[atk_c] eligible LCB-pair pool is empty; writing input ODB unchanged\n")
        odb.write_db(db, out_odb)
        return 0

    # Targeted-attack selective mode: when WM_PAIRS_ATTACK points at a key
    # list, perturb exactly those pairs (file order) and ignore q_s / random
    # shuffle.  Each line is a pair_key as built by build_proximity_pairs
    # ("L_A_name+L_B_name").
    pairs_attack = os.environ.get("WM_PAIRS_ATTACK", "").strip()
    if pairs_attack:
        try:
            with open(pairs_attack) as f:
                wanted_keys = [ln.strip() for ln in f if ln.strip()]
        except OSError as e:
            sys.stderr.write(f"[atk_c] cannot read WM_PAIRS_ATTACK={pairs_attack}: {e}\n")
            wanted_keys = []
        by_key = {pk: (pk, la, lb) for pk, la, lb in pool}
        selected = [by_key[k] for k in wanted_keys if k in by_key]
        n_missing = sum(1 for k in wanted_keys if k not in by_key)
        sys.stderr.write(
            f"[atk_c] selective mode: wanted={len(wanted_keys)} matched={len(selected)} "
            f"missing={n_missing} pool={len(pool)}\n"
        )
        pool = selected
        n_target = len(pool)
    else:
        n_target = int(round(q_s * len(pool)))
        rng.shuffle(pool)

    perturbed = skipped_no_sink = skipped_err = 0
    used_lcbs: set = set()

    for pair_key, L_A, L_B in pool[:n_target]:
        # Avoid touching the same LCB twice in one batch.
        if L_A.getName() in used_lcbs or L_B.getName() in used_lcbs:
            skipped_no_sink += 1
            continue

        # Pick a mutable sink on L_A's output net and reconnect it to L_B.
        if rng.random() < 0.5:
            src, dst = L_A, L_B
        else:
            src, dst = L_B, L_A
        src_net = _output_net_of_lcb(src)
        dst_net = _output_net_of_lcb(dst)
        if src_net is None or dst_net is None:
            skipped_no_sink += 1
            continue
        sinks = _mutable_sinks(src_net)
        if not sinks:
            skipped_no_sink += 1
            continue
        victim = rng.choice(sinks)
        try:
            victim.disconnect()
            victim.connect(dst_net)
            perturbed += 1
            used_lcbs.add(L_A.getName())
            used_lcbs.add(L_B.getName())
        except Exception:
            # Defensive: any residual ODB veto -> skip and move on.
            skipped_err += 1

    odb.write_db(db, out_odb)
    sys.stderr.write(
        f"[atk_c] pool={len(pool)} target={n_target} perturbed={perturbed} "
        f"skipped_no_sink={skipped_no_sink} skipped_err={skipped_err} "
        f"q_s={q_s} seed={seed}\n"
    )
    return 0


sys.exit(main())
