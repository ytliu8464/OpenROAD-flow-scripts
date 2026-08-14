# SPDX-License-Identifier: BSD-3-Clause
"""Shared helpers for pairwise / small-group relative-ordering placement watermarking.

Uses the 32B ``seed_placement`` (``gen_key/``) as the HMAC-SHA256 key for PRF
selection and target bits/permutations.
"""

from __future__ import annotations

import math
import os
import re
import struct
import sys
from bisect import bisect_left
from typing import (
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
)

# ---------------------------------------------------------------------------
# Keyed primitives (shared with every other stage -- see ../wm_prf.py)
# ---------------------------------------------------------------------------

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wm_prf import (  # noqa: E402
    argv_after_openroad_driver,
    binomial_pc,
    hmac_digest as _hmac,
    load_seed_hex,
)


# ---------------------------------------------------------------------------
# PRF: pair/group ids and targets
# ---------------------------------------------------------------------------

def pair_sort_key(seed: bytes, tile_id: Tuple[int, int], name_a: str, name_b: str) -> bytes:
    na, nb = sorted((name_a, name_b))
    tid = struct.pack(">ii", int(tile_id[0]), int(tile_id[1]))
    return _hmac(seed, b"pair_sort", tid, na.encode("utf-8"), nb.encode("utf-8"))


def pair_id_str(name_a: str, name_b: str) -> str:
    a, b = sorted((name_a, name_b))
    return f"{a}|{b}"


def target_bit_for_pair(seed: bytes, tile_id: Tuple[int, int], name_a: str, name_b: str) -> int:
    tid = struct.pack(">ii", int(tile_id[0]), int(tile_id[1]))
    na, nb = sorted((name_a, name_b))
    d = _hmac(seed, b"bit", tid, na.encode("utf-8"), nb.encode("utf-8"))
    return d[0] & 1


def group_sort_key(seed: bytes, tile_id: Tuple[int, int], names: Sequence[str]) -> bytes:
    tid = struct.pack(">ii", int(tile_id[0]), int(tile_id[1]))
    key = "|".join(sorted(names))
    return _hmac(seed, b"group_sort", tid, key.encode("utf-8"))


def group_id_str(names: Sequence[str]) -> str:
    return "|".join(sorted(names))


def target_perm_index(seed: bytes, tile_id: Tuple[int, int], names: Sequence[str]) -> int:
    """Return 0..5 indexing into PERMUTATIONS3."""
    tid = struct.pack(">ii", int(tile_id[0]), int(tile_id[1]))
    key = "|".join(sorted(names))
    d = _hmac(seed, b"perm", tid, key.encode("utf-8"))
    return int.from_bytes(d[:4], "big") % 6


# Six permutations as maps: output position index -> input slot index (left/mid/right)
# Slots 0,1,2 are sorted by increasing x before reorder.
PERMUTATIONS3: Tuple[Tuple[int, int, int], ...] = (
    (0, 1, 2),
    (0, 2, 1),
    (1, 0, 2),
    (1, 2, 0),
    (2, 0, 1),
    (2, 1, 0),
)


def permuted_order_names(names: Sequence[str], perm_idx: int) -> Tuple[str, str, str]:
    """Left-to-right order after permuting the **lexicographically sorted** triple."""
    if len(names) != 3:
        raise ValueError("need exactly 3 names")
    order = PERMUTATIONS3[perm_idx % 6]
    lst = sorted(names)
    return (lst[order[0]], lst[order[1]], lst[order[2]])




# ---------------------------------------------------------------------------
# Row / site geometry
# ---------------------------------------------------------------------------

def sorted_row_bottoms(block) -> List[int]:
    rows = list(block.getRows())
    if not rows:
        raise RuntimeError("No rows in block")
    return sorted({r.getBBox().yMin() for r in rows})






def inst_size(inst) -> Tuple[int, int]:
    m = inst.getMaster()
    return m.getWidth(), m.getHeight()


def inst_bottom_left(inst) -> Tuple[int, int]:
    bb = inst.getBBox()
    return bb.xMin(), bb.yMin()


