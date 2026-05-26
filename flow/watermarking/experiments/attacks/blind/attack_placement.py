# SPDX-License-Identifier: BSD-3-Clause
"""Blind placement attacker (paper §7.1, openroad-python).

Reconstructs the *public eligible* co-row cell tuple set from the placed ODB
(no key required), randomly picks fraction q_s of those tuples, and:

  * 2-tuples: swaps the X-origins of cells A and B (preserves rows; flips bit)
  * 3-tuples: applies a random non-identity permutation of (A, B, C)

After all swaps/permutes, the design is legalized with the same
maximum-displacement limit used in the owner flow
(``dpl.detailedPlacement`` with ``WM_MAX_DISP_X`` / ``WM_MAX_DISP_Y`` env
vars in microns, default 5).

Env:
    WM_ODB        input ODB (placed)
    WM_OUT_ODB    output ODB
    ATK_QS        fraction (e.g. 0.10) -- ignored when WM_TUPLES_ATTACK is set
    ATK_SEED      attacker PRNG seed (default 1)
    WM_PAIR_DIST_UM   max horizontal pair separation in microns (default 1)
    WM_NEIGHBOR_K     bounded-K neighbor enumeration (default 2)
    WM_MAX_DISP_X     legalizer max-displacement-x in microns (default 5)
    WM_MAX_DISP_Y     legalizer max-displacement-y in rows    (default 5)
    WM_INCLUDE_TRIPLES  "1" to include 3-tuples (default 1)
    WM_TUPLES_ATTACK    optional path to a text file with one tuple key per
                        line in the form "A_name|B_name" (pair) or
                        "A_name|B_name|C_name" (triple).  When set, only the
                        listed tuples are perturbed (in file order); ATK_QS is
                        ignored.  Used by the targeted §7.2 attack to deliver
                        a classifier-ranked top-K subset.
"""

import itertools
import os
import random
import sys
from pathlib import Path

# Hook into both the watermark-common helpers and the eligibility module.
_HERE = Path(__file__).resolve()
_WM_ROOT = _HERE.parents[3]                                # .../flow/watermarking
_EXP_DIR = _WM_ROOT / "experiments"
sys.path.insert(0, str(_WM_ROOT / "place_ordering"))
sys.path.insert(0, str(_EXP_DIR))
sys.path.insert(0, str(_EXP_DIR / "lib"))

import odb  # type: ignore

from lib.eligibility import reconstruct_placement_pool       # noqa: E402
import watermark_common as wc                                # type: ignore  # noqa: E402


# Mirror the embedder: it uses inst.setLocation(x, y) (which writes into the
# bottom-left after orientation), so we do the same.
def _swap_pair_x(a, b):
    """Swap the X coordinates of A and B; keep Y unchanged."""
    ax, ay = a.getLocation()
    bx, by = b.getLocation()
    a.setLocation(int(bx), int(ay))
    b.setLocation(int(ax), int(by))


def _apply_permutation_x(cells, perm_indices):
    """Reassign cell locations so cells[i] takes the X of cells[perm_indices[i]].

    Y coordinates are left untouched.  ``perm_indices`` is a permutation of
    ``range(len(cells))``.
    """
    original_xs = [c.getLocation()[0] for c in cells]
    original_ys = [c.getLocation()[1] for c in cells]
    for i, c in enumerate(cells):
        new_x = original_xs[perm_indices[i]]
        c.setLocation(int(new_x), int(original_ys[i]))


def _random_nonidentity_perm_3(rng: random.Random):
    """Pick a random non-identity permutation of (0, 1, 2)."""
    perms = [p for p in itertools.permutations(range(3)) if p != (0, 1, 2)]
    return list(rng.choice(perms))


def _legalize(design, max_disp_um_x: float, max_disp_um_y_rows: float) -> bool:
    """Run incremental detailed_placement with the same caps as the owner flow.

    Mirrors the pattern at watermark_embed.py:998-1011.  Returns True on
    success, False if the legalizer raised (the caller still writes the
    perturbed ODB so verification can run on the un-legalized layout).
    """
    rows = list(design.getBlock().getRows())
    if not rows:
        return True
    sw = rows[0].getSite().getWidth()
    sh = rows[0].getSite().getHeight()
    max_disp_x_sites = max(1, int(design.micronToDBU(max_disp_um_x) / sw))
    max_disp_y_rows  = max(1, int(max_disp_um_y_rows))   # already in rows
    dpl = design.getOpendp()
    sys.stderr.write(
        f"[atk_p] running detailedPlacement max_disp_x={max_disp_x_sites} sites "
        f"max_disp_y={max_disp_y_rows} rows\n"
    )
    try:
        dpl.detailedPlacement(max_disp_x_sites, max_disp_y_rows, "", True)
    except Exception as e:
        sys.stderr.write(f"[atk_p] detailedPlacement raised: {e}\n")
        return False
    try:
        dpl.checkPlacement(False)
    except Exception as e:
        sys.stderr.write(f"[atk_p] checkPlacement warning: {e}\n")
    return True


def _lock_cells(cells, status_locked) -> None:
    """Pin cells in place so the legalizer cannot revert the perturbation.

    Without this, the embedder's pre-filtered swaps survive legalization (each
    swap is HPWL-neutral by construction), but the attacker's blind swaps are
    often HPWL-bad and the legalizer happily snaps them back.  Locking after
    the swap forces the legalizer to either keep the perturbed location or
    propagate the displacement to neighbouring cells.
    """
    for c in cells:
        try:
            c.setPlacementStatus(status_locked)
        except Exception as e:
            sys.stderr.write(f"[atk_p] could not lock {c.getName()}: {e}\n")


