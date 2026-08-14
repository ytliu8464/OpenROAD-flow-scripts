# SPDX-License-Identifier: BSD-3-Clause
"""The PDMarks keyed primitives, in one place.

Every stage (placement, CTS, routing) and every verifier -- including the
key-independent reimplementations under ``experiments/lib/`` -- derives its
object selection and target values from these functions.  Verification only
works if the embedder and the verifier agree byte-for-byte, so they must not be
reimplemented per module.

Contents:
    hmac_digest         length-prefixed HMAC-SHA256, the PRF for every stage
    load_seed_hex       read a 32-byte stage seed written by gen_key/
    derive_stage_seeds  master seed -> per-stage seeds (mirrors gen_key)
    binomial_pc         Bernoulli coincidence probability P_c
    argv_after_openroad_driver
                        argv slice after `openroad -python -exit script.py`

Length prefixing matters: ``hmac_digest(k, b"ab", b"c")`` and
``hmac_digest(k, b"a", b"bc")`` must differ, otherwise a cell named ``ab|c``
and one named ``a|bc`` would collide in the same domain.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import struct
import sys
from typing import Dict, List, Sequence, Union

STAGE_LABELS = ("placement", "cts", "routing")

# A stage seed is SHA-256 output, so exactly 32 bytes.
SEED_BYTES = 32


# ---------------------------------------------------------------------------
# PRF
# ---------------------------------------------------------------------------

def hmac_digest(seed: bytes, *parts: bytes) -> bytes:
    """Length-prefixed HMAC-SHA256 over ``parts``, keyed by ``seed``.

    Each part is preceded by its big-endian uint32 length, so the
    concatenation is unambiguous and distinct part tuples never collide.
    """
    h = hmac.new(seed, b"", hashlib.sha256)
    for p in parts:
        h.update(struct.pack(">I", len(p)))
        h.update(p)
    return h.digest()


# ---------------------------------------------------------------------------
# Seeds
# ---------------------------------------------------------------------------

def load_seed_hex(path: Union[str, "os.PathLike[str]"]) -> bytes:
    """Read a ``seed_<stage>.hex`` file from gen_key/ and return raw bytes.

    Raises rather than returning a short key: a truncated seed would silently
    produce a different -- and unverifiable -- watermark.
    """
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"seed file not found: {path!r}")
    fields = open(path).read().split()
    txt = fields[0] if fields else ""
    try:
        raw = bytes.fromhex(txt)
    except ValueError as e:
        raise ValueError(f"seed file {path} is not valid hex: {e}") from e
    if len(raw) != SEED_BYTES:
        raise ValueError(
            f"expected a {SEED_BYTES}-byte seed in {path}, got {len(raw)} bytes"
        )
    return raw


def derive_stage_seeds(master_seed: bytes) -> Dict[str, bytes]:
    """master_seed -> {placement, cts, routing}.  Mirrors gen_key/seed_common."""
    if len(master_seed) != SEED_BYTES:
        raise ValueError(
            f"master_seed must be {SEED_BYTES} bytes, got {len(master_seed)}"
        )
    return {
        label: hashlib.sha256(master_seed + label.encode("utf-8")).digest()
        for label in STAGE_LABELS
    }


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def binomial_pc(num_constraints: int, num_failures: int,
                p_success: float = 0.5) -> float:
    """P(at most ``num_failures`` unsatisfied) under i.i.d. P(success)=p_success.

    This is the coincidence probability P_c: the chance an unrelated layout
    matches at least ``num_constraints - num_failures`` of the claims.
    """
    total = 0.0
    for i in range(0, num_failures + 1):
        total += (math.comb(num_constraints, i)
                  * (p_success ** (num_constraints - i))
                  * ((1.0 - p_success) ** i))
    return total


# ---------------------------------------------------------------------------
# OpenROAD-Python argv
# ---------------------------------------------------------------------------

def argv_after_openroad_driver(argv: Sequence[str] = None) -> List[str]:
    """Return the argv slice after ``openroad -python -exit script.py``."""
    raw = list(argv if argv is not None else sys.argv[1:])
    skip = {"-python", "-exit", "-no_splash", "-no_init", "-no_settings",
            "-gui", "-minimize"}
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


__all__ = [
    "STAGE_LABELS", "SEED_BYTES",
    "hmac_digest", "load_seed_hex", "derive_stage_seeds",
    "binomial_pc", "argv_after_openroad_driver",
]
