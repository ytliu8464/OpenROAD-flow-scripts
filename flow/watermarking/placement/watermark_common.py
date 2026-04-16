# SPDX-License-Identifier: BSD-3-Clause
"""Shared helpers for row-parity placement watermarking (Kahng et al.)."""

from __future__ import annotations

import hashlib
import hmac
import math
import random
from typing import List, Sequence, Tuple

import odb


def argv_after_openroad_driver() -> List[str]:
    """Return argv slice suitable for argparse after ``openroad -python -exit script.py``."""
    import sys

    skip = {
        "-python",
        "-exit",
        "-no_splash",
        "-no_init",
        "-no_settings",
        "-gui",
        "-minimize",
    }
    raw = sys.argv[1:]
    i = 0
    while i < len(raw):
        a = raw[i]
        if a in skip:
            i += 1
            continue
        if a.startswith("-threads") and i + 1 < len(raw):
            i += 2
            continue
        break
    if i < len(raw) and raw[i].endswith(".py"):
        i += 1
    return raw[i:]


def sorted_row_bottoms(block: odb.dbBlock) -> List[int]:
    rows = list(block.getRows())
    if not rows:
        raise RuntimeError("No rows in block")
    bottoms = sorted({r.getBBox().yMin() for r in rows})
    return bottoms


def infer_row_pitch(row_bottoms: Sequence[int], site_height: int) -> int:
    if len(row_bottoms) >= 2:
        d = row_bottoms[1] - row_bottoms[0]
        if d > 0:
            return d
    return site_height


def watermark_selection(
    key: str,
    message: str,
    cells: Sequence[odb.dbInst],
    num_constraints: int,
) -> Tuple[List[odb.dbInst], List[int], random.Random]:
    """Return (selected_cells, parity_per_cell, rng) where parity 0=EVEN row, 1=ODD."""
    digest = hmac.new(
        key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).digest()
    seed = int.from_bytes(digest[:8], "big")
    rng = random.Random(seed)
    if num_constraints > len(cells):
        raise ValueError(
            f"num_constraints={num_constraints} exceeds movable core cells={len(cells)}"
        )
    chosen = rng.sample(list(cells), num_constraints)
    parities = [rng.randint(0, 1) for _ in chosen]
    return chosen, parities, rng


def binomial_pc(num_constraints: int, num_failures: int, p: float = 0.5) -> float:
    """P(at most ``num_failures`` unsatisfied constraints) under i.i.d. success prob ``p``."""
    # Paper: Pc = sum_{i=0}^{x} C(X, i) p^{X-i} (1-p)^i  where x = # not satisfied, p = P(satisfied by chance).
    x = num_failures
    X = num_constraints
    total = 0.0
    for i in range(0, x + 1):
        total += math.comb(X, i) * (p ** (X - i)) * ((1.0 - p) ** i)
    return total


def collect_movable_core_cells(block: odb.dbBlock) -> List[odb.dbInst]:
    out: List[odb.dbInst] = []
    for inst in block.getInsts():
        if not inst.isCore():
            continue
        if inst.isFixed():
            continue
        if not inst.isPlaced():
            continue
        out.append(inst)
    out.sort(key=lambda c: c.getName())
    return out


def inst_size(inst: odb.dbInst) -> Tuple[int, int]:
    m = inst.getMaster()
    return m.getWidth(), m.getHeight()


def inst_bottom_left(inst: odb.dbInst) -> Tuple[int, int]:
    bb = inst.getBBox()
    return bb.xMin(), bb.yMin()