def snap_row_y(y: int, row_bottoms: Sequence[int]) -> int:
    best = row_bottoms[0]
    bd = abs(y - best)
    for rb in row_bottoms[1:]:
        d = abs(y - rb)
        if d < bd:
            best = rb
            bd = d
    return best




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
# Master / cell filters
# ---------------------------------------------------------------------------

_FILLER_RE = re.compile(
    r"(FILL|FILLCELL|TAP|ENDCAP|DECAP|BOUNDARY|TIE|LOGIC_0|LOGIC_1|CLKBUF|CLKINV)",
    re.I,
)


def is_filler_tap_endcap(master) -> bool:
    try:
        name = master.getName()
    except Exception:
        name = ""
    if _FILLER_RE.search(name or ""):
        return True
    try:
        if hasattr(master, "getType") and master.getType() is not None:
            t = str(master.getType()).upper()
            if "FILL" in t or "ENDCAP" in t or "TIE" in t:
                return True
    except Exception:
        pass
    return False


def fanout_of(inst) -> int:
    """Sum of fanouts on output pins (net ITerm count minus driver)."""
    total = 0
    try:
        for it in inst.getITerms():
            try:
                if not it.isOutputSignal():
                    continue
            except Exception:
                continue
            net = it.getNet()
            if net is None:
                continue
            try:
                n = net.getITermCount()
            except Exception:
                iterms = list(net.getITerms())
                n = len(iterms)
            total = max(total, max(0, int(n) - 1))
    except Exception:
        pass
    return total


def build_clock_net_ids(block) -> Set[int]:
    """Return dbNet ids that are clock nets (sigType CLOCK), if available."""
    out: Set[int] = set()
    try:
        import odb
        clk = getattr(odb, "dbSigType", None)
        if clk is None:
            return out
        for net in block.getNets():
            try:
                st = net.getSigType()
                if st == odb.dbSigType.CLOCK:
                    out.add(net.getId())
            except Exception:
                continue
    except Exception:
        pass
    return out


def is_clock_cell(inst, clk_net_ids: Set[int]) -> bool:
    if not clk_net_ids:
        return False
    try:
        for it in inst.getITerms():
            net = it.getNet()
            if net is None:
                continue
            if net.getId() in clk_net_ids:
                return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# STA: worst slack per instance (single pass)
# ---------------------------------------------------------------------------

def filter_by_slack(
    design,
    cells: Sequence,
    slack_threshold_s: float,
    sdc_path: Optional[str] = None,
    lib_files: Optional[Sequence[str]] = None,
) -> Tuple[List[object], Dict[str, float], Dict[str, str]]:
    """Keep cells with worst pin slack >= threshold; return (kept, worst_slack_by_name, info)."""
    info: Dict[str, str] = {"mode": "unset"}
    slack_by_name: Dict[str, float] = {}

    if lib_files is None:
        env_libs = os.environ.get("WM_LIB_FILES", "")
        lib_files = [p for p in env_libs.split() if p.strip()]
    loaded = 0
    for lib in lib_files or []:
        try:
            design.evalTclString(f'read_liberty "{lib}"')
            loaded += 1
        except Exception as e:
            info["lib_error"] = f"{lib}: {e}"
    if loaded:
        info["libs_loaded"] = str(loaded)

    if sdc_path:
        try:
            design.evalTclString(f'read_sdc "{sdc_path}"')
            info["sdc"] = sdc_path
        except Exception as e:
            info["sdc_error"] = f"{e}"

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
        kept, info2 = _heuristic_filter(design, cells, info)
        for c in kept:
            slack_by_name[c.getName()] = slack_threshold_s
        return kept, slack_by_name, info2

    info["mode"] = "sta"
    try:
        rise = getattr(Timing, "Rise")
        fall = getattr(Timing, "Fall")
        tmax = getattr(Timing, "Max")
    except Exception:
        info["mode"] = "heuristic_no_enum"
        kept, info2 = _heuristic_filter(design, cells, info)
        for c in kept:
            slack_by_name[c.getName()] = slack_threshold_s
        return kept, slack_by_name, info2

    kept: List[object] = []
    for inst in cells:
        worst: Optional[float] = None
        try:
            iterms = list(inst.getITerms())
        except Exception:
            iterms = []
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
        name = inst.getName()
        if worst is None:
            slack_by_name[name] = slack_threshold_s
            kept.append(inst)
            continue
        slack_by_name[name] = worst
        if worst >= slack_threshold_s:
            kept.append(inst)

    info["kept"] = str(len(kept))
    info["dropped"] = str(len(cells) - len(kept))
    return kept, slack_by_name, info


