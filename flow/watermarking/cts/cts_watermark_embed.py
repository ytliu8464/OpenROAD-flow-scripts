#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Embed CTS fanout-parity watermarks on a post-TritonCTS OpenDB.

Strategy
--------
1. Load post-CTS ODB.
2. Enumerate local clock buffers (LCBs) whose output net's sinks are all
   sequential clock pins.
3. Build proximity-based LCB pairs (any two LCBs within distance
   threshold) and filter by fanout headroom and output slew headroom.
4. For each candidate pair compute boundary flip-flops: FFs assigned to
   L_A whose (d_B - d_A) <= delta.
5. PRNG-select ``WM_CTS_NUM_PAIRS`` pairs using the 32B seed from
   ``gen_key/``; each pair's ``target_bit`` / ``target_lcb_is_A`` come
   from a pair-scoped HMAC so order changes do not move bits.
6. If fanout(L_target) %% 2 != target_bit, reassign boundary FFs from the
   source LCB to the target LCB until parity flips. Up to 3 attempts
   per pair with incremental timing + revert on skew/slew failure.
7. Mark watermark LCBs as ``doNotTouch`` / FIRM and write CSV ground truth.
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
        # No STA available -> don't block.
        return True, None, mt
    return ((mt - s) / mt >= headroom_frac), s, mt


def _boundary_ffs(
    lcb_a, lcb_b, delta_dbu: float
) -> List[Tuple[object, float, float, float]]:
    """Return ``[(ff_clk_iterm, d_a, d_b, d_b - d_a)]`` sorted by ``d_b - d_a``.

    Only FFs currently driven by ``lcb_a`` are considered. Boundary iff
    ``d_b - d_a <= delta_dbu``.
    """
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


def _incremental_timing_ok(
    design,
    lcb_src,
    lcb_dst,
    base_skew_s: Optional[float],
    skew_slack_s: float,
    headroom_frac: float,
    max_trans_default_ns: float,
) -> Tuple[bool, str]:
    cc.estimate_parasitics(design)
    # Slew check on both LCB outputs.
    for lcb in (lcb_src, lcb_dst):
        s = cc.lcb_output_slew_s(design, lcb)
        mt = cc.lcb_max_transition_s(design, lcb, max_trans_default_ns)
        if s is not None and s > mt:
            return False, f"slew_exceeded:{lcb.getName()}:{s:.3e}>{mt:.3e}"
        if s is not None and headroom_frac > 0.0:
            if (mt - s) / mt < -0.0:  # accept at-limit; only hard fail is s>mt
                pass
    # Clock skew check.
    if base_skew_s is not None:
        new_skew = cc.worst_clock_skew_s(design)
        if new_skew is not None:
            if abs(new_skew) - abs(base_skew_s) > skew_slack_s:
                return False, (
                    f"skew_grew:{abs(new_skew):.3e}-{abs(base_skew_s):.3e}"
                    f">{skew_slack_s:.3e}"
                )
    return True, "ok"


def _try_flip_parity(
    design,
    pair_src_lcb,
    pair_dst_lcb,
    target_bit: int,
    delta_dbu: float,
    base_skew_s: Optional[float],
    skew_slack_s: float,
    headroom_frac: float,
    max_trans_default_ns: float,
    max_attempts: int,
) -> Tuple[int, int, str]:
    """Return (num_reassigned, attempts, skipped_reason).

    If ``fanout(pair_src_lcb) %% 2 == target_bit`` initially, no-op.
    Otherwise reassign closest-boundary FFs from src -> dst to flip parity,
    rolling back each attempt that fails incremental timing. Up to
    ``max_attempts`` tries.
    """
    cur_bit = cc.lcb_fanout(pair_src_lcb) % 2
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
        ff_it, d_a, d_b, diff = boundary[0]
        tried.add(id(ff_it))
        attempts += 1

        cc.rewire_iterm_to_net(ff_it, dst_net)

        ok, reason = _incremental_timing_ok(
            design, pair_src_lcb, pair_dst_lcb, base_skew_s,
            skew_slack_s, headroom_frac, max_trans_default_ns,
        )
        if ok and (cc.lcb_fanout(pair_src_lcb) % 2) == target_bit:
            return 1, attempts, ""
        # revert
        cc.rewire_iterm_to_net(ff_it, src_net)
        if not ok:
            continue
    return 0, attempts, "timing_or_parity_failed_after_retries"