def main() -> int:
    in_odb  = os.environ["WM_ODB"]
    out_odb = os.environ["WM_OUT_ODB"]
    q_s     = float(os.environ.get("ATK_QS",  "0.05"))
    seed    = int(  os.environ.get("ATK_SEED", "1"))
    pair_um = float(os.environ.get("WM_PAIR_DIST_UM", "1"))
    k_neigh = int(  os.environ.get("WM_NEIGHBOR_K",   "2"))
    mdx_um  = float(os.environ.get("WM_MAX_DISP_X",   "5"))
    mdy_row = float(os.environ.get("WM_MAX_DISP_Y",   "5"))
    incl_t  = os.environ.get("WM_INCLUDE_TRIPLES", "1") != "0"

    # Load the ODB into the active OpenROAD design so resizer/dpl APIs work.
    import openroad as ord_                                  # type: ignore
    tech = ord_.Tech()
    design = ord_.Design(tech)
    design.readDb(in_odb)
    block = design.getBlock()
    rng = random.Random(seed)

    max_dx_dbu = design.micronToDBU(pair_um)

    pool = reconstruct_placement_pool(block,
                                       max_dx_dbu=max_dx_dbu,
                                       k=k_neigh,
                                       include_triples=incl_t)
    if not pool:
        sys.stderr.write("[atk_p] eligible pool is empty; writing input ODB unchanged\n")
        design.writeDb(out_odb)
        return 0

    # Targeted-attack selective mode: when WM_TUPLES_ATTACK points at a key
    # list, perturb exactly those tuples (file order) and ignore q_s / random
    # shuffle.  Each tuple key is "A_name|B_name" or "A_name|B_name|C_name".
    tuples_attack = os.environ.get("WM_TUPLES_ATTACK", "").strip()
    if tuples_attack:
        try:
            with open(tuples_attack) as f:
                wanted_keys = [ln.strip() for ln in f if ln.strip()]
        except OSError as e:
            sys.stderr.write(f"[atk_p] cannot read WM_TUPLES_ATTACK={tuples_attack}: {e}\n")
            wanted_keys = []
        by_key = {}
        for kind, cells in pool:
            key = "|".join(c.getName() for c in cells)
            by_key.setdefault(key, (kind, cells))
        selected = [by_key[k] for k in wanted_keys if k in by_key]
        n_missing = sum(1 for k in wanted_keys if k not in by_key)
        sys.stderr.write(
            f"[atk_p] selective mode: wanted={len(wanted_keys)} matched={len(selected)} "
            f"missing={n_missing} pool={len(pool)}\n"
        )
        pool = selected
        n_target = len(pool)
    else:
        n_target = int(round(q_s * len(pool)))
        rng.shuffle(pool)
    perturbed_pairs = perturbed_triples = skipped = 0
    touched = set()
    perturbed_insts: list = []

    for kind, cells in pool[:n_target]:
        # Skip if any of the cells has already been moved by an earlier
        # tuple in this batch -- the X swap would conflict.  This mirrors
        # the embedder's per-cell ownership rule and keeps the legalizer
        # solvable.
        names = tuple(c.getName() for c in cells)
        if any(n in touched for n in names):
            skipped += 1
            continue
        try:
            if kind == "pair":
                _swap_pair_x(cells[0], cells[1])
                perturbed_pairs += 1
            else:
                perm = _random_nonidentity_perm_3(rng)
                _apply_permutation_x(list(cells), perm)
                perturbed_triples += 1
            touched.update(names)
            perturbed_insts.extend(cells)
        except Exception as e:
            sys.stderr.write(f"[atk_p] swap/perm failed for {names}: {e}\n")
            skipped += 1

    # Lock every perturbed cell so the legalizer cannot undo the swap (see
    # _lock_cells docstring).  setPlacementStatus("LOCKED") leaves the cell
    # in-place during detailedPlacement.
    try:
        locked_status = odb.dbPlacementStatus.LOCKED
    except Exception:
        locked_status = "LOCKED"
    try:
        placed_status = odb.dbPlacementStatus.PLACED
    except Exception:
        placed_status = "PLACED"
    _lock_cells(perturbed_insts, locked_status)

    legal_ok = _legalize(design, mdx_um, mdy_row)

    # Unlock the perturbed cells before writing the ODB.  The lock was
    # purely an artifact to keep the swap durable through *this* legalizer
    # call; leaving cells LOCKED on disk would propagate into the downstream
    # PPA continuation (CTS / route), where another check_placement runs
    # and would refuse to resolve any residual overlaps because all the
    # offending cells are locked.  By unlocking we let downstream tools
    # re-legalize freely.  The verifier downstream measures r_P from the
    # cells' X coordinates, not from PlacementStatus, so its value is
    # unaffected by this unlock.
    _lock_cells(perturbed_insts, placed_status)

    # Always write the perturbed ODB -- even if the legalizer aborted, the
    # un-legalized layout still has the swap and the verifier downstream can
    # still measure r_P on it.
    try:
        design.writeDb(out_odb)
    except Exception as e:
        sys.stderr.write(f"[atk_p] writeDb failed: {e}\n")
        return 1
    sys.stderr.write(
        f"[atk_p] pool={len(pool)} target={n_target} "
        f"perturbed_pairs={perturbed_pairs} perturbed_triples={perturbed_triples} "
        f"skipped={skipped} legalize_ok={int(legal_ok)} "
        f"q_s={q_s} seed={seed}\n"
    )
    return 0


sys.exit(main())
