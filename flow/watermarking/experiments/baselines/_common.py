# SPDX-License-Identifier: BSD-3-Clause
"""Shared helpers for PDMarks baseline implementations.

Provides:
- capacity_for(platform, design, variant) -- returns K matched to PDMarks P-only
- hmac_select(seed, domain, name) -> u32  -- deterministic per-object score
- is_selected(seed, domain, name, K, N)   -- top-K selection by u32 score
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import os
import struct
from pathlib import Path
from typing import Optional

FLOW_HOME = Path(
    os.environ.get(
        "FLOW_HOME",
        "/home/fetzfs_projects/MISC-ytliu/watermarking/"
        "OR0415/OpenROAD-flow-scripts/flow",
    )
)

# Fallback K when no embed CSV exists for a design.  Chosen to give P_c ~ 1e-14,
# which is comparable to the ASAP7 AES case (K=45, all satisfied -> Pc=2^-45).
_DEFAULT_K: dict[str, int] = {
    "aes":           45,
    "jpeg":          129,
    "swerv_wrapper": 80,
    "ariane136":     128,   # no existing embed run; use paper default
    "bp_quad":       128,
}
_DEFAULT_K_FALLBACK = 64    # generic fallback for unknown designs


def capacity_for(platform: str, design: str, variant: str) -> int:
    """Return K = number of watermark objects to embed for the baseline.

    Priority:
    1. Env var BASELINE_K (integer override)
    2. accepted-row count from wm_place_order_embed_v2.csv
    3. accepted-row count from wm_cells_embed.csv (legacy format)
    4. Per-design default table (_DEFAULT_K)
    5. _DEFAULT_K_FALLBACK
    """
    # 1. Env override
    env_k = os.environ.get("BASELINE_K")
    if env_k:
        return int(env_k)

    rdir = FLOW_HOME / "results" / platform / design / variant

    # 2. New-format embed CSV (place_ordering)
    p = rdir / "wm_place_order_embed_v2.csv"
    if p.exists():
        try:
            k = sum(
                1 for r in csv.DictReader(open(p))
                if r.get("skipped_reason", "") in ("", "already_satisfied")
            )
            if k > 0:
                return k
        except Exception:
            pass

    # 3. Legacy format
    p2 = rdir / "wm_cells_embed.csv"
    if p2.exists():
        try:
            k = sum(1 for _ in csv.DictReader(open(p2)))
            if k > 0:
                return k
        except Exception:
            pass

    # 4. Per-design default
    if design in _DEFAULT_K:
        return _DEFAULT_K[design]

    return _DEFAULT_K_FALLBACK


# ---------------------------------------------------------------------------
# HMAC helpers
# ---------------------------------------------------------------------------

def hmac_u32(seed: bytes, domain: bytes, name: str) -> int:
    """Return a 32-bit score for (domain, name) keyed by seed.

    Uses HMAC-SHA256 the same way as the PDMarks place_ordering PRF, but with
    a different domain prefix so the baseline selections don't overlap.
    """
    msg = domain + b"\0" + name.encode("utf-8")
    d = hmac.new(seed, msg, hashlib.sha256).digest()
    return struct.unpack_from("<I", d, 0)[0]


def select_top_k(seed: bytes, domain: bytes, candidates: list[str], k: int) -> set[str]:
    """Return the K candidate names with the smallest HMAC u32 score.

    This gives a deterministic, key-dependent selection that the paper's
    adversary cannot reproduce without the seed.
    """
    scored = [(hmac_u32(seed, domain, c), c) for c in candidates]
    scored.sort()
    return {c for _, c in scored[:k]}


def target_bit(seed: bytes, domain: bytes, name: str) -> int:
    """Return the 1-bit watermark target for object 'name'."""
    msg = domain + b"\0bit\0" + name.encode("utf-8")
    d = hmac.new(seed, msg, hashlib.sha256).digest()
    return d[0] & 1


def load_seed_hex(path: Path) -> bytes:
    raw = Path(path).read_text().strip().split()[0]
    b = bytes.fromhex(raw)
    if len(b) != 32:
        raise ValueError(f"expected 32-byte seed in {path}, got {len(b)}")
    return b


def seeds_for(design: str, gen_key_dir: Optional[Path] = None) -> tuple[bytes, bytes]:
    """Return (seed_placement, seed_routing) for a design."""
    base = gen_key_dir or (FLOW_HOME / "watermarking" / "gen_key" / "out" / design)
    return (
        load_seed_hex(base / "seed_placement.hex"),
        load_seed_hex(base / "seed_routing.hex"),
    )


def pc_stage(big_x: int, small_x: int, p_s: float = 0.5) -> float:
    """Binomial tail P_c = sum_{i=0..x} C(X,i)(1-p)^i p^(X-i)."""
    import math
    if big_x <= 0:
        return 1.0
    q = 1.0 - p_s
    total = 0.0
    for i in range(min(small_x, big_x) + 1):
        total += math.comb(big_x, i) * (q ** i) * (p_s ** (big_x - i))
    return total


__all__ = [
    "FLOW_HOME",
    "capacity_for",
    "hmac_u32",
    "select_top_k",
    "target_bit",
    "load_seed_hex",
    "seeds_for",
    "pc_stage",
]