def compute_worst_slacks(
    design,
    insts: Sequence[object],
    refresh_parasitics: bool = True,
) -> Dict[str, float]:
    """Re-estimate parasitics (optional) and return worst pin slack per inst.

    Liberty / SDC must already be loaded in ``design`` (e.g. by an earlier
    ``filter_by_slack`` call). Returns ``{inst_name: worst_slack_seconds}``.
    Insts with no resolvable slack are reported as ``+inf``.
    """
    if refresh_parasitics:
        try:
            design.evalTclString("estimate_parasitics -placement")
        except Exception:
            pass
    try:
        from openroad import Timing  # type: ignore

        timing = Timing(design)
        rise = getattr(Timing, "Rise")
        fall = getattr(Timing, "Fall")
        tmax = getattr(Timing, "Max")
    except Exception:
        return {}
    out: Dict[str, float] = {}
    for inst in insts:
        worst: Optional[float] = None
        try:
            iterms = list(inst.getITerms())
        except Exception:
            iterms = []
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
        try:
            nm = inst.getName()
        except Exception:
            continue
        out[nm] = worst if worst is not None else float("inf")
    return out


def _heuristic_filter(design, cells: Sequence, info: Dict[str, str]) -> Tuple[List[object], Dict[str, str]]:
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
    return kept, info


# ---------------------------------------------------------------------------
# Tile grid
# ---------------------------------------------------------------------------

def core_bbox(block) -> Tuple[int, int, int, int]:
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


def tile_area_dbu(bbox: Tuple[int, int, int, int], nx: int, ny: int) -> float:
    x0, y0, x1, y1 = bbox
    w = (x1 - x0) / float(max(1, nx))
    h = (y1 - y0) / float(max(1, ny))
    return w * h


# ---------------------------------------------------------------------------
# Criticality bin
# ---------------------------------------------------------------------------

def crit_bin_for(slack_s: float, bin_width_ns: float) -> int:
    if bin_width_ns <= 0:
        return 0
    return int(math.floor(slack_s / (bin_width_ns * 1e-9)))


# ---------------------------------------------------------------------------
# Macro / obstruction spatial index (tile buckets)
# ---------------------------------------------------------------------------

