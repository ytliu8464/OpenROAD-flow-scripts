#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Embed CTS fanout-parity watermarks on a post-TritonCTS OpenDB.

Two channels (priority: pure LCB, then quasi-leaf):
  * ``pure``: sequential fanout only (classic leaf LCB).
  * ``quasi_leaf``: seq_fanout > 1, repair_fanout <= R_max, other_fanout == 0;
    repair cells are frozen (identity + do-not-touch).

Parity is always ``seq_fanout(target_lcb) % 2``. One successful embed consumes
both LCBs (no reuse across pairs).
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple

from openroad import Design, Tech

import cts_watermark_common as cc


def _fanout_headroom_ok(
    design, lcb, margin: int, max_fanout_default: int
) -> Tuple[bool, int, int]:
    mf = cc.lcb_max_fanout(design, lcb, max_fanout_default)
    f = cc.lcb_fanout(lcb)
    return (f + margin < mf), f, mf


def _slew_headroom_ok(
    design, lcb, headroom_frac: float, max_trans_default_ns: float
) -> Tuple[bool, Optional[float], float]:
    mt = cc.lcb_max_transition_s(design, lcb, max_trans_default_ns)
    s = cc.lcb_output_slew_s(design, lcb)
    if s is None:
        return True, None, mt
    return ((mt - s) / mt >= headroom_frac), s, mt


def _cap_headroom_ok(
    design,
    lcb,
    cap_headroom_frac: float,
    default_cap_ff: float,
) -> Tuple[bool, Optional[float], float]:
    """True if observed cap <= (1 - frac) * liberty max_capacitance."""
    cap_max = cc.lcb_max_capacitance_s(design, lcb, default_cap_ff)
    c = cc.lcb_output_load_cap_s(design, lcb)
    if c is None:
        return True, None, cap_max
    limit = (1.0 - cap_headroom_frac) * cap_max
    return (c <= limit), c, cap_max


def _boundary_ffs(
    lcb_a, lcb_b, delta_dbu: float
) -> List[Tuple[object, float, float, float]]:
    """Boundary FFs on ``lcb_a`` relative to ``lcb_b`` (seq sinks only)."""
    ca = cc.inst_center(lcb_a)
    cb = cc.inst_center(lcb_b)
    out: List[Tuple[object, float, float, float]] = []
    for it in cc.lcb_fanout_ff_iterms(lcb_a):
        try:
            inst = it.getInst()
        except Exception:
            continue
        if inst is None:
            continue
        cf = cc.inst_center(inst)
        d_a = cc.manhattan(cf, ca)
        d_b = cc.manhattan(cf, cb)
        if (d_b - d_a) <= delta_dbu:
            out.append((it, d_a, d_b, d_b - d_a))
    out.sort(key=lambda r: r[3])
    return out


def _mark_fixed(lcb) -> None:
    try:
        lcb.setDoNotTouch(True)
    except Exception:
        pass
    try:
        lcb.setPlacementStatus("FIRM")
    except Exception:
        pass


def _mark_repair_fixed(lcb) -> None:
    for it in cc.lcb_sink_breakdown(lcb)["repair_iterms"]:
        try:
            inst = it.getInst()
            if inst is not None:
                inst.setDoNotTouch(True)
        except Exception:
            pass


def _incremental_timing_ok(
    design,
    lcb_src,
    lcb_dst,
    base_skew_s: Optional[float],
    skew_slack_s: float,
    slew_headroom_frac: float,
    max_trans_default_ns: float,
    enforce_slew_margin: bool,
    cap_headroom_frac: float,
    default_cap_ff: float,
    check_cap_margin: bool,
    base_setup_s: Optional[float],
    base_hold_s: Optional[float],
    tol_setup_s: float,
    tol_hold_s: float,
    check_slack: bool,
) -> Tuple[bool, str]:
    cc.estimate_parasitics(design)
    for lcb in (lcb_src, lcb_dst):
        s = cc.lcb_output_slew_s(design, lcb)
        mt = cc.lcb_max_transition_s(design, lcb, max_trans_default_ns)
        if s is not None and s > mt:
            return False, f"slew_exceeded:{lcb.getName()}:{s:.3e}>{mt:.3e}"
        if enforce_slew_margin and s is not None and slew_headroom_frac > 0.0:
            if (mt - s) / mt < slew_headroom_frac:
                return False, f"slew_margin:{lcb.getName()}"
        if check_cap_margin:
            ok_c, cobs, cmax = _cap_headroom_ok(
                design, lcb, cap_headroom_frac, default_cap_ff
            )
            if not ok_c and cobs is not None:
                return False, f"cap_margin:{lcb.getName()}:{cobs:.3e}"
    if base_skew_s is not None:
        new_skew = cc.worst_clock_skew_s(design)
        if new_skew is not None:
            if abs(new_skew) - abs(base_skew_s) > skew_slack_s:
                return False, (
                    f"skew_grew:{abs(new_skew):.3e}-{abs(base_skew_s):.3e}"
                    f">{skew_slack_s:.3e}"
                )
    if check_slack:
        if base_setup_s is not None:
            ns = cc.worst_setup_slack_s(design)
            if ns is not None and ns < base_setup_s - tol_setup_s:
                return False, "setup_slack"
        if base_hold_s is not None:
            nh = cc.worst_hold_slack_s(design)
            if nh is not None and (base_hold_s - nh) > tol_hold_s:
                return False, "hold_slack"
    return True, "ok"


