# SPDX-License-Identifier: BSD-3-Clause
"""Shared helpers for CTS fanout-parity watermarking.

The 32B ``seed_cts`` byte string (produced by ``watermarking/gen_key/``) is
used as the HMAC-SHA256 key for all per-pair derivations. There are **no**
``WM_KEY`` / ``WM_MESSAGE`` inputs; determinism flows from the seed alone,
matching the convention established by ``place_site_parity/``.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import random
import struct
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import odb


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
    """Read a hex seed file produced by ``gen_key/`` and return raw bytes."""
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
# HMAC helpers (all keyed by the 32B seed)
# ---------------------------------------------------------------------------

def hmac_digest(seed: bytes, *parts: bytes) -> bytes:
    h = hmac.new(seed, b"", hashlib.sha256)
    for p in parts:
        h.update(struct.pack(">I", len(p)))
        h.update(p)
    return h.digest()


def master_rng(seed: bytes, domain: bytes = b"cts_pairs") -> random.Random:
    d = hmac_digest(seed, domain)
    return random.Random(int.from_bytes(d[:8], "big"))


def pair_bits(
    seed: bytes, pair_key: str, l_a_name: str, l_b_name: str
) -> Tuple[int, int]:
    """Return ``(target_bit, target_lcb_is_A)`` for a single LCB pair."""
    d = hmac_digest(
        seed, b"pair",
        pair_key.encode("utf-8"),
        l_a_name.encode("utf-8"),
        l_b_name.encode("utf-8"),
    )
    target_bit = d[0] & 1
    target_lcb_is_A = (d[0] >> 1) & 1
    return target_bit, target_lcb_is_A


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def inst_center(inst) -> Tuple[float, float]:
    bb = inst.getBBox()
    return ((bb.xMin() + bb.xMax()) / 2.0, (bb.yMin() + bb.yMax()) / 2.0)


def manhattan(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def site_pitch_dbu(block) -> int:
    """Return the site width (DBU) used as the "site pitch" unit."""
    rows = list(block.getRows())
    if not rows:
        return 1
    return rows[0].getSite().getWidth()


# ---------------------------------------------------------------------------
# Clock tree / LCB enumeration
# ---------------------------------------------------------------------------

# Master-name heuristics for clock buffers/inverters inserted by TritonCTS.
_CLKBUF_MASTER_HINTS = ("CLKBUF", "CLKINV", "CLKGATE", "CTSBUF")
_CLKBUF_INST_HINTS = ("clkbuf", "clkinv", "clkgate", "ctsbuf")


def _master_is_buffer_like(master) -> bool:
    """True if ``master`` is a single-input, single-output combinational cell
    (i.e., a buffer or inverter), regardless of whether the library named it as
    a clock buffer. CTS in ASAP7 reuses generic ``BUFx*`` cells, so name-based
    matching alone misses the LCBs."""
    try:
        if master.isSequential():
            return False
    except Exception:
        return False
    ins = outs = 0
    try:
        for mt in master.getMTerms():
            io = str(mt.getIoType()).upper()
            if io == "INPUT":
                ins += 1
            elif io == "OUTPUT":
                outs += 1
    except Exception:
        return False
    return ins == 1 and outs == 1


def _master_is_clock_buffer(master) -> bool:
    name = master.getName().upper()
    if any(h in name for h in _CLKBUF_MASTER_HINTS):
        return True
    return _master_is_buffer_like(master)


def _inst_name_looks_like_clkbuf(inst) -> bool:
    try:
        n = inst.getName()
    except Exception:
        return False
    base = n.rsplit("/", 1)[-1].lower()
    return any(base.startswith(h) for h in _CLKBUF_INST_HINTS)


def _inst_single_output_net(inst) -> Optional[object]:
    """Return the single OUTPUT iterm's net, or None if not exactly one output."""
    out_net = None
    out_count = 0
    for it in inst.getITerms():
        try:
            if it.getIoType() == "OUTPUT":
                out_count += 1
                out_net = it.getNet()
        except Exception:
            continue
    if out_count != 1:
        return None
    return out_net


