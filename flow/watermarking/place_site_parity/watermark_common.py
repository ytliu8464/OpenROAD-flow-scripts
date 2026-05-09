# SPDX-License-Identifier: BSD-3-Clause
"""Shared helpers for site-parity (mod-5 along X) placement watermarking.

The 32B ``seed_placement`` byte string (produced by ``gen_key/``) is used as
the HMAC-SHA256 key for all per-tile / per-cell derivations. This
decouples the watermark from the free-form ``WM_KEY`` / ``WM_MESSAGE`` used
by the older row-parity flow.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import random
import struct
from typing import Dict, List, Optional, Sequence, Tuple

import odb


MODULUS = 5  # residue modulus for site parity
PC_P = 1.0 / MODULUS  # per-cell coincidence probability


# ---------------------------------------------------------------------------
# argv / seed loading
# ---------------------------------------------------------------------------

def argv_after_openroad_driver() -> List[str]:
    """Return argv slice after ``openroad -python -exit script.py``."""
    import sys

    skip = {
        "-python", "-exit", "-no_splash", "-no_init", "-no_settings",
        "-gui", "-minimize",
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


def load_seed_hex(path: str) -> bytes:
    """Read a hex seed file produced by ``gen_key/`` and return raw bytes.

    Accepts 64 hex chars (32B SHA-256 output). Whitespace/newline allowed.
    """
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"WM_SEED_HEX path not found: {path!r}")
    txt = open(path).read().strip()
    if len(txt) < 32:
        raise ValueError(f"seed hex too short in {path}: len={len(txt)}")
    try:
        raw = bytes.fromhex(txt)
    except ValueError as e:
        raise ValueError(f"seed file {path} is not valid hex: {e}")
    if len(raw) < 16:
        raise ValueError(f"seed too short after decode: {len(raw)} bytes")
    return raw


# ---------------------------------------------------------------------------
# HMAC helpers (all use the 32B seed as the key)
# ---------------------------------------------------------------------------

def _hmac(seed: bytes, *parts: bytes) -> bytes:
    h = hmac.new(seed, b"", hashlib.sha256)
    for p in parts:
        h.update(struct.pack(">I", len(p)))
        h.update(p)
    return h.digest()


def tile_rng(seed: bytes, tile_id: Tuple[int, int]) -> random.Random:
    tid = struct.pack(">ii", int(tile_id[0]), int(tile_id[1]))
    d = _hmac(seed, b"tile", tid)
    return random.Random(int.from_bytes(d[:8], "big"))


def target_residue(
    seed: bytes, tile_id: Tuple[int, int], inst_name: str
) -> int:
    tid = struct.pack(">ii", int(tile_id[0]), int(tile_id[1]))
    d = _hmac(seed, b"residue", tid, inst_name.encode("utf-8"))
    return d[0] % MODULUS


# ---------------------------------------------------------------------------
# Row / site geometry
# ---------------------------------------------------------------------------

def sorted_row_bottoms(block) -> List[int]:
    rows = list(block.getRows())
    if not rows:
        raise RuntimeError("No rows in block")
    return sorted({r.getBBox().yMin() for r in rows})


def rows_by_bottom(block) -> Dict[int, List[object]]:
    """Map ``row.yMin -> [row,...]`` (usually single row per Y)."""
    out: Dict[int, List[object]] = {}
    for r in block.getRows():
        out.setdefault(r.getBBox().yMin(), []).append(r)
    return out


def infer_row_pitch(row_bottoms: Sequence[int], site_height: int) -> int:
    if len(row_bottoms) >= 2:
        d = row_bottoms[1] - row_bottoms[0]
        if d > 0:
            return d
    return site_height


def row_for_y(rows_map: Dict[int, List[object]], y: int):
    """Return a row whose yMin is closest to ``y`` (any in the list)."""
    if not rows_map:
        return None
    best_y = min(rows_map.keys(), key=lambda ry: abs(ry - y))
    return rows_map[best_y][0]


def inst_size(inst) -> Tuple[int, int]:
    m = inst.getMaster()
    return m.getWidth(), m.getHeight()


def inst_bottom_left(inst) -> Tuple[int, int]:
    bb = inst.getBBox()
    return bb.xMin(), bb.yMin()


def site_col(inst, row_xmin: int, site_width: int) -> int:
    x, _ = inst_bottom_left(inst)
    return int(round((x - row_xmin) / float(site_width)))


def residue_of(inst, row_xmin: int, site_width: int) -> int:
    return site_col(inst, row_xmin, site_width) % MODULUS


# ---------------------------------------------------------------------------
# Signed mod-5 delta (shortest signed shift in sites)
# ---------------------------------------------------------------------------

def signed_delta_mod(target: int, current: int, modulus: int = MODULUS) -> int:
    d = (target - current) % modulus
    if d > modulus // 2:
        d -= modulus
    return d


# ---------------------------------------------------------------------------
# Core cell collection
# ---------------------------------------------------------------------------

def collect_movable_core_cells(block) -> List[object]:
    out: List[object] = []
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


# ---------------------------------------------------------------------------
# STA slack filter
# ---------------------------------------------------------------------------

def filter_by_slack(
    design,
    cells: Sequence,
    slack_threshold_s: float,
    sdc_path: Optional[str] = None,
    lib_files: Optional[Sequence[str]] = None,
) -> Tuple[List[object], Dict[str, str]]:
    """Keep only cells whose **worst** pin slack >= ``slack_threshold_s``.

    Falls back to a heuristic (drop sequentials + clock cells) if STA is not
    available. Returns (kept_cells, info) where ``info`` is diagnostic.
    """
    info: Dict[str, str] = {"mode": "unset"}

    # Load liberty files so STA can compute slack.
    # Prefer the caller-supplied list; fall back to WM_LIB_FILES env var.
    if lib_files is None:
        env_libs = os.environ.get("WM_LIB_FILES", "")
        lib_files = [p for p in env_libs.split() if p.strip()]
    loaded = 0
    for lib in lib_files:
        try:
            design.evalTclString(f'read_liberty "{lib}"')
            loaded += 1
        except Exception as e:
            info[f"lib_error"] = f"{lib}: {e}"
    if loaded:
        info["libs_loaded"] = str(loaded)

    # Optional: read SDC if caller supplied one.
    if sdc_path:
        try:
            design.evalTclString(f'read_sdc "{sdc_path}"')
            info["sdc"] = sdc_path
        except Exception as e:
            info["sdc_error"] = f"{e}"

    # Estimate parasitics (placement-based).
    try:
        design.evalTclString("estimate_parasitics -placement")
        info["parasitics"] = "placement"
    except Exception as e:
        info["parasitics_error"] = f"{e}"

    try:
        from openroad import Timing  # type: ignore

        timing = Timing(design)
        has_api = True
    except Exception as e:
        info["timing_error"] = f"{e}"
        has_api = False

    if not has_api:
        return _heuristic_filter(design, cells, info), info

    info["mode"] = "sta"
    kept: List[object] = []
    # Map of instance -> its worst slack (min over all iTerms / corners / edges).
    try:
        rise = getattr(Timing, "Rise")
        fall = getattr(Timing, "Fall")
        tmax = getattr(Timing, "Max")
    except Exception:
        info["mode"] = "heuristic_no_enum"
        return _heuristic_filter(design, cells, info), info

    for inst in cells:
        try:
            iterms = list(inst.getITerms())
        except Exception:
            iterms = []
        worst = None
        for it in iterms:
            for edge in (rise, fall):
                try:
                    s = timing.getPinSlack(it, edge, tmax)
                except Exception:
                    continue
                if s is None:
                    continue
                try:
                    sf = float(s)
                except (TypeError, ValueError):
                    continue
                if math.isnan(sf) or math.isinf(sf):
                    continue
                if worst is None or sf < worst:
                    worst = sf
        if worst is None:
            kept.append(inst)
            continue
        if worst >= slack_threshold_s:
            kept.append(inst)

    info["kept"] = str(len(kept))
    info["dropped"] = str(len(cells) - len(kept))
    return kept, info


def _heuristic_filter(design, cells: Sequence, info: Dict[str, str]) -> List[object]:
    info["mode"] = info.get("mode", "heuristic")
    kept: List[object] = []
    dropped_seq = 0
    for inst in cells:
        try:
            master = inst.getMaster()
            if design.isSequential(master):
                dropped_seq += 1
                continue
        except Exception:
            pass
        kept.append(inst)
    info["heuristic_dropped_sequential"] = str(dropped_seq)
    info["kept"] = str(len(kept))
    info["dropped"] = str(len(cells) - len(kept))
    return kept


# ---------------------------------------------------------------------------
# Tile grid
# ---------------------------------------------------------------------------

def core_bbox(block) -> Tuple[int, int, int, int]:
    """Return (xmin, ymin, xmax, ymax) of the core area in DBU.

    Uses die bbox as a stable fallback if the core area is zero.
    """
    try:
        ca = block.getCoreArea()
        x0, y0, x1, y1 = ca.xMin(), ca.yMin(), ca.xMax(), ca.yMax()
        if x1 > x0 and y1 > y0:
            return x0, y0, x1, y1
    except Exception:
        pass
    da = block.getDieArea()
    return da.xMin(), da.yMin(), da.xMax(), da.yMax()


def make_tile_grid(
    block, nx: int, ny: int
) -> Tuple[Tuple[int, int, int, int], float, float]:
    """Return (bbox, tile_w, tile_h) floats for the NX x NY grid."""
    x0, y0, x1, y1 = core_bbox(block)
    nx = max(1, int(nx))
    ny = max(1, int(ny))
    tw = (x1 - x0) / float(nx)
    th = (y1 - y0) / float(ny)
    return (x0, y0, x1, y1), tw, th


def tile_of(
    inst, bbox: Tuple[int, int, int, int], tw: float, th: float, nx: int, ny: int
) -> Tuple[int, int]:
    x, y = inst_bottom_left(inst)
    tx = min(nx - 1, max(0, int((x - bbox[0]) / tw))) if tw > 0 else 0
    ty = min(ny - 1, max(0, int((y - bbox[1]) / th))) if th > 0 else 0
    return tx, ty


# ---------------------------------------------------------------------------
# Binomial coincidence (p = 1/MODULUS)
# ---------------------------------------------------------------------------

def binomial_pc(num_constraints: int, num_failures: int, p: float = PC_P) -> float:
    """P(at most ``num_failures`` unsatisfied) under i.i.d. P(success)=p."""
    x = num_failures
    X = num_constraints
    total = 0.0
    for i in range(0, x + 1):
        total += math.comb(X, i) * (p ** (X - i)) * ((1.0 - p) ** i)
    return total