def _try_flip_parity(
    design,
    pair_src_lcb,
    pair_dst_lcb,
    target_bit: int,
    delta_dbu: float,
    base_skew_s: Optional[float],
    skew_slack_s: float,
    slew_headroom_frac: float,
    max_trans_default_ns: float,
    max_attempts: int,
    enforce_slew_margin: bool,
    cap_headroom_frac: float,
    default_cap_ff: float,
    check_cap_margin: bool,
    base_setup_s: Optional[float],
    base_hold_s: Optional[float],
    tol_setup_s: float,
    tol_hold_s: float,
    check_slack: bool,
    quasi_repair_check: bool,
    sig_src_before: Optional[Tuple[Tuple[str, str, str], ...]],
    sig_dst_before: Optional[Tuple[Tuple[str, str, str], ...]],
) -> Tuple[int, int, str]:
    """Flip ``seq_fanout(pair_src) % 2`` via boundary FF moves src -> dst."""
    cur_bit = cc.lcb_seq_fanout(pair_src_lcb) % 2
    if cur_bit == target_bit:
        return 0, 0, ""

    dst_net = cc.lcb_output_net(pair_dst_lcb)
    src_net = cc.lcb_output_net(pair_src_lcb)

    attempts = 0
    tried: set = set()
    while attempts < max_attempts:
        boundary = _boundary_ffs(pair_src_lcb, pair_dst_lcb, delta_dbu)
        boundary = [row for row in boundary if id(row[0]) not in tried]
        if not boundary:
            return 0, attempts, "no_boundary_ff"
        ff_it, _da, _db, _diff = boundary[0]
        tried.add(id(ff_it))
        attempts += 1

        cc.rewire_iterm_to_net(ff_it, dst_net)

        ok, reason = _incremental_timing_ok(
            design,
            pair_src_lcb,
            pair_dst_lcb,
            base_skew_s,
            skew_slack_s,
            slew_headroom_frac,
            max_trans_default_ns,
            enforce_slew_margin,
            cap_headroom_frac,
            default_cap_ff,
            check_cap_margin,
            base_setup_s,
            base_hold_s,
            tol_setup_s,
            tol_hold_s,
            check_slack,
        )
        if quasi_repair_check and sig_src_before is not None and sig_dst_before is not None:
            if cc.lcb_repair_signature(pair_src_lcb) != sig_src_before:
                cc.rewire_iterm_to_net(ff_it, src_net)
                return 0, attempts, "repair_changed"
            if cc.lcb_repair_signature(pair_dst_lcb) != sig_dst_before:
                cc.rewire_iterm_to_net(ff_it, src_net)
                return 0, attempts, "repair_changed"

        if ok and (cc.lcb_seq_fanout(pair_src_lcb) % 2) == target_bit:
            return 1, attempts, ""
        cc.rewire_iterm_to_net(ff_it, src_net)
        if not ok:
            continue
    return 0, attempts, "timing_or_parity_failed_after_retries"


def _parse_channel_budget(s: str) -> Tuple[str, Optional[int], Optional[int]]:
    """Return (mode, pure_cap, quasi_cap). Modes: auto, pure_only, quasi_only, ratio."""
    raw = (s or "auto").strip().lower()
    if raw in ("", "auto"):
        return ("auto", None, None)
    if raw == "pure_only":
        return ("pure_only", None, None)
    if raw == "quasi_only":
        return ("quasi_only", None, None)
    if ":" in raw:
        a, b = raw.split(":", 1)
        try:
            return ("ratio", int(a.strip()), int(b.strip()))
        except ValueError:
            pass
    return ("auto", None, None)