def _inst_single_input_net(inst) -> Optional[object]:
    """Return the single INPUT iterm's net, or None if not exactly one input."""
    in_net = None
    in_count = 0
    for it in inst.getITerms():
        try:
            if it.getIoType() == "INPUT":
                in_count += 1
                in_net = it.getNet()
        except Exception:
            continue
    if in_count != 1:
        return None
    return in_net


def _iterm_is_sequential_clock_sink(it) -> bool:
    """True if ``it`` is the clock pin of a sequential instance."""
    try:
        if it.getIoType() != "INPUT":
            return False
        mt = it.getMTerm()
        sig = mt.getSigType()
        if str(sig).upper() != "CLOCK":
            inst = it.getInst()
            if inst is None:
                return False
            master = inst.getMaster()
            if not master.isSequential():
                return False
            return mt.getName().upper() in ("CP", "CLK", "CK", "CLOCK")
        return True
    except Exception:
        return False


def _net_driver_iterm(net) -> Optional[object]:
    """Return the first OUTPUT iterm driving this net, if any."""
    try:
        for it in net.getITerms():
            if it.getIoType() == "OUTPUT":
                return it
    except Exception:
        pass
    return None


def collect_lcbs(block) -> List[object]:
    """Enumerate local clock buffers.

    A cell is an LCB iff:
      * its master name matches a CLKBUF/CLKINV hint;
      * its single output net's sinks are **all** sequential clock pins
        (no downstream clock buffer on the net).
    """
    out: List[object] = []
    for inst in block.getInsts():
        try:
            master = inst.getMaster()
        except Exception:
            continue
        if not _master_is_clock_buffer(master):
            continue
        out_net = _inst_single_output_net(inst)
        if out_net is None:
            continue
        sinks = [it for it in out_net.getITerms() if it.getIoType() == "INPUT"]
        if not sinks:
            continue
        if not all(_iterm_is_sequential_clock_sink(it) for it in sinks):
            continue
        out.append(inst)
    out.sort(key=lambda c: c.getName())
    return out


def lcb_output_net(lcb) -> object:
    net = _inst_single_output_net(lcb)
    if net is None:
        raise RuntimeError(f"LCB {lcb.getName()} has no unique output net")
    return net


def lcb_parent(lcb) -> Optional[object]:
    """Return the instance driving the LCB's clock input net, if any."""
    in_net = _inst_single_input_net(lcb)
    if in_net is None:
        return None
    drv = _net_driver_iterm(in_net)
    if drv is None:
        return None
    return drv.getInst()


def lcb_fanout(lcb) -> int:
    net = lcb_output_net(lcb)
    return sum(1 for it in net.getITerms() if it.getIoType() == "INPUT")


def lcb_fanout_ff_iterms(lcb) -> List[object]:
    net = lcb_output_net(lcb)
    out: List[object] = []
    for it in net.getITerms():
        if _iterm_is_sequential_clock_sink(it):
            out.append(it)
    return out


# ---------------------------------------------------------------------------
# LCB pairs (proximity-based)
# ---------------------------------------------------------------------------

def build_proximity_pairs(
    lcbs: Sequence[object], max_dist_dbu: float
) -> List[Tuple[str, object, object]]:
    """Return ``(pair_key, L_A, L_B)`` for every LCB pair within distance.

    Any two LCBs whose centroids are within ``max_dist_dbu`` Manhattan
    distance form a candidate pair.  The incremental timing check after
    reassignment is the real safety net; no parent-sharing requirement.
    Pairs are ordered deterministically (L_A.name < L_B.name).
    """
    sorted_lcbs = sorted(lcbs, key=lambda c: c.getName())
    out: List[Tuple[str, object, object]] = []
    for i in range(len(sorted_lcbs)):
        ca = inst_center(sorted_lcbs[i])
        na = sorted_lcbs[i].getName()
        for j in range(i + 1, len(sorted_lcbs)):
            cb = inst_center(sorted_lcbs[j])
            if manhattan(ca, cb) > max_dist_dbu:
                continue
            nb = sorted_lcbs[j].getName()
            pair_key = f"{na}+{nb}"
            out.append((pair_key, sorted_lcbs[i], sorted_lcbs[j]))
    return out


# ---------------------------------------------------------------------------
# Fanout / slew headroom filters
# ---------------------------------------------------------------------------

