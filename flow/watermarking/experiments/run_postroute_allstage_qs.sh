#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
#
# ALL-STAGE blind attack (placement + CTS + routing) at a specified q_s, then
# post-route PPA of the removed-watermark, re-implemented design.
#
#   Usage:
#     bash run_postroute_allstage_qs.sh <platform> <design> <q_s>
#
#     platform : nangate45 | asap7
#     design   : jpeg | swerv_wrapper | ariane136 | bp_multi_top | cva6 | ariane
#     q_s      : perturbation fraction, e.g. 0.4
#
#   What it does
#     [1] Attack ALL THREE carriers at q_s (extraction / removal evidence):
#           run_blind_attack.py --stages placement,cts,routing
#           -> r_P (placement), r_C (CTS), Z_R/p_R (routing) in
#              results/phase3/raw/blind_<plat>_<design>_<stage>_qs<q>.json
#         (ASAP7 has no routing channel, so routing is skipped there.)
#     [2] Post-route PPA of the fully-removed design:
#           continue the placement-attacked ODB through the STANDARD back end
#           (place_ordering/run_ppa.sh -> make wm_cts_and_route = copy_inputs
#           + cts + route + finish).  Because CTS and routing are re-run WITHOUT
#           their watermark embedders, both are removed; the placement carrier is
#           already perturbed by the step-1 swap.  So the resulting 6_report.json
#           is the PPA of a design with all three watermarks gone.
#     [3] Report r_P/r_C/Z_R (removal) + absolute post-route PPA + delta vs the
#           watermark-only baseline (aggregate_attack_ppa.py -> blind_ppa.csv).
#
#   NOTE ON MODEL: step 2 re-implements the back end (fresh CTS + route). This is
#   the "perturb placement, re-implement downstream" removal. If instead you want
#   the LAYOUT-PRESERVING routing cost (local rip-up + reroute of only the
#   watermark nets, keeping the stolen placement/CTS), use the routing attack's
#   own 5_route.odb under atk-r-<design>-qs<q> and run `make finish` on it.
#
set -euo pipefail

PLAT="${1:?platform}"; DESIGN="${2:?design}"; QS="${3:?q_s}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
export EXPERIMENTS_HOME="$HERE"

# --- resolve bench metadata + ORFS paths (placement continuation variant) -----
read -r NICK WMFV RESDIR LOGDIR VARIANT < <(python3.11 - "$PLAT" "$DESIGN" "$QS" <<'PY'
import sys
sys.path.insert(0, ".")
from bench_matrix import ACTIVE_BENCHES
from lib.orfs import experiment_results, experiment_logs
plat, design, qs = sys.argv[1:4]
b = next((x for x in ACTIVE_BENCHES
          if x.platform == plat and (x.design == design or x.design_nickname == design)), None)
assert b is not None, f"no bench {plat}/{design} in ACTIVE_BENCHES"
variant = f"atk-p-{b.design}-qs{qs}"     # placement-attacked continuation
print(b.design_nickname, b.wm_flow_variant,
      experiment_results(plat, b.design_nickname, variant),
      experiment_logs(plat, b.design_nickname, variant), variant)
PY
)
echo "[cfg] $PLAT/$DESIGN nick=$NICK wm_variant=$WMFV q_s=$QS -> FLOW_VARIANT=$VARIANT"

# --- [1] attack all three carriers at q_s ------------------------------------
echo "[1/3] blind attack on placement + CTS + routing at q_s=$QS ..."
python3.11 attacks/blind/run_blind_attack.py \
    --stages placement,cts,routing --qs-list "$QS" --designs "$DESIGN"

echo "      extraction after attack (watermark removed if all below threshold):"
python3.11 - "$PLAT" "$DESIGN" "$QS" <<'PY'
import json, sys
from pathlib import Path
plat, design, qs = sys.argv[1:4]
raw = Path("results/phase3/raw")
def val(stage, *keys):
    p = raw / f"blind_{plat}_{design}_{stage}_qs{qs}.json"
    if not p.exists(): return "(none)"
    j = json.loads(p.read_text())
    return "  ".join(f"{k}={j.get(k)}" for k in keys if j.get(k) not in ("", None))
print(f"        placement: {val('placement','r_P')}   (tau_P=0.75)")
print(f"        CTS      : {val('cts','r_C')}   (tau_C=0.75)")
print(f"        routing  : {val('routing','Z_R','p_R')}   (alpha_R=1e-4; NG45 only)")
PY

# --- [2] post-route continuation (standard back end -> removes CTS+routing) ---
ODB="results/phase3/raw/atk_p_${PLAT}_${DESIGN}_qs${QS}.odb"
[ -f "$ODB" ] || ODB=$(ls -t results/phase3/raw/atk_p_${PLAT}_${DESIGN}_qs*.odb 2>/dev/null | head -1 || true)
[ -n "${ODB:-}" ] && [ -f "$ODB" ] || { echo "ERROR: placement-attacked ODB not found"; exit 1; }
ODB_ABS="$(readlink -f "$ODB")"
echo "[2/3] re-implementing CTS + route + finish on $ODB_ABS (HEAVY) ..."
env DESIGN="$DESIGN" DESIGN_NICKNAME="$NICK" PLATFORM="$PLAT" \
    WM_FLOW_VARIANT="$WMFV" FLOW_VARIANT="$VARIANT" WM_RESULTS="$RESDIR" \
    DP_ODB="$ODB_ABS" bash ../place_ordering/run_ppa.sh

# --- [3] report post-route PPA -----------------------------------------------
echo "[3/3] post-route PPA (all watermarks removed):"
python3.11 - "$PLAT" "$NICK" "$VARIANT" "$LOGDIR" <<'PY'
import sys
sys.path.insert(0, ".")
from lib.orfs import load_experiment_metrics
plat, nick, variant, logdir = sys.argv[1:5]
try:
    m = load_experiment_metrics(plat, nick, variant)
    print(f"  WNS (ns)  : {m.wns_ns}")
    print(f"  TNS (ns)  : {m.tns_ns}")
    print(f"  power (W) : {m.power_w}")
    print(f"  routed WL : {m.rwl_um}")
except Exception as e:
    print(f"  (could not load metrics: {e}); check {logdir}/6_report.json")
PY

echo "[agg] deltas vs watermark-only baseline -> results/phase3/blind_ppa.csv"
python3.11 attacks/ppa/aggregate_attack_ppa.py >/dev/null 2>&1 || true
grep -E "^${PLAT},${DESIGN},placement,${QS}," results/phase3/blind_ppa.csv 2>/dev/null \
  || echo "  (no aggregated row yet)"
echo "[done] all-stage post-route PPA for $PLAT/$DESIGN qs=$QS"