def _filter_candidate_pairs(
    design,
    pairs: Sequence[Tuple[str, object, object]],
    fanout_margin: int,
    max_fanout: int,
    slew_headroom_frac: float,
    max_transition_ns: float,
    cap_headroom_frac: float,
    default_cap_ff: float,
    check_cap: bool,
    avoid_hold_repair: bool,
    is_quasi: bool,
) -> List[Tuple[str, object, object]]:
    out: List[Tuple[str, object, object]] = []
    for pair_key, la, lb in pairs:
        if avoid_hold_repair and is_quasi:
            if cc.quasi_leaf_has_hold_repair_hint(la) or cc.quasi_leaf_has_hold_repair_hint(lb):
                continue
        ok_a, _fa, _mfa = _fanout_headroom_ok(design, la, fanout_margin, max_fanout)
        ok_b, _fb, _mfb = _fanout_headroom_ok(design, lb, fanout_margin, max_fanout)
        if not (ok_a and ok_b):
            continue
        ok_sa, _sa, _mta = _slew_headroom_ok(
            design, la, slew_headroom_frac, max_transition_ns
        )
        ok_sb, _sb, _mtb = _slew_headroom_ok(
            design, lb, slew_headroom_frac, max_transition_ns
        )
        if not (ok_sa and ok_sb):
            continue
        if check_cap:
            ok_ca, _ca, _cma = _cap_headroom_ok(
                design, la, cap_headroom_frac, default_cap_ff
            )
            ok_cb, _cb, _cmb = _cap_headroom_ok(
                design, lb, cap_headroom_frac, default_cap_ff
            )
            if not (ok_ca and ok_cb):
                continue
        out.append((pair_key, la, lb))
    return out


def _build_cross_channel_pairs(
    pure_lcbs: Sequence[object],
    quasi_lcbs: Sequence[object],
    max_dist_dbu: float,
) -> List[Tuple[str, object, object]]:
    """Return pure-quasi proximity pairs.

    ``L_A`` is kept as the pure LCB and ``L_B`` as the quasi-leaf LCB; pair_key
    is still based on deterministic instance names.
    """
    out: List[Tuple[str, object, object]] = []
    for pure in sorted(pure_lcbs, key=lambda c: c.getName()):
        cp = cc.inst_center(pure)
        np = pure.getName()
        for quasi in sorted(quasi_lcbs, key=lambda c: c.getName()):
            cq = cc.inst_center(quasi)
            if cc.manhattan(cp, cq) > max_dist_dbu:
                continue
            nq = quasi.getName()
            pair_key = f"{np}+{nq}"
            out.append((pair_key, pure, quasi))
    return out


def _build_attempt_queue(
    mode: str,
    pure_pairs: Sequence[Tuple[str, object, object]],
    quasi_pairs: Sequence[Tuple[str, object, object]],
    mixed_pairs: Sequence[Tuple[str, object, object]],
    rng_pure,
    rng_quasi,
    rng_mixed,
) -> List[Tuple[str, Tuple[str, object, object]]]:
    pure_sorted = sorted(pure_pairs, key=lambda x: x[0])
    quasi_sorted = sorted(quasi_pairs, key=lambda x: x[0])
    mixed_sorted = sorted(mixed_pairs, key=lambda x: x[0])
    pure_order = (
        list(pure_sorted)
        if not pure_sorted
        else rng_pure.sample(pure_sorted, len(pure_sorted))
    )
    quasi_order = (
        list(quasi_sorted)
        if not quasi_sorted
        else rng_quasi.sample(quasi_sorted, len(quasi_sorted))
    )
    mixed_order = (
        list(mixed_sorted)
        if not mixed_sorted
        else rng_mixed.sample(mixed_sorted, len(mixed_sorted))
    )
    if mode == "pure_only":
        return [("pure", p) for p in pure_order]
    if mode == "quasi_only":
        return [("quasi_leaf", p) for p in quasi_order]
    if mode == "ratio":
        return [("pure", p) for p in pure_order] + [
            ("quasi_leaf", p) for p in quasi_order
        ] + [
            ("pure_quasi", p) for p in mixed_order
        ]
    # auto
    return [("pure", p) for p in pure_order] + [
        ("quasi_leaf", p) for p in quasi_order
    ] + [
        ("pure_quasi", p) for p in mixed_order
    ]