class MacroIndex:
    """Bucket expanded macro/blockage bboxes by tile for proximity checks."""

    def __init__(
        self,
        block,
        bbox: Tuple[int, int, int, int],
        nx: int,
        ny: int,
        tw: float,
        th: float,
        margin_dbu: int,
    ) -> None:
        self._bbox = bbox
        self._nx = nx
        self._ny = ny
        self._tw = tw
        self._th = th
        self._margin = margin_dbu
        self._boxes: Dict[Tuple[int, int], List[Tuple[int, int, int, int]]] = {}

        # Non-core instances (macros)
        for inst in block.getInsts():
            try:
                if inst.isCore():
                    continue
                bb = inst.getBBox()
                self._add_box(bb.xMin(), bb.yMin(), bb.xMax(), bb.yMax())
            except Exception:
                continue

        # Obstructions
        try:
            for obs in block.getObstructions():
                try:
                    bb = obs.getBBox()
                    self._add_box(bb.xMin(), bb.yMin(), bb.xMax(), bb.yMax())
                except Exception:
                    continue
        except Exception:
            pass

    def _tiles_for_rect(self, x0: int, y0: int, x1: int, y1: int) -> List[Tuple[int, int]]:
        bx0, by0, bx1, by1 = self._bbox
        txs = []
        if self._tw <= 0 or self._th <= 0:
            return [(0, 0)]
        tx0 = max(0, min(self._nx - 1, int((x0 - bx0) / self._tw)))
        tx1 = max(0, min(self._nx - 1, int((x1 - bx0) / self._tw)))
        ty0 = max(0, min(self._ny - 1, int((y0 - by0) / self._th)))
        ty1 = max(0, min(self._ny - 1, int((y1 - by0) / self._th)))
        for tx in range(tx0, tx1 + 1):
            for ty in range(ty0, ty1 + 1):
                txs.append((tx, ty))
        return txs if txs else [(0, 0)]

    def _add_box(self, x0: int, y0: int, x1: int, y1: int) -> None:
        m = self._margin
        x0, y0, x1, y1 = x0 - m, y0 - m, x1 + m, y1 + m
        for tid in self._tiles_for_rect(x0, y0, x1, y1):
            self._boxes.setdefault(tid, []).append((x0, y0, x1, y1))

    def near_blockage(self, inst) -> bool:
        bb = inst.getBBox()
        x0, y0, x1, y1 = bb.xMin(), bb.yMin(), bb.xMax(), bb.yMax()
        for tid in self._tiles_for_rect(x0, y0, x1, y1):
            for bx0, by0, bx1, by1 in self._boxes.get(tid, []):
                if not (x1 < bx0 or x0 > bx1 or y1 < by0 or y0 > by1):
                    return True
        return False


# ---------------------------------------------------------------------------
# Bucket key
# ---------------------------------------------------------------------------

BucketKey = Tuple[Tuple[int, int], int, int, int]  # (tile_tx, tile_ty), row_y, width, crit_bin


def make_bucket_key(
    tile_id: Tuple[int, int],
    row_y: int,
    master_width: int,
    slack_s: float,
    crit_bin_width_ns: float,
) -> BucketKey:
    cb = crit_bin_for(slack_s, crit_bin_width_ns)
    return (tile_id[0], tile_id[1]), row_y, master_width, cb


# ---------------------------------------------------------------------------
# Sliding-window pair/triple enumeration
# ---------------------------------------------------------------------------

def enumerate_close_pairs_sorted_row(
    cells_sorted_x: Sequence[object],
    max_dx_dbu: int,
) -> Iterator[Tuple[object, object]]:
    """Yield (A,B) with x(A)<x(B) and x(B)-x(A) <= max_dx; O(n) window per row bucket."""
    n = len(cells_sorted_x)
    for i in range(n):
        xi, _ = inst_bottom_left(cells_sorted_x[i])
        for j in range(i + 1, n):
            xj, _ = inst_bottom_left(cells_sorted_x[j])
            if xj - xi > max_dx_dbu:
                break
            yield (cells_sorted_x[i], cells_sorted_x[j])


def enumerate_close_pairs_neighbor_k(
    cells_sorted_x: Sequence[object],
    max_dx_dbu: int,
    k: int,
    pos: Optional[Dict[int, Tuple[int, int]]] = None,
) -> Iterator[Tuple[object, object]]:
    """Bounded-K version: yield only (i, i+1), ..., (i, i+k) within max_dx_dbu.

    With ``k=2`` (default), at most ``2*n`` pairs are enumerated per bucket
    instead of the full O(n^2) window. Uses ``pos`` (inst_id -> (x, y)) as a
    fast lookup if provided, otherwise falls back to ``inst_bottom_left``.
    """
    n = len(cells_sorted_x)
    if k <= 0:
        # Full window mode for experiments; identical to enumerate_close_pairs_sorted_row.
        yield from enumerate_close_pairs_sorted_row(cells_sorted_x, max_dx_dbu)
        return

    if pos is not None:
        def _xof(c):
            try:
                return pos[c.getId()][0]
            except Exception:
                return inst_bottom_left(c)[0]
    else:
        def _xof(c):
            return inst_bottom_left(c)[0]

    for i in range(n):
        xi = _xof(cells_sorted_x[i])
        upper = min(n, i + 1 + k)
        for j in range(i + 1, upper):
            xj = _xof(cells_sorted_x[j])
            if xj - xi > max_dx_dbu:
                break
            yield (cells_sorted_x[i], cells_sorted_x[j])