def _liberty_max_fanout(design, master_name: str, default: int) -> int:
    """Best-effort Liberty ``max_fanout`` lookup via OpenSTA; fall back to default."""
    try:
        s = design.evalTclString(
            f"sta::liberty_cell_property [get_lib_cells {master_name}] max_fanout"
        )
        v = float(str(s).strip())
        if v > 0:
            return int(v)
    except Exception:
        pass
    return int(default)


def lcb_max_fanout(design, lcb, default: int) -> int:
    return _liberty_max_fanout(design, lcb.getMaster().getName(), default)


def _liberty_max_transition(design, master_name: str, default_ns: float) -> float:
    """Best-effort Liberty ``max_transition`` (seconds) lookup."""
    try:
        s = design.evalTclString(
            f"sta::liberty_cell_property [get_lib_cells {master_name}] max_transition"
        )
        v = float(str(s).strip())
        if v > 0:
            return v
    except Exception:
        pass
    return float(default_ns) * 1e-9


def lcb_max_transition_s(design, lcb, default_ns: float) -> float:
    return _liberty_max_transition(
        design, lcb.getMaster().getName(), default_ns
    )


# ---------------------------------------------------------------------------
# Timing / incremental parasitics
# ---------------------------------------------------------------------------

def estimate_parasitics(design) -> Optional[str]:
    try:
        design.evalTclString("estimate_parasitics -placement")
        return "placement"
    except Exception as e:
        return f"error:{e}"


def _make_timing(design):
    try:
        from openroad import Timing
        return Timing(design)
    except Exception:
        return None


def lcb_output_slew_s(design, lcb) -> Optional[float]:
    """Return max pin slew (seconds) at the LCB output iterm, or None."""
    t = _make_timing(design)
    if t is None:
        return None
    try:
        rise = getattr(t, "Rise", None) or getattr(type(t), "Rise", None)
        fall = getattr(t, "Fall", None) or getattr(type(t), "Fall", None)
        tmax = getattr(t, "Max", None) or getattr(type(t), "Max", None)
    except Exception:
        rise = fall = tmax = None

    try:
        out_it = None
        for it in lcb.getITerms():
            if it.getIoType() == "OUTPUT":
                out_it = it
                break
        if out_it is None:
            return None
    except Exception:
        return None

    best: Optional[float] = None
    for edge in (rise, fall):
        if edge is None:
            continue
        try:
            s = t.getPinSlew(out_it, edge, tmax) if tmax is not None else t.getPinSlew(out_it, edge)
        except Exception:
            try:
                s = t.getPinSlew(out_it)
            except Exception:
                continue
        try:
            sf = float(s)
        except (TypeError, ValueError):
            continue
        if math.isnan(sf) or math.isinf(sf):
            continue
        if best is None or sf > best:
            best = sf
    return best


def worst_clock_skew_s(design) -> Optional[float]:
    """Return worst setup clock skew in seconds using the STA internal API.

    ``sta::worst_clk_skew_cmd`` returns the numeric skew value directly
    (in the library time unit) instead of printing a report to stdout.
    """
    try:
        raw = design.evalTclString("sta::worst_clk_skew_cmd setup 0")
        v = float(str(raw).strip())
    except Exception:
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    # sta::worst_clk_skew_cmd returns the value in seconds (SI).
    return v


# ---------------------------------------------------------------------------
# Rewiring
# ---------------------------------------------------------------------------

def rewire_iterm_to_net(iterm, new_net) -> None:
    """Disconnect ``iterm`` from its current net and connect to ``new_net``."""
    try:
        iterm.disconnect()
    except Exception:
        pass
    iterm.connect(new_net)


# ---------------------------------------------------------------------------
# Binomial coincidence (p = 0.5 for parity)
# ---------------------------------------------------------------------------

def binomial_pc(num_constraints: int, num_failures: int, p: float = 0.5) -> float:
    """P(at most ``num_failures`` unsatisfied) under i.i.d. P(success)=p."""
    x = num_failures
    X = num_constraints
    total = 0.0
    for i in range(0, x + 1):
        total += math.comb(X, i) * (p ** (X - i)) * ((1.0 - p) ** i)
    return total
