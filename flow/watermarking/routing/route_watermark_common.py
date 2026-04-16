# SPDX-License-Identifier: BSD-3-Clause
"""Shared helpers for spacing-only NDR routing watermarking."""

from __future__ import annotations

import hashlib
import hmac
import math
import random
from typing import List, Optional, Sequence, Tuple

import odb

WM_NDR_NAME_DEFAULT = "wm_spacing_ndr"
TARGET_LAYERS_DEFAULT = ("metal2", "metal3")


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


def binomial_pc(num_constraints: int, num_failures: int, p: float = 0.5) -> float:
    """P(at most ``num_failures`` unsatisfied constraints) under i.i.d. success prob ``p``."""
    x = num_failures
    X = num_constraints
    total = 0.0
    for i in range(0, x + 1):
        total += math.comb(X, i) * (p ** (X - i)) * ((1.0 - p) ** i)
    return total


def collect_signal_nets(block: odb.dbBlock) -> List[odb.dbNet]:
    """Routable signal nets: not special, SIGNAL type, at least two instance pins."""
    out: List[odb.dbNet] = []
    for net in block.getNets():
        if net.isSpecial():
            continue
        if net.getSigType() != "SIGNAL":
            continue
        iterms = list(net.getITerms())
        if len(iterms) < 2:
            continue
        out.append(net)
    out.sort(key=lambda n: n.getName())
    return out


def watermark_net_selection(
    key: str,
    message: str,
    nets: Sequence[odb.dbNet],
    num_nets: int,
) -> Tuple[List[odb.dbNet], random.Random]:
    """Deterministically select ``num_nets`` nets using HMAC-SHA256 PRNG seed."""
    digest = hmac.new(
        key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).digest()
    seed = int.from_bytes(digest[:8], "big")
    rng = random.Random(seed)
    if num_nets > len(nets):
        raise ValueError(
            f"num_nets={num_nets} exceeds eligible signal nets={len(nets)}"
        )
    chosen = rng.sample(list(nets), num_nets)
    return chosen, rng


def create_spacing_ndr(
    block: odb.dbBlock,
    tech: odb.dbTech,
    spacing_dbu: int,
    ndr_name: str,
    target_layers: Sequence[str],
) -> odb.dbTechNonDefaultRule:
    """Create a block-scoped NDR: default width on all routing layers; extra spacing on target layers."""
    if block.findNonDefaultRule(ndr_name):
        raise RuntimeError(
            f"Non-default rule {ndr_name!r} already exists; start from a clean post-CTS ODB"
        )
    ndr = odb.dbTechNonDefaultRule_create(block, ndr_name)
    if ndr is None:
        raise RuntimeError(f"dbTechNonDefaultRule_create failed for {ndr_name!r}")
    targets = set(target_layers)
    for layer in tech.getLayers():
        if layer.getType() != "ROUTING":
            continue
        rule = odb.dbTechLayerRule_create(ndr, layer)
        rule.setWidth(layer.getWidth())
        if layer.getName() in targets:
            rule.setSpacing(spacing_dbu)
        else:
            rule.setSpacing(layer.getSpacing())
    return ndr


def layer_spacing_from_ndr(
    ndr: odb.dbTechNonDefaultRule, layer_name: str, tech: odb.dbTech
) -> Optional[int]:
    """Return NDR spacing in DBU for ``layer_name``, or None if missing."""
    layer = tech.findLayer(layer_name)
    if layer is None:
        return None
    lr = ndr.getLayerRule(layer)
    if lr is None:
        return None
    return lr.getSpacing()


def wire_has_rule_opcode(net: odb.dbNet) -> bool:
    """True if encoded ``dbWire`` contains at least one RULE opcode."""
    wire = net.getWire()
    if wire is None:
        return False
    dec = odb.dbWireDecoder()
    dec.begin(wire)
    while True:
        op = dec.next()
        if op == odb.dbWireDecoder.END_DECODE:
            break
        if op == odb.dbWireDecoder.RULE:
            return True
    return False
