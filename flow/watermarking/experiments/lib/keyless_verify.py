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

try:
    from cts_watermark_common import pair_bits as _cts_pair_bits  # type: ignore
except Exception:
    _cts_pair_bits = None


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


def placement_extraction_rate(embed_csv: Path,
                              observed_csv: Path,
                              seed_placement: bytes) -> Tuple[int, int]:
    """Return (X, x): claims checked and number of mismatches."""
    if _place_bit is None or _place_perm is None:
        raise RuntimeError("place_ordering.watermark_common not importable")

    embed = {}
    for row in csv.DictReader(open(embed_csv)):
        if row.get("skipped_reason", "") not in ("", "already_satisfied"):
            continue
        # rows in the embed CSV always have a stable identifier; if it's
        # absent, we fall back to "name_a|name_b[|name_c]".
        key = row.get("tuple_id") or row.get("pair_id") or row.get("group_id") \
              or row.get("name_a", "") + "|" + row.get("name_b", "") + \
                 (("|" + row.get("name_c", "")) if row.get("name_c") else "")
        embed[key] = row
    observed = {}
    for row in csv.DictReader(open(observed_csv)):
        key = row.get("tuple_id") or row.get("pair_id") or row.get("group_id") \
              or row.get("name_a", "") + "|" + row.get("name_b", "") + \
                 (("|" + row.get("name_c", "")) if row.get("name_c") else "")
        observed[key] = row

    big_x = 0
    small_x = 0
    for key, erow in embed.items():
        orow = observed.get(key)
        if orow is None:
            continue
        kind = erow.get("kind", "pair")
        tile_id = _parse_tile_id(erow.get("tile_id", ""))
        if kind == "pair":
            target = _place_bit(seed_placement, tile_id,
                                erow["name_a"], erow["name_b"])
            try:
                obs = int(orow.get("observed_bit",
                                   orow.get("obs_bit", -1)))
            except Exception:
                continue
            big_x += 1
            if obs != target:
                small_x += 1
        else:
            names = [erow["name_a"], erow["name_b"], erow.get("name_c", "")]
            names = [n for n in names if n]
            target = _place_perm(seed_placement, tile_id, names)
            try:
                obs = int(orow.get("observed_perm",
                                   orow.get("obs_perm", -1)))
            except Exception:
                continue
            big_x += 1
            if obs != target:
                small_x += 1
    return big_x, small_x


# ---------------------------------------------------------------------------
# CTS
# ---------------------------------------------------------------------------

def cts_extraction_rate(embed_csv: Path, observed_csv: Path,
                        seed_cts: bytes) -> Tuple[int, int]:
    if _cts_pair_bits is None:
        raise RuntimeError("cts_v2.cts_watermark_common not importable")

    embed_rows = list(csv.DictReader(open(embed_csv)))
    observed = {r.get("target_lcb", ""): r
                for r in csv.DictReader(open(observed_csv))
                if r.get("target_lcb")}

    big_x = 0
    small_x = 0
    for r in embed_rows:
        # Skip rows the embedder rejected at write time.
        if r.get("skipped_reason", "") not in ("", "ok", "already_satisfied"):
            continue
        status = r.get("status", "")
        if status and status not in ("ok", "accepted", "already_satisfied"):
            continue
        pair_key = r.get("pair_id", "")
        l_a = r.get("lcb_a", r.get("name_a", ""))
        l_b = r.get("lcb_b", r.get("name_b", ""))
        target_bit, _ = _cts_pair_bits(seed_cts, pair_key, l_a, l_b)
        tgt_lcb = r.get("target_lcb", "")
        orow = observed.get(tgt_lcb)
        if orow is None:
            continue
        try:
            obs = int(orow.get("observed_bit",
                               orow.get("obs_bit", -1)))
        except Exception:
            continue
        big_x += 1
        if obs != target_bit:
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