def _write_pairs_csv(path: str, rows: Sequence[Dict[str, object]]) -> None:
    header = [
        "pair_idx", "pair_key", "L_A", "L_B", "target_lcb", "other_lcb",
        "target_bit", "final_bit",
        "fanout_target_before", "fanout_target_after",
        "fanout_other_before", "fanout_other_after",
        "num_boundary_ffs", "num_reassigned", "attempts", "skipped_reason",
    ]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow([r.get(k, "") for k in header])


def main() -> int:
    p = argparse.ArgumentParser(description="Embed CTS fanout-parity watermark")
    p.add_argument("--input", default=os.environ.get("WM_CTS_INPUT"))
    p.add_argument("--output-odb", default=os.environ.get("WM_CTS_OUTPUT_ODB"))
    p.add_argument("--output-csv", default=os.environ.get("WM_CTS_OUTPUT_CSV"))
    p.add_argument(
        "--seed-hex", default=os.environ.get("WM_SEED_HEX"),
        help="Path to seed_cts.hex produced by gen_key/",
    )
    p.add_argument(
        "--num-pairs", type=int,
        default=int(os.environ.get("WM_CTS_NUM_PAIRS", "32")),
    )
    p.add_argument(
        "--sibling-dist-um", type=float,
        default=float(os.environ.get("WM_CTS_SIBLING_DIST_UM", "20")),
    )
    p.add_argument(
        "--delta-sites", type=float,
        default=float(os.environ.get("WM_CTS_DELTA_SITES", "2")),
    )
    p.add_argument(
        "--fanout-margin", type=int,
        default=int(os.environ.get("WM_CTS_FANOUT_MARGIN", "2")),
    )
    p.add_argument(
        "--slew-headroom-frac", type=float,
        default=float(os.environ.get("WM_CTS_SLEW_HEADROOM_FRAC", "0.20")),
    )
    p.add_argument(
        "--skew-slack-ps", type=float,
        default=float(os.environ.get("WM_CTS_SKEW_SLACK_PS", "20")),
    )
    p.add_argument(
        "--max-fanout", type=int,
        default=int(os.environ.get("WM_CTS_MAX_FANOUT", "32")),
    )
    p.add_argument(
        "--max-transition-ns", type=float,
        default=float(os.environ.get("WM_CTS_MAX_TRANSITION_NS", "0.4")),
    )
    p.add_argument(
        "--max-attempts", type=int,
        default=int(os.environ.get("WM_CTS_MAX_ATTEMPTS", "3")),
    )
    p.add_argument(
        "--sdc", default=os.environ.get("WM_SDC", ""),
    )
    p.add_argument(
        "--lib-files", default=os.environ.get("WM_LIB_FILES", ""),
        help="Space-separated liberty file paths for STA.",
    )
    p.add_argument(
        "--setrc", default=os.environ.get("WM_SETRC", ""),
        help="Path to platform setRC.tcl for wire RC estimation.",
    )
    args = p.parse_args(cc.argv_after_openroad_driver())

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

    lib_paths = [p for p in args.lib_files.split() if p.strip()]
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
        print("[cts_wm_embed] WARNING: no liberty files loaded; "
              "timing checks will be best-effort")

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

    # Baseline parasitics + skew snapshot.
    cc.estimate_parasitics(design)
    base_skew_s = cc.worst_clock_skew_s(design)
    print(
        f"[cts_wm_embed] baseline worst |skew| = "
        f"{(abs(base_skew_s) if base_skew_s is not None else float('nan')):.3e} s"
    )

    pitch = cc.site_pitch_dbu(block)
    delta_dbu = float(args.delta_sites) * float(pitch)
    sibling_dist_dbu = float(design.micronToDBU(args.sibling_dist_um))
    skew_slack_s = args.skew_slack_ps * 1e-12

    lcbs = cc.collect_lcbs(block)
    print(f"[cts_wm_embed] LCBs discovered: {len(lcbs)}")
    if not lcbs:
        print("[cts_wm_embed] no LCBs found; nothing to do", file=sys.stderr)
        return 1

    all_pairs = cc.build_proximity_pairs(lcbs, sibling_dist_dbu)
    print(
        f"[cts_wm_embed] proximity pairs (within "
        f"{args.sibling_dist_um:.2f} um): {len(all_pairs)}"
    )

    # Apply headroom filters and keep pairs where BOTH LCBs pass.
    candidates: List[Tuple[str, object, object]] = []
    dropped_fanout = 0
    dropped_slew = 0
    for pair_key, la, lb in all_pairs:
        ok_a, f_a, mf_a = _fanout_headroom_ok(
            design, la, args.fanout_margin, args.max_fanout
        )
        ok_b, f_b, mf_b = _fanout_headroom_ok(
            design, lb, args.fanout_margin, args.max_fanout
        )
        if not (ok_a and ok_b):
            dropped_fanout += 1
            continue
        ok_sa, s_a, mt_a = _slew_headroom_ok(
            design, la, args.slew_headroom_frac, args.max_transition_ns
        )
        ok_sb, s_b, mt_b = _slew_headroom_ok(
            design, lb, args.slew_headroom_frac, args.max_transition_ns
        )
        if not (ok_sa and ok_sb):
            dropped_slew += 1
            continue
        candidates.append((pair_key, la, lb))
    print(
        f"[cts_wm_embed] candidate pairs after filters: {len(candidates)} "
        f"(dropped fanout={dropped_fanout} slew={dropped_slew})"
    )
    if not candidates:
        print("[cts_wm_embed] no candidate pairs survived filters", file=sys.stderr)
        return 1

    # Deterministic RNG for pair sampling.
    rng = cc.master_rng(seed, b"cts_pairs")
    n_pick = min(args.num_pairs, len(candidates))
    if n_pick < args.num_pairs:
        print(
            f"[cts_wm_embed] WARNING: requested {args.num_pairs} pairs but "
            f"only {len(candidates)} candidates available"
        )
    selected = rng.sample(candidates, n_pick)

    rows: List[Dict[str, object]] = []
    satisfied = 0
    for idx, (pair_key, la, lb) in enumerate(selected):
        target_bit, target_is_a = cc.pair_bits(
            seed, pair_key, la.getName(), lb.getName()
        )
        target_lcb = la if target_is_a == 1 else lb
        other_lcb = lb if target_is_a == 1 else la

        f_t_before = cc.lcb_fanout(target_lcb)
        f_o_before = cc.lcb_fanout(other_lcb)

        boundary = _boundary_ffs(target_lcb, other_lcb, delta_dbu)
        n_boundary = len(boundary)

        if f_t_before % 2 == target_bit:
            # Already matches; nothing to reassign.
            num_reassigned, attempts, skipped = 0, 0, ""
        else:
            num_reassigned, attempts, skipped = _try_flip_parity(
                design,
                target_lcb, other_lcb,
                target_bit, delta_dbu, base_skew_s, skew_slack_s,
                args.slew_headroom_frac, args.max_transition_ns,
                args.max_attempts,
            )

        f_t_after = cc.lcb_fanout(target_lcb)
        f_o_after = cc.lcb_fanout(other_lcb)
        final_bit = f_t_after % 2
        if final_bit == target_bit and not skipped:
            satisfied += 1
            _mark_fixed(target_lcb)
            _mark_fixed(other_lcb)

        rows.append({
            "pair_idx": idx,
            "pair_key": pair_key,
            "L_A": la.getName(),
            "L_B": lb.getName(),
            "target_lcb": target_lcb.getName(),
            "other_lcb": other_lcb.getName(),
            "target_bit": target_bit,
            "final_bit": final_bit,
            "fanout_target_before": f_t_before,
            "fanout_target_after": f_t_after,
            "fanout_other_before": f_o_before,
            "fanout_other_after": f_o_after,
            "num_boundary_ffs": n_boundary,
            "num_reassigned": num_reassigned,
            "attempts": attempts,
            "skipped_reason": skipped,
        })

    fail = len(rows) - satisfied
    pc = cc.binomial_pc(len(rows), fail)

    if args.output_csv:
        out_dir = os.path.dirname(os.path.abspath(args.output_csv))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        _write_pairs_csv(args.output_csv, rows)
        print(f"[cts_wm_embed] pair list -> {args.output_csv}")

    design.writeDb(args.output_odb)
    print(
        f"[cts_wm_embed] pairs={len(rows)} satisfied={satisfied} failed={fail} "
        f"Pc(p=1/2)<={pc:.3e} -> {args.output_odb}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
