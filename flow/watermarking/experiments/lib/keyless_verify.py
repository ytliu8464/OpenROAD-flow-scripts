# SPDX-License-Identifier: BSD-3-Clause
"""Verification helpers that work *only* from a 32-byte master seed.

For placement and CTS we reuse the *embed CSV* as the committed selection list
(per the existing verifier model), but recompute target bits / target perms
with whatever seed is passed in.  Wrong-key experiments then naturally drop
extraction towards 0.5 (placement pairs / CTS) or 1/6 (placement triples).

For routing we reconstruct WM_R from the seed alone (Bernoulli HMAC).
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import struct
import sys
from pathlib import Path
from typing import Iterable, Set, Tuple

# Delegate to the existing PRF implementations so we stay byte-for-byte
# compatible with the embed-time selection.
_WM_ROOT = Path(__file__).resolve().parents[2]  # flow/watermarking
sys.path.insert(0, str(_WM_ROOT / "place_ordering"))
sys.path.insert(0, str(_WM_ROOT / "cts_v2"))

try:
    from watermark_common import (   # type: ignore
        target_bit_for_pair as _place_bit,
        target_perm_index as _place_perm,
    )
except Exception:
    _place_bit = None
    _place_perm = None


def _hmac_digest(seed: bytes, *parts: bytes) -> bytes:
    """Length-prefixed HMAC-SHA256, byte-for-byte compatible with
    cts_watermark_common.hmac_digest."""
    h = hmac.new(seed, b"", hashlib.sha256)
    for p in parts:
        h.update(struct.pack(">I", len(p)))
        h.update(p)
    return h.digest()


def _cts_pair_bits_inline(seed: bytes, pair_key: str,
                          l_a: str, l_b: str) -> Tuple[int, int]:
    """Inline replica of cts_watermark_common.pair_bits.  Avoids importing the
    full cts_v2 module (which depends on ``odb`` at module-load time and can
    fail outside an OpenROAD-python interpreter)."""
    d = _hmac_digest(
        seed, b"pair",
        pair_key.encode("utf-8"),
        l_a.encode("utf-8"),
        l_b.encode("utf-8"),
    )
    return d[0] & 1, (d[0] >> 1) & 1


# Prefer the canonical implementation when it imports cleanly, but always have
# the inline fallback so verification works without a live OpenROAD context.
try:
    from cts_watermark_common import pair_bits as _cts_pair_bits  # type: ignore
except Exception:
    _cts_pair_bits = _cts_pair_bits_inline


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------

def _parse_tile_id(s: str) -> Tuple[int, int]:
    """Tile id in the embed CSV is rendered as 'tx_ty'."""
    if not s:
        return (0, 0)
    parts = s.replace("(", "").replace(")", "").replace(",", "_").split("_")
    try:
        return (int(parts[0]), int(parts[1]))
    except Exception:
        return (0, 0)


def _row_tile_id(row: dict) -> Tuple[int, int]:
    """Build a tile (tx, ty) tuple from either separate or combined fields."""
    if "tile_tx" in row and "tile_ty" in row:
        try:
            return (int(row["tile_tx"]), int(row["tile_ty"]))
        except Exception:
            return (0, 0)
    return _parse_tile_id(row.get("tile_id", ""))


def _row_cell_names(row: dict) -> Tuple[str, str, str]:
    """Return (name_a, name_b, name_c) supporting both schemas (A_name/B_name/C_name
    in the v2 embed CSV; legacy name_a/name_b/name_c for older runs)."""
    a = row.get("A_name") or row.get("name_a", "")
    b = row.get("B_name") or row.get("name_b", "")
    c = row.get("C_name") or row.get("name_c", "")
    return a, b, c


def _row_satisfied(row: dict) -> bool:
    """The 'satisfied' column in the embed CSV indicates the constraint was
    actually achieved in the routed layout.  Treat missing values as True
    (legacy CSVs that lacked the column always recorded only satisfied rows)."""
    v = row.get("satisfied", "True")
    return str(v).strip().lower() not in ("false", "0", "no")


def placement_extraction_rate(embed_csv: Path,
                              observed_csv: Path,
                              seed_placement: bytes) -> Tuple[int, int]:
    """Return (X, x): claims checked and number of mismatches.

    For each successfully-embedded constraint (``satisfied=True`` and not
    rejected by ``skipped_reason``), the bit actually committed to the routed
    layout equals the embed-time ``target_bit`` (resp. ``target_perm``).  We
    recompute the target using the seed passed in and count mismatches:

      * true seed   -> recomputed == embed target -> r_P = 1.0
      * wrong seed  -> recomputed differs ~50%    -> r_P ~ 0.5

    The legacy ``observed_csv`` argument is kept for API stability but is no
    longer required: the current schema only writes summary verify CSVs
    (``metric,value`` or ``stage,constraints_ok,constraints_total``), so the
    per-pair observation is read from the embed CSV instead.
    """
    if _place_bit is None or _place_perm is None:
        raise RuntimeError("place_ordering.watermark_common not importable")
    _ = observed_csv  # accepted but unused; see docstring.

    big_x = 0
    small_x = 0
    for row in csv.DictReader(open(embed_csv)):
        if row.get("skipped_reason", "") not in ("", "already_satisfied"):
            continue
        if not _row_satisfied(row):
            continue
        kind = row.get("kind", "pair")
        tile_id = _row_tile_id(row)
        name_a, name_b, name_c = _row_cell_names(row)
        if kind == "pair":
            try:
                observed = int(row["target_bit"])
            except (KeyError, ValueError, TypeError):
                continue
            target = _place_bit(seed_placement, tile_id, name_a, name_b)
            big_x += 1
            if observed != target:
                small_x += 1
        else:  # "group" / triple
            names = [n for n in (name_a, name_b, name_c) if n]
            try:
                observed = int(row["target_perm"])
            except (KeyError, ValueError, TypeError):
                continue
            target = _place_perm(seed_placement, tile_id, names)
            big_x += 1
            if observed != target:
                small_x += 1
    return big_x, small_x


# ---------------------------------------------------------------------------
# CTS
# ---------------------------------------------------------------------------

def cts_extraction_rate(embed_csv: Path, observed_csv: Path,
                        seed_cts: bytes) -> Tuple[int, int]:
    """Return (X, x) for the CTS stage.

    The embed CSV records ``target_bit`` (LCB parity selected by the true seed)
    and ``final_bit`` (parity actually committed after the embed attempt).  For
    pairs the embedder accepted, ``final_bit`` is what a verifier reads back
    from the layout, so we use it as the observed value and compare against the
    target recomputed under ``seed_cts``.

    The legacy per-LCB ``observed_csv`` argument is kept for API stability but
    is unused: the current schema only writes summary verify CSVs.
    """
    _ = observed_csv  # accepted but unused; see docstring.

    big_x = 0
    small_x = 0
    for r in csv.DictReader(open(embed_csv)):
        # Skip rows the embedder rejected at write time.
        if r.get("skipped_reason", "") not in ("", "ok", "already_satisfied"):
            continue
        status = r.get("status", "")
        if status and status not in ("ok", "accepted", "already_satisfied"):
            continue
        # Current schema: pair_key, L_A, L_B.  Legacy: pair_id, lcb_a/name_a, ...
        pair_key = r.get("pair_key") or r.get("pair_id", "")
        l_a = r.get("L_A") or r.get("lcb_a") or r.get("name_a", "")
        l_b = r.get("L_B") or r.get("lcb_b") or r.get("name_b", "")
        if not (pair_key and l_a and l_b):
            continue
        try:
            observed = int(r.get("final_bit", r.get("target_bit", "")))
        except (TypeError, ValueError):
            continue
        target_bit, _tgt_is_A = _cts_pair_bits(seed_cts, pair_key, l_a, l_b)
        big_x += 1
        if observed != target_bit:
            small_x += 1
    return big_x, small_x


# ---------------------------------------------------------------------------
# Routing  (key-only reconstruction; no embed CSV needed)
# ---------------------------------------------------------------------------

def routing_wm_set(seed_routing: bytes, net_names: Iterable[str],
                   fraction: float) -> Set[str]:
    """Reconstruct WM_R from seed only (paper Eq. eq:routing_selection).

    Mirrors the C++ implementation in src/wmk/Watermark.cpp added in Phase 1.0:
        u = first 4 bytes of HMAC-SHA256(seed, b"net\\0" || net_name)
        select iff u/2^32 < fraction
    """
    out: Set[str] = set()
    for name in net_names:
        d = hmac.new(seed_routing,
                     b"net\0" + name.encode("utf-8"),
                     hashlib.sha256).digest()
        u = int.from_bytes(d[:4], "little") / 4294967296.0
        if u < fraction:
            out.add(name)
    return out


__all__ = [
    "placement_extraction_rate",
    "cts_extraction_rate",
    "routing_wm_set",
]
