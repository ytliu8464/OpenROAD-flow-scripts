#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
#
# PROTOTYPE: layout-PRESERVING surgical routing attack + post-route PPA.
# Reroutes ONLY the q_s% watermark nets on the owner's fully-routed watermarked
# layout, keeping every other net's detailed routing fixed (via OpenROAD's
# FIXED-wire primitive).  Then finishes for PPA.
#
#   Usage: bash run_surgical_reroute_qs.sh <platform> <design> <q_s> [routed_wm_odb]
#
# Differs from run_attack_route.sh (which re-runs the FULL router with the
# wrong-way bias toggled) -- here the stolen routing is preserved verbatim
# except on the watermark nets, so the PPA delta is the true "reroute only the
# marked nets" cost.  NanGate45 only (ASAP7 has no routing channel).
set -euo pipefail

PLAT="${1:?platform}"; DESIGN="${2:?design}"; QS="${3:?q_s}"; ODB_IN="${4:-}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$HERE"
export EXPERIMENTS_HOME="$HERE"
FLOW_HOME="$(cd "$HERE/../.." && pwd)"
WM="$FLOW_HOME/watermarking"
SIF="${SINGULARITY_SIF:-/home/tool/singularity/images/ispd26.sif}"
OPENROAD_EXE="${OPENROAD_EXE:-$FLOW_HOME/../../OpenROAD/build/bin/openroad}"

# --- resolve owner routed ODB + route_counts (reuse run_blind helpers) --------
read -r NICK WMFV RCIN ODB_RESOLVED < <(python3.11 - "$PLAT" "$DESIGN" <<'PY'
import sys; sys.path.insert(0,"."); sys.path.insert(0,"attacks/blind")
from bench_matrix import ACTIVE_BENCHES
from run_blind_attack import _pick_embed_dir, _route_counts_csv
plat,design=sys.argv[1:3]
b=next(x for x in ACTIVE_BENCHES if x.platform==plat and (x.design==design or x.design_nickname==design))
ed,_,_,_,_=_pick_embed_dir(plat,b.design_nickname,b.wm_flow_variant)
rc=_route_counts_csv(ed,b)
# owner watermarked routed ODB candidates
import glob
cands=[str(ed/"5_route.odb")]+sorted(glob.glob(f"results/{plat}/{b.design_nickname}/*route*/5_route.odb"))
odb=next((c for c in cands if __import__('os').path.exists(c)), "")
print(b.design_nickname, b.wm_flow_variant, rc or "NONE", odb or "NONE")
PY
)
[ -n "$ODB_IN" ] || ODB_IN="$ODB_RESOLVED"
[ -f "$ODB_IN" ] || { echo "ERROR: owner routed ODB not found (pass it as arg 4). tried: $ODB_RESOLVED"; exit 1; }
[ "$RCIN" != "NONE" ] && [ -f "$RCIN" ] || { echo "ERROR: route_counts CSV not found for $PLAT/$DESIGN"; exit 1; }
echo "[cfg] $PLAT/$DESIGN q_s=$QS  in_odb=$ODB_IN  route_counts=$RCIN"

VAR="atk-surgical-$DESIGN-qs$QS"
OUTDIR="results/$PLAT/$NICK/$VAR"; mkdir -p "$OUTDIR"
NETS="results/phase3/raw/atk_surgical_${PLAT}_${DESIGN}_qs${QS}_nets.txt"
OUT_ODB="$OUTDIR/5_route.odb"

# --- 1) pick the q_s% watermark attack nets ----------------------------------
echo "[1/3] selecting q_s=$QS watermark nets ..."
ATK_QS="$QS" WM_ROUTE_COUNTS_IN="$RCIN" WM_NETS_ATTACK_OUT="$NETS" \
  python3.11 attacks/blind/attack_routing.py
echo "      $(wc -l < "$NETS") nets selected -> $NETS"

# --- 2) surgical reroute (preserve all else, reroute only watermark nets) ----
echo "[2/3] surgical reroute inside singularity (preserve fixed, reroute marked) ..."
NUM_CORES="${NUM_CORES:-$(nproc)}"
singularity exec -B /home -B /tmp -e "$SIF" env \
  WM_ODB="$(readlink -f "$ODB_IN")" WM_NETS_ATTACK="$(readlink -f "$NETS")" \
  WM_OUT_ODB="$(readlink -f "$OUT_ODB")" \
  "$OPENROAD_EXE" -exit -threads "$NUM_CORES" "$WM/routing_wrong_way/surgical_reroute.tcl"
[ -f "$OUT_ODB" ] || { echo "ERROR: surgical reroute produced no ODB (see log above)"; exit 1; }

# --- 3) finish (density-fill + final_report -> 6_report.json) INSIDE singularity
# Use do-finish (NOT finish): it runs only 6_1_fill + 6_report from 5_route.odb,
# so it needs just 5_route.odb (ours) + 5_route.sdc -- no rebuild from synth and
# no GDS/LEC.  Must run in the container (host yosys/tools are unavailable).
echo "[3/3] density-fill + final_report for post-route PPA ..."
cp -f "$(dirname "$ODB_IN")/5_route.sdc" "$OUTDIR/5_route.sdc" 2>/dev/null \
  || echo "[warn] could not copy 5_route.sdc from $(dirname "$ODB_IN")"
singularity exec -B /home -B /tmp -e "$SIF" env \
  PROJ_DIR="/home/fetzfs_projects/MISC-ytliu/watermarking" \
  OPENROAD_EXE="$OPENROAD_EXE" FLOW_HOME="$FLOW_HOME" \
  EXPERIMENTS_HOME="$EXPERIMENTS_HOME" WM_RESULTS_HOME="$EXPERIMENTS_HOME/results" \
  DESIGN="$DESIGN" DESIGN_NICKNAME="$NICK" PLATFORM="$PLAT" \
  WM_FLOW_VARIANT="$WMFV" FLOW_VARIANT="$VAR" WM_RESULTS="$(readlink -f "$OUTDIR")" \
  bash -lc "make -f '$FLOW_HOME/Makefile' \
      DESIGN_CONFIG='$FLOW_HOME/designs/$PLAT/$DESIGN/config.mk' \
      WORK_HOME='$EXPERIMENTS_HOME' do-finish" \
  || echo "[warn] do-finish failed; the rerouted 5_route.odb is at $OUT_ODB"

python3.11 - "$PLAT" "$NICK" "$VAR" <<'PY'
import sys; sys.path.insert(0,".")
from lib.orfs import load_experiment_metrics
try:
    m=load_experiment_metrics(*sys.argv[1:4])
    print(f"  WNS={m.wns_ns}  TNS={m.tns_ns}  power(W)={m.power_w}  rWL(um)={m.rwl_um}")
except Exception as e:
    print(f"  (metrics load failed: {e})")
PY
echo "[done] surgical reroute $PLAT/$DESIGN qs=$QS -> FLOW_VARIANT=$VAR"
