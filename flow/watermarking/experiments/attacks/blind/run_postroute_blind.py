#!/usr/bin/env python3.11
# SPDX-License-Identifier: BSD-3-Clause
"""Chained blind ALL-STAGE attack + post-route PPA (keeps the watermarked flow).

Unlike run_blind_attack.py (which attacks each stage independently for the
extraction curves), this orchestrator produces ONE post-route layout in which
all three carriers are attacked:

  1. placement : swap q_s% of the eligible co-row tuples ON the owner's
                 watermarked 4_cts_wm.odb (its cell positions carry the
                 placement mark), then re-legalize.
  2. cts       : move q_s% of the eligible LCB-pair sinks on that same ODB
                 (its clock tree carries the CTS mark).
  3. routing   : re-route the watermark nets.  On NanGate45 this uses
                 run_attack_route.sh (clears the wrong-way bias on the
                 q_s% attack subset and re-runs route + finish).  ASAP7 has no
                 routing channel, so it just runs standard route + finish.

Result: a finished (6_report.json) layout with placement + CTS perturbed and
the routing watermark re-routed -- i.e. the real all-stage blind attack, not
the "perturb placement, run non-watermarking back end" shortcut.

Usage:
    python3.11 run_postroute_blind.py --platform P --design D --qs Q [--fraction F]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]          # .../watermarking/experiments
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "attacks" / "blind"))
from bench_matrix import ACTIVE_BENCHES
from lib.orfs import FLOW_HOME, experiment_results, experiment_logs, load_experiment_metrics
from run_blind_attack import (or_python, _run_logged, _pick_embed_dir,
                              _route_counts_csv, _log_path)

RAW = HERE / "results" / "phase3" / "raw"
BLIND = HERE / "attacks" / "blind"
NO_ROUTE_PLATS = {"asap7"}


def _bench(plat, design):
    return next((x for x in ACTIVE_BENCHES if x.platform == plat and
                (x.design == design or x.design_nickname == design)), None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", required=True)
    ap.add_argument("--design", required=True)
    ap.add_argument("--qs", required=True)
    ap.add_argument("--fraction", type=float, default=0.01,
                    help="routing watermark fraction (matches embed; default 0.01)")
    args = ap.parse_args()
    q = args.qs

    b = _bench(args.platform, args.design)
    if b is None:
        print(f"[err] no bench {args.platform}/{args.design}", file=sys.stderr)
        return 2
    nick = b.design_nickname
    (embed_dir, _p_odb, _p_csv, c_odb, _c_csv) = _pick_embed_dir(
        b.platform, nick, b.wm_flow_variant)
    cts_wm = embed_dir / c_odb                       # owner's watermarked 4_cts
    if not cts_wm.exists():
        print(f"[err] owner watermarked CTS not found: {cts_wm}", file=sys.stderr)
        return 2
    RAW.mkdir(parents=True, exist_ok=True)
    tag = f"{b.platform}_{b.design}_qs{q}"

    # --- 1) placement attack on the watermarked 4_cts ------------------------
    pa = RAW / f"atk_blind_{tag}_place.odb"
    print(f"[1/4] placement attack (swap {q} of co-row tuples) on {cts_wm.name}")
    rc = or_python(BLIND / "attack_placement.py",
                   {"WM_ODB": str(cts_wm), "WM_OUT_ODB": str(pa), "ATK_QS": str(q)},
                   log=_log_path(RAW, b, "blindP", q, "atk"))
    if rc != 0 or not pa.exists():
        print(f"[err] placement attack failed (rc={rc})", file=sys.stderr); return 1

    # --- 2) CTS attack on the placement-attacked ODB -------------------------
    ca = RAW / f"atk_blind_{tag}_cts.odb"
    print(f"[2/4] CTS attack (move {q} of LCB-pair sinks) on {pa.name}")
    rc = or_python(BLIND / "attack_cts.py",
                   {"WM_ODB": str(pa), "WM_OUT_ODB": str(ca), "ATK_QS": str(q)},
                   log=_log_path(RAW, b, "blindC", q, "atk"))
    if rc != 0 or not ca.exists():
        print(f"[err] CTS attack failed (rc={rc})", file=sys.stderr); return 1

    # --- 3) routing: reroute watermark nets (NG45) or standard route (ASAP7) --
    variant = f"atk-blind-{b.design}-qs{q}"
    wm_results = experiment_results(b.platform, nick, variant)
    wm_results.mkdir(parents=True, exist_ok=True)
    ca_abs = str(ca.resolve())

    if b.platform in NO_ROUTE_PLATS:
        print(f"[3/4] no routing channel on {b.platform}; standard route + finish")
        rc = _run_logged(
            ["bash", str(FLOW_HOME / "watermarking" / "cts_v2" / "run_ppa.sh")],
            env={"DESIGN": b.design, "DESIGN_NICKNAME": nick, "PLATFORM": b.platform,
                 "WM_FLOW_VARIANT": b.wm_flow_variant, "FLOW_VARIANT": variant,
                 "WM_RESULTS": str(wm_results), "CTS_ODB": ca_abs},
            log=_log_path(RAW, b, "blindR", q, "route"))
    else:
        print(f"[3/4] reroute {q} of routing watermark nets + finish")
        rc_in = _route_counts_csv(embed_dir, b)
        if rc_in is None:
            print("[err] no route_counts*.csv for routing attack", file=sys.stderr); return 1
        nets = RAW / f"atk_blind_{tag}_nets.txt"
        rcp = _run_logged(["python3.11", str(BLIND / "attack_routing.py")],
                          env={"WM_ROUTE_COUNTS_IN": str(rc_in),
                               "WM_NETS_ATTACK_OUT": str(nets), "ATK_QS": str(q)},
                          log=_log_path(RAW, b, "blindR", q, "pick"))
        if rcp != 0 or not nets.exists():
            print(f"[err] attack_routing.py failed (rc={rcp})", file=sys.stderr); return 1
        rc = _run_logged(
            ["bash", str(FLOW_HOME / "watermarking" / "routing_wrong_way" / "run_attack_route.sh")],
            env={"DESIGN": b.design, "DESIGN_NICKNAME": nick, "PLATFORM": b.platform,
                 "WM_FLOW_VARIANT": b.wm_flow_variant, "FLOW_VARIANT": variant,
                 "WM_RESULTS": str(wm_results), "WM_NETS_ATTACK": str(nets),
                 "CTS_ODB": ca_abs, "WATERMARK_FRACTION": str(args.fraction)},
            log=_log_path(RAW, b, "blindR", q, "route"))
    if rc != 0:
        print(f"[err] routing/finish failed (rc={rc})", file=sys.stderr); return 1

    # --- 4) post-route PPA ---------------------------------------------------
    print(f"[4/4] post-route PPA (all three carriers attacked):")
    try:
        m = load_experiment_metrics(b.platform, nick, variant)
        print(f"      WNS (ns) : {m.wns_ns}")
        print(f"      TNS (ns) : {m.tns_ns}")
        print(f"      power(W) : {m.power_w}")
        print(f"      routed WL: {m.rwl_um}")
    except Exception as e:
        rep = experiment_logs(b.platform, nick, variant) / "6_report.json"
        print(f"      (metrics load failed: {e}); check {rep}")
    print(f"[done] {b.platform}/{b.design} qs={q}  FLOW_VARIANT={variant}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
