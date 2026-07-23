#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
#
# Run ONE design through the full post-route flow after a blind attack at a
# specified q_s, and report the post-route PPA.
#
#   Usage:
#     bash run_postroute_qs.sh <platform> <design> <q_s> [stage]
# bash run_postroute_qs.sh nangate45 jpeg 0.4 [ok]
# bash run_postroute_qs.sh nangate45 swerv_wrapper 0.7 [ok]
# bash run_postroute_qs.sh nangate45 ariane136 0.4 [ok]
# bash run_postroute_qs.sh nangate45 bp_multi_top 0.4 []
# bash run_postroute_qs.sh asap7 jpeg 0.4 []
# bash run_postroute_qs.sh asap7 swerv_wrapper 0.4 [ok]
# bash run_postroute_qs.sh asap7 cva6 0.4 [ok]
# bash run_postroute_qs.sh asap7 ariane 0.4 [ok]
#
#     platform : nangate45 | asap7
#     design   : jpeg | swerv_wrapper | ariane136 | bp_multi_top | cva6 | ariane
#     q_s      : perturbation fraction, e.g. 0.4
#     stage    : placement (default) | cts
#
#   Pipeline (reuses the existing tested harness):
#     [1] attacks/blind/run_blind_attack.py  -> attacked ODB
#           results/phase3/raw/atk_{p,c}_<plat>_<design>_qs<q>.odb   (cheap; ~seconds)
#     [2] place_ordering/run_ppa.sh (or cts_v2/run_ppa.sh)          -> re-run
#           CTS + global/detailed route + finish on the attacked ODB   (HEAVY)
#           under FLOW_VARIANT atk-{p,c}-<design>-qs<q>
#     [3] read post-route PPA from the fresh 6_report.json (ns-normalized),
#           and compute deltas vs the watermark-only baseline via
#           attacks/ppa/aggregate_attack_ppa.py -> results/phase3/blind_ppa.csv
#
# Only step [2] is expensive; run it on a server. Independent (design,q_s)
# invocations can be distributed freely.
set -euo pipefail

PLAT="${1:?platform}"; DESIGN="${2:?design}"; QS="${3:?q_s}"; STAGE="${4:-placement}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
export EXPERIMENTS_HOME="$HERE"

init=$([ "$STAGE" = cts ] && echo c || echo p)

# --- resolve bench metadata (nickname / wm_flow_variant) + ORFS paths ---------
read -r NICK WMFV RESDIR LOGDIR VARIANT < <(python3.11 - "$PLAT" "$DESIGN" "$STAGE" "$QS" <<'PY'
import sys
sys.path.insert(0, ".")
from bench_matrix import ACTIVE_BENCHES
from lib.orfs import experiment_results, experiment_logs
plat, design, stage, qs = sys.argv[1:5]
b = next((x for x in ACTIVE_BENCHES
          if x.platform == plat and (x.design == design or x.design_nickname == design)), None)
assert b is not None, f"no bench {plat}/{design} in ACTIVE_BENCHES"
init = "c" if stage == "cts" else "p"
variant = f"atk-{init}-{b.design}-qs{qs}"
print(b.design_nickname, b.wm_flow_variant,
      experiment_results(plat, b.design_nickname, variant),
      experiment_logs(plat, b.design_nickname, variant),
      variant)
PY
)
echo "[cfg] $PLAT/$DESIGN nick=$NICK wm_variant=$WMFV stage=$STAGE q_s=$QS -> FLOW_VARIANT=$VARIANT"

# --- [1] produce the attacked ODB at q_s -------------------------------------
echo "[1/3] blind $STAGE attack at q_s=$QS ..."
python3.11 attacks/blind/run_blind_attack.py --stages "$STAGE" --qs-list "$QS" --designs "$DESIGN"

ODB="results/phase3/raw/atk_${init}_${PLAT}_${DESIGN}_qs${QS}.odb"
if [ ! -f "$ODB" ]; then
  ODB=$(ls -t results/phase3/raw/atk_${init}_${PLAT}_${DESIGN}_qs*.odb 2>/dev/null | head -1 || true)
fi
[ -n "${ODB:-}" ] && [ -f "$ODB" ] || { echo "ERROR: attacked ODB not found for $PLAT/$DESIGN qs=$QS"; exit 1; }
ODB_ABS="$(readlink -f "$ODB")"
echo "      attacked ODB: $ODB_ABS"

# --- [2] continue the back-end flow (heavy) ----------------------------------
echo "[2/3] re-running CTS + route + finish (full flow -- heavy) ..."
if [ "$STAGE" = cts ]; then
  env DESIGN="$DESIGN" DESIGN_NICKNAME="$NICK" PLATFORM="$PLAT" \
      WM_FLOW_VARIANT="$WMFV" FLOW_VARIANT="$VARIANT" WM_RESULTS="$RESDIR" \
      CTS_ODB="$ODB_ABS" bash ../cts_v2/run_ppa.sh
else
  env DESIGN="$DESIGN" DESIGN_NICKNAME="$NICK" PLATFORM="$PLAT" \
      WM_FLOW_VARIANT="$WMFV" FLOW_VARIANT="$VARIANT" WM_RESULTS="$RESDIR" \
      DP_ODB="$ODB_ABS" bash ../place_ordering/run_ppa.sh
fi

# --- [3] report post-route PPA (absolute + delta vs watermark baseline) -------
echo "[3/3] post-route PPA:"
python3.11 - "$PLAT" "$NICK" "$VARIANT" "$LOGDIR" <<'PY'
import sys
sys.path.insert(0, ".")
from lib.orfs import load_experiment_metrics
plat, nick, variant, logdir = sys.argv[1:5]
try:
    m = load_experiment_metrics(plat, nick, variant)
    print(f"  WNS (ns)   : {m.wns_ns}")
    print(f"  TNS (ns)   : {m.tns_ns}")
    print(f"  power (W)  : {m.power_w}")
    print(f"  routed WL  : {m.rwl_um}")
except Exception as e:
    print(f"  (could not load metrics: {e}); check {logdir}/6_report.json")
PY

echo "[agg] deltas vs watermark-only baseline -> results/phase3/blind_ppa.csv"
python3.11 attacks/ppa/aggregate_attack_ppa.py >/dev/null 2>&1 || true
echo "  header: $(head -1 results/phase3/blind_ppa.csv 2>/dev/null || echo '(no csv)')"
grep -E "^${PLAT},${DESIGN},${STAGE},${QS}," results/phase3/blind_ppa.csv 2>/dev/null \
  || echo "  (no aggregated row yet for ${PLAT},${DESIGN},${STAGE},${QS})"
echo "[done] $PLAT/$DESIGN qs=$QS"
