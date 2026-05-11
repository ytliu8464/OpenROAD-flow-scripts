# SPDX-License-Identifier: BSD-3-Clause
"""Master-key and stage-seed derivation utilities.

This mirrors flow/watermarking/gen_key/seed_common.py:

  master_seed   = SHA256(sig)
  seed_<stage>  = SHA256(master_seed || stage_label_bytes)

For wrong-key / attack experiments we want a deterministic, reproducible stream
of 32-byte "master keys" without going through Ed25519 signing.  We therefore
treat any 32-byte string as a "master seed" directly: this is interchangeable
with the SHA256(sig) value above for downstream HMAC derivation.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Dict


STAGE_LABELS = ("placement", "cts", "routing")


def derive_stage_seeds(master_seed: bytes) -> Dict[str, bytes]:
    """master_seed -> {placement, cts, routing} seeds.  Matches seed_common."""
    if len(master_seed) != 32:
        raise ValueError(f"master_seed must be 32 bytes, got {len(master_seed)}")
    return {
        label: hashlib.sha256(master_seed + label.encode("utf-8")).digest()
        for label in STAGE_LABELS
    }


def load_seed_hex(path: Path) -> bytes:
    raw = Path(path).read_text().strip().split()[0]
    b = bytes.fromhex(raw)
    if len(b) != 32:
        raise ValueError(f"expected 32-byte seed in {path}, got {len(b)}")
    return b


def wrong_key_stream(count: int, namespace: str = "pdmarks-wrong-key") -> list:
    """Return a deterministic list of `count` 32-byte master seeds.

    Each seed_i = HMAC-SHA256("pdmarks-wrong-key-stream", namespace || i).
    These are intentionally not derived from any real Ed25519 sig.
    """
    out = []
    key = b"pdmarks-wrong-key-stream"
    for i in range(count):
        msg = namespace.encode("utf-8") + b"\0" + str(i).encode("utf-8")
        out.append(hmac.new(key, msg, hashlib.sha256).digest())
    return out


def routing_select_net(seed_routing: bytes, net_name: str) -> float:
    """Reproduce the C++ Bernoulli selection u_R(n) in [0,1).

    u = first 4 bytes of HMAC-SHA256(seed_routing, b"net\0" || net_name)
    interpreted as little-endian uint32; returns u / 2^32.
    """
    d = hmac.new(seed_routing, b"net\0" + net_name.encode("utf-8"),
                 hashlib.sha256).digest()
    u = int.from_bytes(d[:4], "little", signed=False)
    return u / 4294967296.0


def routing_is_selected(seed_routing: bytes, net_name: str, fraction: float) -> bool:
    return routing_select_net(seed_routing, net_name) < fraction


__all__ = [
    "STAGE_LABELS",
    "derive_stage_seeds",
    "load_seed_hex",
    "wrong_key_stream",
    "routing_select_net",
    "routing_is_selected",
]