def _unique_lcb_names(
    pairs: Sequence[Tuple[str, object, object]],
) -> set:
    names = set()
    for _pair_key, la, lb in pairs:
        names.add(la.getName())
        names.add(lb.getName())
    return names


CSV_HEADER = [
    "pair_idx",
    "pair_key",
    "channel",
    "L_A",
    "L_B",
    "target_lcb",
    "other_lcb",
    "target_bit",
    "final_bit",
    "fanout_target_before",
    "fanout_target_after",
    "fanout_other_before",
    "fanout_other_after",
    "seq_fanout_target_before",
    "seq_fanout_target_after",
    "seq_fanout_other_before",
    "seq_fanout_other_after",
    "repair_fanout_target",
    "repair_fanout_other",
    "cap_target",
    "cap_max_target",
    "num_boundary_ffs",
    "num_reassigned",
    "attempts",
    "skipped_reason",
]


def _write_pairs_csv(path: str, rows: Sequence[Dict[str, object]]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_HEADER)
        for r in rows:
            w.writerow([r.get(k, "") for k in CSV_HEADER])


def main() -> int:
    p = argparse.ArgumentParser(description="Embed CTS fanout-parity watermark")
    p.add_argument("--input", default=os.environ.get("WM_CTS_INPUT"))
    p.add_argument("--output-odb", default=os.environ.get("WM_CTS_OUTPUT_ODB"))
    p.add_argument("--output-csv", default=os.environ.get("WM_CTS_OUTPUT_CSV"))
    p.add_argument(
        "--seed-hex",
        default=os.environ.get("WM_SEED_HEX"),
        help="Path to seed_cts.hex produced by gen_key/",
    )
    p.add_argument(
        "--num-pairs",
        type=int,
        default=int(os.environ.get("WM_CTS_NUM_PAIRS", "32")),
    )
    p.add_argument(
        "--sibling-dist-um",
        type=float,
        default=float(os.environ.get("WM_CTS_SIBLING_DIST_UM", "20")),
    )
    p.add_argument(
        "--delta-sites",
        type=float,
        default=float(os.environ.get("WM_CTS_DELTA_SITES", "2")),
    )
    p.add_argument(
        "--fanout-margin",
        type=int,
        default=int(os.environ.get("WM_CTS_FANOUT_MARGIN", "2")),
    )
    p.add_argument(
        "--slew-headroom-frac",
        type=float,
        default=float(os.environ.get("WM_CTS_SLEW_HEADROOM_FRAC", "0.20")),
    )
    p.add_argument(
        "--skew-slack-ps",
        type=float,
        default=float(os.environ.get("WM_CTS_SKEW_SLACK_PS", "20")),
    )
    p.add_argument(
        "--max-fanout",
        type=int,
        default=int(os.environ.get("WM_CTS_MAX_FANOUT", "32")),
    )
    p.add_argument(
        "--max-transition-ns",
        type=float,
        default=float(os.environ.get("WM_CTS_MAX_TRANSITION_NS", "0.4")),
    )
    p.add_argument(
        "--max-capacitance-ff",
        type=float,
        default=float(os.environ.get("WM_CTS_MAX_CAP_FF", "50")),
        help="Fallback Liberty max_capacitance when lookup fails (femtofarads).",
    )
    p.add_argument(
        "--max-attempts",
        type=int,
        default=int(os.environ.get("WM_CTS_MAX_ATTEMPTS", "3")),
    )
    p.add_argument(
        "--sdc",
        default=os.environ.get("WM_SDC", ""),
    )
    p.add_argument(
        "--lib-files",
        default=os.environ.get("WM_LIB_FILES", ""),
        help="Space-separated liberty file paths for STA.",
    )
    p.add_argument(
        "--setrc",
        default=os.environ.get("WM_SETRC", ""),
        help="Path to platform setRC.tcl for wire RC estimation.",
    )
    p.add_argument(
        "--r-max",
        type=int,
        default=int(os.environ.get("WM_CTS_R_MAX", str(cc.R_MAX_DEFAULT))),
    )
    p.add_argument(
        "--ql-slew-headroom-frac",
        type=float,
        default=None,
        help="Quasi-leaf min output slew margin (default: max(0.30, pure+0.10)).",
    )
    p.add_argument(
        "--ql-cap-headroom-frac",
        type=float,
        default=float(os.environ.get("WM_CTS_QL_CAP_HEADROOM_FRAC", "0.20")),
    )
    p.add_argument(
        "--ql-setup-slack-ps",
        type=float,
        default=float(os.environ.get("WM_CTS_QL_SETUP_SLACK_PS", "50")),
    )
    p.add_argument(
        "--ql-hold-slack-ps",
        type=float,
        default=float(os.environ.get("WM_CTS_QL_HOLD_SLACK_PS", "30")),
    )
    p.add_argument(
        "--ql-skew-slack-ps",
        type=float,
        default=None,
        help="Quasi-leaf skew slack vs baseline (default: WM_CTS_QL_SKEW_SLACK_PS or WM_CTS_SKEW_SLACK_PS or 20).",
    )
    p.add_argument(
        "--avoid-hold-repair",
        type=int,
        default=int(os.environ.get("WM_CTS_AVOID_HOLD_REPAIR", "1")),
        choices=(0, 1),
    )
    p.add_argument(
        "--channel-budget",
        default=os.environ.get("WM_CTS_CHANNEL_BUDGET", "auto"),
        help="auto | pure_only | quasi_only | N:M",
    )
    args = p.parse_args(cc.argv_after_openroad_driver())

    if args.ql_slew_headroom_frac is None:
        env_ql = os.environ.get("WM_CTS_QL_SLEW_HEADROOM_FRAC")
        if env_ql is not None and str(env_ql).strip() != "":
            args.ql_slew_headroom_frac = float(env_ql)
        else:
            args.ql_slew_headroom_frac = max(
                0.30, float(args.slew_headroom_frac) + 0.10
            )
    if args.ql_skew_slack_ps is None:
        for key in ("WM_CTS_QL_SKEW_SLACK_PS", "WM_CTS_SKEW_SLACK_PS"):
            v = os.environ.get(key)
            if v is not None and str(v).strip() != "":
                args.ql_skew_slack_ps = float(v)
                break
        else:
            args.ql_skew_slack_ps = 20.0

    if not args.input or not args.output_odb:
        p.error(
            "--input and --output-odb (or WM_CTS_INPUT / WM_CTS_OUTPUT_ODB) required"
        )
    if not args.seed_hex:
        p.error("--seed-hex (or WM_SEED_HEX) is required")

    seed = cc.load_seed_hex(args.seed_hex)
    print(f"[cts_wm_embed] seed <- {args.seed_hex} (len={len(seed)} bytes)")

    tech = Tech()
    design = Design(tech)
    design.readDb(args.input)
    block = design.getBlock()
    if block is None:
        print("No block in database", file=sys.stderr)
        return 1

    lib_paths = [lp for lp in args.lib_files.split() if lp.strip()]
    libs_loaded = 0
    for lp in lib_paths:
        try:
            design.evalTclString(f'read_liberty "{lp}"')
            libs_loaded += 1
        except Exception as e:
            print(f"[cts_wm_embed] read_liberty warning: {lp}: {e}")
    if libs_loaded:
        print(f"[cts_wm_embed] loaded {libs_loaded} liberty file(s)")
    else:
        print(
            "[cts_wm_embed] WARNING: no liberty files loaded; "
            "timing checks will be best-effort"
        )

    if args.sdc:
        try:
            design.evalTclString(f'read_sdc "{args.sdc}"')
            print(f"[cts_wm_embed] read_sdc <- {args.sdc}")
        except Exception as e:
            print(f"[cts_wm_embed] read_sdc warning: {e}")

    if args.setrc:
        try:
            design.evalTclString(f'source "{args.setrc}"')
            print(f"[cts_wm_embed] setRC   <- {args.setrc}")
        except Exception as e:
            print(f"[cts_wm_embed] setRC warning: {e}")

    cc.estimate_parasitics(design)
    base_skew_s = cc.worst_clock_skew_s(design)
    base_setup_s = cc.worst_setup_slack_s(design)
    base_hold_s = cc.worst_hold_slack_s(design)
    print(
        f"[cts_wm_embed] baseline worst |skew| = "
        f"{(abs(base_skew_s) if base_skew_s is not None else float('nan')):.3e} s"
    )

    pitch = cc.site_pitch_dbu(block)
    delta_dbu = float(args.delta_sites) * float(pitch)
    sibling_dist_dbu = float(design.micronToDBU(args.sibling_dist_um))
    skew_slack_s = args.skew_slack_ps * 1e-12
    ql_skew_slack_s = args.ql_skew_slack_ps * 1e-12
    tol_setup_s = args.ql_setup_slack_ps * 1e-12
    tol_hold_s = args.ql_hold_slack_ps * 1e-12
    default_cap_ff = float(args.max_capacitance_ff)

    classified = cc.classify_lcbs(block, args.r_max)
    pure_lcbs = classified["pure"]
    quasi_lcbs = classified["quasi_leaf"]
    print(
        f"[cts_wm_embed] classified LCBs: pure={len(pure_lcbs)} "
        f"quasi_leaf={len(quasi_lcbs)} (r_max={args.r_max})"
    )
    if not pure_lcbs and not quasi_lcbs:
        print("[cts_wm_embed] no pure or quasi_leaf LCBs; nothing to do", file=sys.stderr)
        return 1

    all_pure_pairs = cc.build_proximity_pairs(pure_lcbs, sibling_dist_dbu)
    all_quasi_pairs = cc.build_proximity_pairs(quasi_lcbs, sibling_dist_dbu)
    all_mixed_pairs = _build_cross_channel_pairs(
        pure_lcbs, quasi_lcbs, sibling_dist_dbu
    )
    print(
        f"[cts_wm_embed] proximity pairs pure={len(all_pure_pairs)} "
        f"quasi_leaf={len(all_quasi_pairs)} pure_quasi={len(all_mixed_pairs)} "
        f"(within {args.sibling_dist_um:.2f} um)"
    )

    pure_cand = _filter_candidate_pairs(
        design,
        all_pure_pairs,
        args.fanout_margin,
        args.max_fanout,
        args.slew_headroom_frac,
        args.max_transition_ns,
        args.ql_cap_headroom_frac,
        default_cap_ff,
        check_cap=False,
        avoid_hold_repair=False,
        is_quasi=False,
    )
    quasi_cand = _filter_candidate_pairs(
        design,
        all_quasi_pairs,
        args.fanout_margin,
        args.max_fanout,
        args.ql_slew_headroom_frac,
        args.max_transition_ns,
        args.ql_cap_headroom_frac,
        default_cap_ff,
        check_cap=True,
        avoid_hold_repair=bool(args.avoid_hold_repair),
        is_quasi=True,
    )
    mixed_cand = _filter_candidate_pairs(
        design,
        all_mixed_pairs,
        args.fanout_margin,
        args.max_fanout,
        args.ql_slew_headroom_frac,
        args.max_transition_ns,
        args.ql_cap_headroom_frac,
        default_cap_ff,
        check_cap=True,
        avoid_hold_repair=bool(args.avoid_hold_repair),
        is_quasi=True,
    )
    print(
        f"[cts_wm_embed] candidates after filters: pure={len(pure_cand)} "
        f"quasi_leaf={len(quasi_cand)} pure_quasi={len(mixed_cand)}"
    )
    mode, pure_cap, quasi_cap = _parse_channel_budget(args.channel_budget)
    rng_pure = cc.master_rng(seed, b"cts_pure")
    rng_quasi = cc.master_rng(seed, b"cts_quasi")
    rng_mixed = cc.master_rng(seed, b"cts_pure_quasi")
    attempt_queue = _build_attempt_queue(
        mode, pure_cand, quasi_cand, mixed_cand, rng_pure, rng_quasi, rng_mixed
    )

    if not attempt_queue:
        print("[cts_wm_embed] no candidate pairs in queue", file=sys.stderr)
        return 1

    max_pure_success = len(_unique_lcb_names(pure_cand)) // 2
    max_quasi_success = len(_unique_lcb_names(quasi_cand)) // 2
    all_candidate_names = (
        _unique_lcb_names(pure_cand)
        | _unique_lcb_names(quasi_cand)
        | _unique_lcb_names(mixed_cand)
    )
    max_any_success = len(all_candidate_names) // 2
    if mode == "pure_only":
        max_success = max_pure_success
    elif mode == "quasi_only":
        max_success = max_quasi_success
    elif mode == "ratio" and pure_cap is not None and quasi_cap is not None:
        max_success = min(
            max_any_success,
            min(pure_cap, max_pure_success) + min(quasi_cap, max_quasi_success)
            + len(_unique_lcb_names(mixed_cand)) // 2,
        )
    else:
        max_success = max_any_success
    target_successes = min(args.num_pairs, max_success)
    if target_successes < args.num_pairs:
        print(
            f"[cts_wm_embed] target capped at {target_successes}: "
            f"one-use candidate pools allow at most pure={max_pure_success} "
            f"quasi={max_quasi_success} mixed-enabled={max_any_success} successes"
        )

    used_lcbs: set = set()
    rows: List[Dict[str, object]] = []
    satisfied = 0
    pair_idx = 0
    success_pure = 0
    success_quasi = 0
    success_mixed = 0

    for channel, (pair_key, la, lb) in attempt_queue:
        if satisfied >= target_successes:
            break

        na, nb = la.getName(), lb.getName()
        if na in used_lcbs or nb in used_lcbs:
            rows.append({
                "pair_idx": pair_idx,
                "pair_key": pair_key,
                "channel": channel,
                "L_A": na,
                "L_B": nb,
                "target_lcb": "",
                "other_lcb": "",
                "target_bit": "",
                "final_bit": "",
                "fanout_target_before": "",
                "fanout_target_after": "",
                "fanout_other_before": "",
                "fanout_other_after": "",
                "seq_fanout_target_before": "",
                "seq_fanout_target_after": "",
                "seq_fanout_other_before": "",
                "seq_fanout_other_after": "",
                "repair_fanout_target": "",
                "repair_fanout_other": "",
                "cap_target": "",
                "cap_max_target": "",
                "num_boundary_ffs": "",
                "num_reassigned": "",
                "attempts": "",
                "skipped_reason": "lcb_already_used",
            })
            pair_idx += 1
            continue

        if mode == "ratio" and pure_cap is not None and quasi_cap is not None:
            if channel == "pure" and success_pure >= pure_cap:
                rows.append({
                    "pair_idx": pair_idx,
                    "pair_key": pair_key,
                    "channel": channel,
                    "L_A": na,
                    "L_B": nb,
                    "target_lcb": "",
                    "other_lcb": "",
                    "target_bit": "",
                    "final_bit": "",
                    "fanout_target_before": "",
                    "fanout_target_after": "",
                    "fanout_other_before": "",
                    "fanout_other_after": "",
                    "seq_fanout_target_before": "",
                    "seq_fanout_target_after": "",
                    "seq_fanout_other_before": "",
                    "seq_fanout_other_after": "",
                    "repair_fanout_target": "",
                    "repair_fanout_other": "",
                    "cap_target": "",
                    "cap_max_target": "",
                    "num_boundary_ffs": "",
                    "num_reassigned": "",
                    "attempts": "",
                    "skipped_reason": "channel_budget_pure_cap",
                })
                pair_idx += 1
                continue
            if channel == "quasi_leaf" and success_quasi >= quasi_cap:
                rows.append({
                    "pair_idx": pair_idx,
                    "pair_key": pair_key,
                    "channel": channel,
                    "L_A": na,
                    "L_B": nb,
                    "target_lcb": "",
                    "other_lcb": "",
                    "target_bit": "",
                    "final_bit": "",
                    "fanout_target_before": "",
                    "fanout_target_after": "",
                    "fanout_other_before": "",
                    "fanout_other_after": "",
                    "seq_fanout_target_before": "",
                    "seq_fanout_target_after": "",
                    "seq_fanout_other_before": "",
                    "seq_fanout_other_after": "",
                    "repair_fanout_target": "",
                    "repair_fanout_other": "",
                    "cap_target": "",
                    "cap_max_target": "",
                    "num_boundary_ffs": "",
                    "num_reassigned": "",
                    "attempts": "",
                    "skipped_reason": "channel_budget_quasi_cap",
                })
                pair_idx += 1
                continue

        target_bit, target_is_a = cc.pair_bits(seed, pair_key, na, nb)
        target_lcb = la if target_is_a == 1 else lb
        other_lcb = lb if target_is_a == 1 else la

        is_quasi = channel in ("quasi_leaf", "pure_quasi")
        slew_frac = (
            args.ql_slew_headroom_frac if is_quasi else args.slew_headroom_frac
        )
        skew_use = ql_skew_slack_s if is_quasi else skew_slack_s
        enforce_margin = True
        cap_frac = args.ql_cap_headroom_frac if is_quasi else 0.0
        check_cap = is_quasi
        check_slack = is_quasi and (
            base_setup_s is not None or base_hold_s is not None
        )

        f_t_before = cc.lcb_fanout(target_lcb)
        f_o_before = cc.lcb_fanout(other_lcb)
        st_before = cc.lcb_seq_fanout(target_lcb)
        so_before = cc.lcb_seq_fanout(other_lcb)
        rt_before = cc.lcb_repair_fanout(target_lcb)
        ro_before = cc.lcb_repair_fanout(other_lcb)
        cap_obs = cc.lcb_output_load_cap_s(design, target_lcb)
        cap_mx = cc.lcb_max_capacitance_s(design, target_lcb, default_cap_ff)

        boundary = _boundary_ffs(target_lcb, other_lcb, delta_dbu)
        n_boundary = len(boundary)

        sig_src_before: Optional[Tuple[Tuple[str, str, str], ...]] = None
        sig_dst_before: Optional[Tuple[Tuple[str, str, str], ...]] = None
        if is_quasi:
            sig_src_before = cc.lcb_repair_signature(target_lcb)
            sig_dst_before = cc.lcb_repair_signature(other_lcb)

        if st_before % 2 == target_bit:
            num_reassigned, attempts, skipped = 0, 0, ""
        else:
            num_reassigned, attempts, skipped = _try_flip_parity(
                design,
                target_lcb,
                other_lcb,
                target_bit,
                delta_dbu,
                base_skew_s,
                skew_use,
                slew_frac,
                args.max_transition_ns,
                args.max_attempts,
                enforce_margin,
                cap_frac,
                default_cap_ff,
                check_cap,
                base_setup_s,
                base_hold_s,
                tol_setup_s,
                tol_hold_s,
                check_slack,
                is_quasi,
                sig_src_before,
                sig_dst_before,
            )

        f_t_after = cc.lcb_fanout(target_lcb)
        f_o_after = cc.lcb_fanout(other_lcb)
        st_after = cc.lcb_seq_fanout(target_lcb)
        so_after = cc.lcb_seq_fanout(other_lcb)
        final_bit = st_after % 2

        ok_row = final_bit == target_bit and not skipped
        if ok_row:
            satisfied += 1
            used_lcbs.add(na)
            used_lcbs.add(nb)
            _mark_fixed(target_lcb)
            _mark_fixed(other_lcb)
            if is_quasi:
                _mark_repair_fixed(target_lcb)
                _mark_repair_fixed(other_lcb)
            if channel == "pure":
                success_pure += 1
            elif channel == "quasi_leaf":
                success_quasi += 1
            else:
                success_mixed += 1

        rows.append({
            "pair_idx": pair_idx,
            "pair_key": pair_key,
            "channel": channel,
            "L_A": na,
            "L_B": nb,
            "target_lcb": target_lcb.getName(),
            "other_lcb": other_lcb.getName(),
            "target_bit": target_bit,
            "final_bit": final_bit,
            "fanout_target_before": f_t_before,
            "fanout_target_after": f_t_after,
            "fanout_other_before": f_o_before,
            "fanout_other_after": f_o_after,
            "seq_fanout_target_before": st_before,
            "seq_fanout_target_after": st_after,
            "seq_fanout_other_before": so_before,
            "seq_fanout_other_after": so_after,
            "repair_fanout_target": rt_before,
            "repair_fanout_other": ro_before,
            "cap_target": (
                cap_obs if cap_obs is not None else ""
            ),
            "cap_max_target": cap_mx,
            "num_boundary_ffs": n_boundary,
            "num_reassigned": num_reassigned,
            "attempts": attempts,
            "skipped_reason": skipped,
        })
        pair_idx += 1

    fail = len(rows) - satisfied
    pc = cc.binomial_pc(max(len(rows), 1), fail) if rows else 1.0

    if args.output_csv:
        out_dir = os.path.dirname(os.path.abspath(args.output_csv))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        _write_pairs_csv(args.output_csv, rows)
        print(f"[cts_wm_embed] pair list -> {args.output_csv}")

    design.writeDb(args.output_odb)
    print(
        f"[cts_wm_embed] attempts={len(rows)} satisfied={satisfied} failed={fail} "
        f"pure_succ={success_pure} quasi_succ={success_quasi} "
        f"pure_quasi_succ={success_mixed} "
        f"Pc(p=1/2)<={pc:.3e} -> {args.output_odb}"
    )
    if satisfied < args.num_pairs:
        print(
            f"[cts_wm_embed] WARNING: wanted {args.num_pairs} successes "
            f"but got {satisfied}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