def enumerate_close_triples_sorted_row(
    cells_sorted_x: Sequence[object],
    max_dx_dbu: int,
) -> Iterator[Tuple[object, object, object]]:
    """Adjacent triples in x order with span <= max_dx (first to last)."""
    n = len(cells_sorted_x)
    for i in range(n):
        xi, _ = inst_bottom_left(cells_sorted_x[i])
        for j in range(i + 1, n):
            xj, _ = inst_bottom_left(cells_sorted_x[j])
            if xj - xi > max_dx_dbu:
                break
            for k in range(j + 1, n):
                xk, _ = inst_bottom_left(cells_sorted_x[k])
                if xk - xi > max_dx_dbu:
                    break
                yield (cells_sorted_x[i], cells_sorted_x[j], cells_sorted_x[k])




# ---------------------------------------------------------------------------
# HPWL (local nets only)
# ---------------------------------------------------------------------------



def affected_nets(insts: Sequence[object]) -> Set[object]:
    nets: Set[object] = set()
    seen: Set[int] = set()
    for inst in insts:
        try:
            for it in inst.getITerms():
                net = it.getNet()
                if net is None:
                    continue
                nid = net.getId()
                if nid in seen:
                    continue
                seen.add(nid)
                nets.add(net)
        except Exception:
            continue
    return nets


