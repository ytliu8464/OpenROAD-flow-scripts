#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Filter the unfiltered E_C CSV down to the embedder's *true* pre-attempt
feasible set, using the same electrical + HMAC checks the embedder applies.

Reads (env):
  WM_ODB        watermarked 4_cts_wm.odb (the leaked layout)
  WM_LIB_FILES  space-separated Liberty paths
  WM_SDC        post-CTS SDC for the design
  WM_SETRC      platform setRC.tcl
  WM_SEED_HEX   seed_cts.hex (used in the embedder's HMAC feasibility check)
  WM_FEAT_IN    input feature CSV (object_id, label, <features...>)
  WM_FEAT_OUT   output filtered CSV (same schema)

Optional knobs (defaults match the embedder; see cts_watermark_embed.main()):
  WM_CTS_FANOUT_MARGIN, WM_CTS_MAX_FANOUT
  WM_CTS_SLEW_HEADROOM_FRAC, WM_CTS_QL_SLEW_HEADROOM_FRAC
  WM_CTS_MAX_TRANSITION_NS
  WM_CTS_QL_CAP_HEADROOM_FRAC, WM_CTS_MAX_CAP_FF
  WM_CTS_DELTA_SITES, WM_CTS_RMAX

For each (object_id = "L_A+L_B") in WM_FEAT_IN:
  1. Apply the electrical filter from _filter_candidate_pairs:
       - fanout headroom on both LCBs
       - slew headroom on both LCBs (quasi pairs use the stricter ql_ value)
       - cap headroom on both LCBs (quasi pairs only)
  2. Apply the HMAC pre-attempt feasibility check from
     _count_pre_attempt_feasible: zero-edit success OR boundary FF available.
  3. Keep the row iff both filters pass.

The committed positives (label==1) are always kept; the embedder, by
construction, only marks pairs that pass both filters.  Negatives that fail
either filter could not have been chosen by the embedder regardless of the
secret key, so they must not be in the reconstructed E_C.

Output stderr line:
  [filter_cts] total=N kept=K elec_rej=A hmac_rej=B unknown=U
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

import openroad as ord_

_HERE = Path(__file__).resolve().parent
_WM_ROOT = _HERE.parents[2]
sys.path.insert(0, str(_WM_ROOT / "cts_v2"))
import cts_watermark_common as cc  # type: ignore


# ---------------------------------------------------------------------------
# Embedder filter helpers (ported from cts_watermark_embed.py to avoid
# importing the embedder module, which pulls in argparse / a CLI we do
# not want here).
# ---------------------------------------------------------------------------

def _fanout_headroom_ok(design, lcb, margin: int, max_default: int) -> bool:
    mf = cc.lcb_max_fanout(design, lcb, max_default)
    f = cc.lcb_fanout(lcb)
    return (f + margin < mf)


def _slew_headroom_ok(design, lcb, headroom_frac: float, max_trans_default_ns: float) -> bool:
    mt = cc.lcb_max_transition_s(design, lcb, max_trans_default_ns)
    s = cc.lcb_output_slew_s(design, lcb)
    if s is None or mt is None or mt <= 0:
        return True
    return ((mt - s) / mt >= headroom_frac)


def _cap_headroom_ok(design, lcb, cap_headroom_frac: float, default_cap_ff: float) -> bool:
    cap_max = cc.lcb_max_capacitance_s(design, lcb, default_cap_ff)
    c = cc.lcb_output_load_cap_s(design, lcb)
    if c is None or cap_max is None or cap_max <= 0:
        return True
    limit = (1.0 - cap_headroom_frac) * cap_max
    return (c <= limit)


def _has_boundary_ff(la, lb, delta_dbu: float) -> bool:
    ca = cc.inst_center(la)
    cb = cc.inst_center(lb)
    try:
        for it in cc.lcb_fanout_ff_iterms(la):
            inst = it.getInst()
            if inst is None:
                continue
            cf = cc.inst_center(inst)
            d_a = cc.manhattan(cf, ca)
            d_b = cc.manhattan(cf, cb)
            if (d_b - d_a) <= delta_dbu:
                return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    in_odb = os.environ["WM_ODB"]
    lib_files = [lp for lp in os.environ.get("WM_LIB_FILES", "").split() if lp.strip()]
    sdc = os.environ.get("WM_SDC", "")
    setrc = os.environ.get("WM_SETRC", "")
    seed_path = os.environ["WM_SEED_HEX"]
    feat_in = Path(os.environ["WM_FEAT_IN"])
    feat_out = Path(os.environ["WM_FEAT_OUT"])

    fanout_margin = int(os.environ.get("WM_CTS_FANOUT_MARGIN", "2"))
    max_fanout = int(os.environ.get("WM_CTS_MAX_FANOUT", "32"))
    slew_headroom_frac = float(os.environ.get("WM_CTS_SLEW_HEADROOM_FRAC", "0.20"))
    ql_slew_headroom_frac_env = os.environ.get("WM_CTS_QL_SLEW_HEADROOM_FRAC")
    if ql_slew_headroom_frac_env and ql_slew_headroom_frac_env.strip():
        ql_slew_headroom_frac = float(ql_slew_headroom_frac_env)
    else:
        ql_slew_headroom_frac = max(0.30, slew_headroom_frac + 0.10)
    max_transition_ns = float(os.environ.get("WM_CTS_MAX_TRANSITION_NS", "0.4"))
    ql_cap_headroom_frac = float(os.environ.get("WM_CTS_QL_CAP_HEADROOM_FRAC", "0.20"))
    default_cap_ff = float(os.environ.get("WM_CTS_MAX_CAP_FF", "50"))
    delta_sites = float(os.environ.get("WM_CTS_DELTA_SITES", "2"))
    r_max = int(os.environ.get("WM_CTS_RMAX", "5"))

    # --- design + STA load ----------------------------------------------------
    tech = ord_.Tech()
    design = ord_.Design(tech)
    sys.stderr.write(f"[filter_cts] reading {in_odb}\n")
    design.readDb(in_odb)
    block = design.getBlock()

    libs_loaded = 0
    for lp in lib_files:
        try:
            design.evalTclString(f'read_liberty "{lp}"')
            libs_loaded += 1
        except Exception as e:
            sys.stderr.write(f"[filter_cts] read_liberty warning: {lp}: {e}\n")
    sys.stderr.write(f"[filter_cts] loaded {libs_loaded} liberty file(s)\n")

    if sdc:
        try:
            design.evalTclString(f'read_sdc "{sdc}"')
        except Exception as e:
            sys.stderr.write(f"[filter_cts] read_sdc warning: {e}\n")
    if setrc:
        try:
            design.evalTclString(f'source "{setrc}"')
        except Exception as e:
            sys.stderr.write(f"[filter_cts] setRC warning: {e}\n")
    cc.estimate_parasitics(design)

    # --- LCB classification (so we know which thresholds to apply) ------------
    classified = cc.classify_lcbs(block, r_max=r_max)
    pure_set = {c.getName() for c in classified["pure"]}
    quasi_set = {c.getName() for c in classified["quasi_leaf"]}

    # --- seed + boundary FF window -------------------------------------------
    seed = cc.load_seed_hex(seed_path)
    pitch = cc.site_pitch_dbu(block)
    delta_dbu = float(delta_sites) * float(pitch)

    by_name = {inst.getName(): inst for inst in block.getInsts()}

    # --- per-LCB memoization (each LCB appears in many pairs) ----------------
    _fan_ok_cache: dict = {}
    _slew_ok_cache: dict = {}
    _cap_ok_cache: dict = {}

    def fan_ok(lcb):
        nm = lcb.getName()
        if nm not in _fan_ok_cache:
            _fan_ok_cache[nm] = _fanout_headroom_ok(design, lcb, fanout_margin, max_fanout)
        return _fan_ok_cache[nm]

    def slew_ok(lcb, frac):
        # Slew memo keys on the frac too -- ql_ uses a stricter value.
        key = (lcb.getName(), frac)
        if key not in _slew_ok_cache:
            _slew_ok_cache[key] = _slew_headroom_ok(design, lcb, frac, max_transition_ns)
        return _slew_ok_cache[key]

    def cap_ok(lcb):
        nm = lcb.getName()
        if nm not in _cap_ok_cache:
            _cap_ok_cache[nm] = _cap_headroom_ok(design, lcb, ql_cap_headroom_frac, default_cap_ff)
        return _cap_ok_cache[nm]

    # --- main filter loop -----------------------------------------------------
    total = 0
    kept = 0
    pos_kept = 0
    pos_total = 0
    elec_rej = 0
    hmac_rej = 0
    unknown = 0

    feat_out.parent.mkdir(parents=True, exist_ok=True)
    with open(feat_in) as fin, open(feat_out, "w", newline="") as fout:
        reader = csv.reader(fin)
        writer = csv.writer(fout)
        header = next(reader)
        writer.writerow(header)

        for row in reader:
            total += 1
            pair_key = row[0]
            try:
                label = int(row[1])
            except ValueError:
                label = 0
            if label == 1:
                pos_total += 1

            # object_id format is "name_a+name_b" (see dump_features.dump_cts).
            parts = pair_key.split("+")
            if len(parts) != 2:
                unknown += 1
                continue
            name_a, name_b = parts
            la = by_name.get(name_a)
            lb = by_name.get(name_b)
            if la is None or lb is None:
                unknown += 1
                continue

            # Channel classification.  Mirrors the embedder: quasi_quasi and
            # pure_quasi cross pairs use the stricter ql_ thresholds, plus the
            # cap headroom check (which pure_pure pairs skip).
            a_quasi = name_a in quasi_set
            b_quasi = name_b in quasi_set
            is_quasi_pair = a_quasi and b_quasi
            is_cross = (a_quasi != b_quasi) and (name_a in pure_set or name_b in pure_set
                                                 or a_quasi or b_quasi)
            use_ql = is_quasi_pair or is_cross
            slew_frac = ql_slew_headroom_frac if use_ql else slew_headroom_frac
            check_cap = use_ql

            # 1. Electrical filter
            if not fan_ok(la) or not fan_ok(lb):
                elec_rej += 1
                continue
            if not slew_ok(la, slew_frac) or not slew_ok(lb, slew_frac):
                elec_rej += 1
                continue
            if check_cap and (not cap_ok(la) or not cap_ok(lb)):
                elec_rej += 1
                continue

            # 2. HMAC pre-attempt feasibility
            target_bit, target_is_a = cc.pair_bits(seed, pair_key, name_a, name_b)
            target_lcb = la if target_is_a == 1 else lb
            other_lcb = lb if target_is_a == 1 else la
            try:
                seq_fo = cc.lcb_sink_breakdown(target_lcb)["seq"]
            except Exception:
                seq_fo = 0
            zero_edit = ((seq_fo % 2) == target_bit)
            if not zero_edit and not _has_boundary_ff(target_lcb, other_lcb, delta_dbu):
                hmac_rej += 1
                continue

            writer.writerow(row)
            kept += 1
            if label == 1:
                pos_kept += 1

    sys.stderr.write(
        f"[filter_cts] total={total} kept={kept} (pos {pos_kept}/{pos_total}) "
        f"elec_rej={elec_rej} hmac_rej={hmac_rej} unknown={unknown}\n")
    return 0


sys.exit(main())