def _pin_centers_for_net(net, mover_old: Dict[object, Tuple[int, int]]) -> List[Tuple[int, int]]:
    pins: List[Tuple[int, int]] = []
    for it in net.getITerms():
        inst = it.getInst()
        if inst is None:
            continue
        if inst in mover_old:
            pins.append(mover_old[inst])
            continue
        try:
            bb = it.getBBox()
            pins.append(((bb.xMin() + bb.xMax()) // 2, (bb.yMin() + bb.yMax()) // 2))
        except Exception:
            continue
    return pins


def _hpwl_from_pins(pins: Sequence[Tuple[int, int]]) -> int:
    if len(pins) < 2:
        return 0
    xs = [p[0] for p in pins]
    ys = [p[1] for p in pins]
    return (max(xs) - min(xs)) + (max(ys) - min(ys))


def hpwl_delta_for_moves(
    movers: Sequence[Tuple[object, Tuple[int, int], Tuple[int, int]]],
) -> int:
    """Sum over affected nets of (hpwl_after - hpwl_before), DBU.

    movers: (inst, (old_x, old_y), (new_x, new_y)) — old = position before move
    """
    inst_set = [m[0] for m in movers]
    mover_old = {m[0]: (m[1][0], m[1][1]) for m in movers}
    mover_new = {m[0]: (m[2][0], m[2][1]) for m in movers}
    nets = affected_nets(inst_set)
    delta = 0
    for net in nets:
        try:
            pins_before = _pin_centers_for_net(net, mover_old)
            pins_after = []
            for it in net.getITerms():
                inst = it.getInst()
                if inst is None:
                    continue
                if inst in mover_new:
                    pins_after.append(mover_new[inst])
                else:
                    try:
                        bb = it.getBBox()
                        pins_after.append(
                            ((bb.xMin() + bb.xMax()) // 2, (bb.yMin() + bb.yMax()) // 2)
                        )
                    except Exception:
                        continue
            hb = _hpwl_from_pins(pins_before)
            ha = _hpwl_from_pins(pins_after)
            delta += ha - hb
        except Exception:
            continue
    return delta


def swap_delta_hpwl(a, b) -> int:
    ax, ay = inst_bottom_left(a)
    bx, by = inst_bottom_left(b)
    return hpwl_delta_for_moves([(a, (ax, ay), (bx, by)), (b, (bx, by), (ax, ay))])


def triple_perm_hpwl_delta(a, b, c, perm_idx: int) -> int:
    """HPWL delta vs current placement when assigning sorted x slots to permuted instances."""
    insts = (a, b, c)
    xs = sorted([inst_bottom_left(i)[0] for i in insts])
    perm = PERMUTATIONS3[perm_idx % 6]
    movers = []
    for slot_k in range(3):
        inst = insts[perm[slot_k]]
        ox, oy = inst_bottom_left(inst)
        movers.append((inst, (ox, oy), (xs[slot_k], oy)))
    return hpwl_delta_for_moves(movers)


def triple_all_perm_hpwl_costs(a, b, c) -> List[int]:
    return [triple_perm_hpwl_delta(a, b, c, k) for k in range(6)]


# ---------------------------------------------------------------------------
# Deduped HPWL cache (avoid OpenDB walks during enumeration)
# ---------------------------------------------------------------------------

class HPWLCache:
    """Per-phase deduplicated cache of cell positions and incident net pins.

    Layout:
      pos:       Dict[inst_id, (x, y)]                         # only kept cells
      inst_nets: Dict[inst_id, List[net_id]]                   # only kept cells
      net_pins:  Dict[net_id, List[(px, py, owner_inst_id_or_None)]]

    Each net is stored exactly once. Pins on movable kept cells carry an
    ``owner_inst_id`` so that hypothetical moves can be applied without OpenDB
    calls. Pins on non-kept cells (e.g. fixed/clock cells we don't watermark)
    store ``None`` and are treated as static. Nets with more than
    ``net_fanout_max`` ITerms are skipped entirely; they are usually
    global/control nets, expensive to score, and not informative for local
    swap quality.

    Memory budget for typical asap7 designs: ~25 MB on jpeg, ~70-150 MB on
    swerv_wrapper. The cache is freed before final DPL.
    """

    def __init__(
        self,
        kept_cells: Sequence[object],
        net_fanout_max: int = 64,
    ) -> None:
        self.net_fanout_max = max(2, int(net_fanout_max))
        self.pos: Dict[int, Tuple[int, int]] = {}
        self.inst_nets: Dict[int, List[int]] = {}
        self.net_pins: Dict[int, List[Tuple[int, int, Optional[int]]]] = {}
        self.skipped_nets: Set[int] = set()

        kept_ids: Set[int] = set()
        for inst in kept_cells:
            try:
                iid = inst.getId()
            except Exception:
                continue
            try:
                bb = inst.getBBox()
                self.pos[iid] = (bb.xMin(), bb.yMin())
            except Exception:
                continue
            kept_ids.add(iid)
            self.inst_nets[iid] = []

        # Walk kept cells to collect their incident nets.
        seen_nets: Set[int] = set()
        for inst in kept_cells:
            try:
                iid = inst.getId()
            except Exception:
                continue
            if iid not in self.inst_nets:
                continue
            try:
                iterms = list(inst.getITerms())
            except Exception:
                iterms = []
            for it in iterms:
                try:
                    net = it.getNet()
                except Exception:
                    net = None
                if net is None:
                    continue
                try:
                    nid = net.getId()
                except Exception:
                    continue
                if nid in self.skipped_nets:
                    continue
                if nid in seen_nets:
                    if nid not in self.inst_nets[iid]:
                        self.inst_nets[iid].append(nid)
                    continue
                seen_nets.add(nid)
                # Cache pins for this net once.
                try:
                    pin_iterms = list(net.getITerms())
                except Exception:
                    pin_iterms = []
                if len(pin_iterms) > self.net_fanout_max:
                    self.skipped_nets.add(nid)
                    continue
                pins: List[Tuple[int, int, Optional[int]]] = []
                ok = True
                for pit in pin_iterms:
                    pinst = pit.getInst()
                    try:
                        bb = pit.getBBox()
                        cx = (bb.xMin() + bb.xMax()) // 2
                        cy = (bb.yMin() + bb.yMax()) // 2
                    except Exception:
                        ok = False
                        break
                    pid = None
                    if pinst is not None:
                        try:
                            opid = pinst.getId()
                            if opid in kept_ids:
                                pid = opid
                        except Exception:
                            pid = None
                    pins.append((cx, cy, pid))
                if not ok or len(pins) < 2:
                    self.skipped_nets.add(nid)
                    continue
                self.net_pins[nid] = pins
                if nid not in self.inst_nets[iid]:
                    self.inst_nets[iid].append(nid)

    @staticmethod
    def _hpwl(pins: Sequence[Tuple[int, int]]) -> int:
        if len(pins) < 2:
            return 0
        xmin = pins[0][0]
        xmax = xmin
        ymin = pins[0][1]
        ymax = ymin
        for px, py in pins[1:]:
            if px < xmin:
                xmin = px
            elif px > xmax:
                xmax = px
            if py < ymin:
                ymin = py
            elif py > ymax:
                ymax = py
        return (xmax - xmin) + (ymax - ymin)

    def _hpwl_with_overrides(
        self,
        nid: int,
        overrides: Dict[int, Tuple[int, int]],
    ) -> int:
        pts: List[Tuple[int, int]] = []
        for px, py, oid in self.net_pins[nid]:
            if oid is not None and oid in overrides:
                base = self.pos.get(oid)
                if base is None:
                    pts.append((px, py))
                    continue
                nx, ny = overrides[oid]
                ox, oy = base
                pts.append((px + (nx - ox), py + (ny - oy)))
            else:
                pts.append((px, py))
        return HPWLCache._hpwl(pts)

    def swap_delta_hpwl(self, a, b) -> int:
        """HPWL delta if instances a and b swap their (x, y) coordinates."""
        try:
            aid = a.getId()
            bid = b.getId()
        except Exception:
            return swap_delta_hpwl(a, b)
        ap = self.pos.get(aid)
        bp = self.pos.get(bid)
        if ap is None or bp is None:
            return swap_delta_hpwl(a, b)
        nets_a = self.inst_nets.get(aid, [])
        nets_b = self.inst_nets.get(bid, [])
        union: List[int] = list(nets_a)
        seen = set(nets_a)
        for n in nets_b:
            if n not in seen:
                seen.add(n)
                union.append(n)
        if not union:
            return 0
        overrides_after = {aid: bp, bid: ap}
        delta = 0
        for nid in union:
            pins = self.net_pins.get(nid)
            if pins is None:
                continue
            hb = HPWLCache._hpwl([(px, py) for px, py, _ in pins])
            ha = self._hpwl_with_overrides(nid, overrides_after)
            delta += ha - hb
        return delta

    def update_pos_after_swap(self, a, b) -> None:
        """Keep cache consistent after an actually applied swap."""
        try:
            aid = a.getId()
            bid = b.getId()
        except Exception:
            return
        ap = self.pos.get(aid)
        bp = self.pos.get(bid)
        if ap is None or bp is None:
            return
        # Update pin positions on every shared net.
        nets_a = set(self.inst_nets.get(aid, []))
        nets_b = set(self.inst_nets.get(bid, []))
        for nid in nets_a | nets_b:
            pins = self.net_pins.get(nid)
            if pins is None:
                continue
            new_pins: List[Tuple[int, int, Optional[int]]] = []
            for px, py, oid in pins:
                if oid == aid:
                    new_pins.append((px + (bp[0] - ap[0]), py + (bp[1] - ap[1]), oid))
                elif oid == bid:
                    new_pins.append((px + (ap[0] - bp[0]), py + (ap[1] - bp[1]), oid))
                else:
                    new_pins.append((px, py, oid))
            self.net_pins[nid] = new_pins
        self.pos[aid] = bp
        self.pos[bid] = ap

    def neighbor_min_slack(
        self,
        slack_map: Dict[str, float],
        id_to_name: Dict[int, str],
        default: float,
    ) -> Dict[int, float]:
        """For every cached inst, the min slack of any *net-mate* cell (excluding self).

        The instance's own slack is already filtered upstream by the slack
        threshold; this map protects against timing-critical *neighbors*. If
        a cell has no eligible neighbors (all nets dropped, all neighbors
        non-kept), it gets ``default`` (treated as "no constraint").

        Cells that pin into a net we skipped (high-fanout) are NOT considered,
        which is conservative for our purpose: we only protect cells whose
        signal-net neighbors are timing-critical.
        """
        out: Dict[int, float] = {}
        for iid, nets in self.inst_nets.items():
            best: Optional[float] = None
            for nid in nets:
                pins = self.net_pins.get(nid)
                if pins is None:
                    continue
                for _px, _py, oid in pins:
                    if oid is None or oid == iid:
                        continue
                    nm = id_to_name.get(oid)
                    if nm is None:
                        continue
                    s = slack_map.get(nm)
                    if s is None:
                        continue
                    if best is None or s < best:
                        best = s
            out[iid] = default if best is None else best
        return out


# ---------------------------------------------------------------------------
# Tile density (cell area fraction)
# ---------------------------------------------------------------------------

def compute_tile_density(
    cells: Sequence[object],
    bbox: Tuple[int, int, int, int],
    nx: int,
    ny: int,
    tw: float,
    th: float,
) -> Dict[Tuple[int, int], float]:
    """Fraction of tile area occupied by cell bounding boxes (may exceed 1)."""
    areas: Dict[Tuple[int, int], float] = {}
    ta = tile_area_dbu(bbox, nx, ny)
    x0, y0, x1, y1 = bbox
    for inst in cells:
        bb = inst.getBBox()
        ix0, iy0, ix1, iy1 = bb.xMin(), bb.yMin(), bb.xMax(), bb.yMax()
        tx0 = max(0, min(nx - 1, int((ix0 - x0) / tw))) if tw > 0 else 0
        tx1 = max(0, min(nx - 1, int((ix1 - x0) / tw))) if tw > 0 else 0
        ty0 = max(0, min(ny - 1, int((iy0 - y0) / th))) if th > 0 else 0
        ty1 = max(0, min(ny - 1, int((iy1 - y0) / th))) if th > 0 else 0
        area_inst = float((ix1 - ix0) * (iy1 - iy0))
        for tx in range(tx0, tx1 + 1):
            for ty in range(ty0, ty1 + 1):
                areas[(tx, ty)] = areas.get((tx, ty), 0.0) + area_inst
    if ta <= 0:
        return {k: 0.0 for k in areas}
    return {k: v / ta for k, v in areas.items()}


# ---------------------------------------------------------------------------
# Tile balance tracker
# ---------------------------------------------------------------------------

class TileBalanceTracker:
    """Track left/right displacement counts and total |dx| per tile (density balance)."""

    def __init__(self, cap_disp_dbu_per_tile: int, max_lr_skew: int = 8) -> None:
        self.cap = max(0, cap_disp_dbu_per_tile)
        self.max_lr_skew = max_lr_skew
        self.left_shift: Dict[Tuple[int, int], int] = {}
        self.right_shift: Dict[Tuple[int, int], int] = {}
        self.disp_sum: Dict[Tuple[int, int], int] = {}

    def try_moves(self, tid: Tuple[int, int], moves: Sequence[Tuple[int, int]]) -> bool:
        """Return True if moves can be applied without exceeding caps; state unchanged if False."""
        if not moves:
            return True
        add_disp = sum(abs(nx - ox) for ox, nx in moves)
        new_ls = self.left_shift.get(tid, 0)
        new_rs = self.right_shift.get(tid, 0)
        for ox, nx in moves:
            d = nx - ox
            if d < 0:
                new_ls += 1
            elif d > 0:
                new_rs += 1
        if self.disp_sum.get(tid, 0) + add_disp > self.cap:
            return False
        if abs(new_ls - new_rs) > self.max_lr_skew:
            return False
        self.disp_sum[tid] = self.disp_sum.get(tid, 0) + add_disp
        self.left_shift[tid] = new_ls
        self.right_shift[tid] = new_rs
        return True


def sort_instances_by_x(insts: Sequence[object]) -> List[object]:
    return sorted(insts, key=lambda i: inst_bottom_left(i)[0])


def order_names_left_to_right(insts: Sequence[object]) -> Tuple[str, ...]:
    return tuple(i.getName() for i in sort_instances_by_x(insts))
