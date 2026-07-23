#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
#
# For each NG45 design: surgically reroute ONLY the watermark nets (keeping the
# layout), then (1) dump route_counts on the rerouted layout to re-measure the
# routing-watermark statistic Z_R/p_R (removal check) and (2) do-finish for
# post-route PPA (degradation check).  Compares to the watermarked baseline.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$HERE"
FLOW_HOME="$(cd "$HERE/../.." && pwd)"; WM="$FLOW_HOME/watermarking"
SIF="${SINGULARITY_SIF:-/home/tool/singularity/images/ispd26.sif}"
OPENROAD_EXE="${OPENROAD_EXE:-$FLOW_HOME/../../OpenROAD/build/bin/openroad}"
TCL="$WM/routing_wrong_way/reroute_experiment.tcl"
THREADS="${THREADS:-$(nproc)}"
OUTB="$HERE/results/phase3/removal_check"; mkdir -p "$OUTB"

# design  nick  design_config_name
ROWS=(
  "jpeg jpeg jpeg"
  "swerv_wrapper swerv_wrapper swerv_wrapper"
  "ariane136 ariane136 ariane136"
  "bp_multi_top bp_multi bp_multi_top"
)

for row in "${ROWS[@]}"; do
  read -r design nick dcfg <<< "$row"
  IN="$HERE/results/nangate45/$nick/pdmarks-all-stage/5_route.odb"
  [ -f "$IN" ] || { echo "[skip] $nick: no input ODB"; continue; }
  OD="$OUTB/$nick"; mkdir -p "$OD"
  VAR="atk-surgical-fullwm-$design"
  ODIR="$HERE/results/nangate45/$nick/$VAR"; mkdir -p "$ODIR"
  OUT_ODB="$ODIR/5_route.odb"
  echo "===== $nick : surgical full-watermark reroute  $(date '+%H:%M:%S') ====="

  # 1) surgical reroute (reroute ALL watermark nets, keep the rest FIXED)
  singularity exec -B /home -B /tmp -e "$SIF" env \
    MODE=surgical WM_ODB="$(readlink -f "$IN")" WM_OUT_ODB="$OUT_ODB" \
    WM_NETS_OUT="$OD/wm_nets.txt" \
    "$OPENROAD_EXE" -exit -threads "$THREADS" "$TCL" > "$OD/surgical.log" 2>&1
  [ -f "$OUT_ODB" ] || { echo "[err] $nick surgical produced no ODB"; continue; }
  echo "  rerouted -> $(grep -oE 'reroute_nets=[0-9]+' "$OD/surgical.log" | tail -1)"

  # 2) route_counts on the rerouted layout (removal check)
  WM_ODB="$(readlink -f "$OUT_ODB")" WM_COUNTS_CSV="$OD/route_counts_surgical.csv" \
    OPENROAD_EXE="$OPENROAD_EXE" bash "$HERE/tools/dump_route_counts.sh" \
    > "$OD/dump_surgical.log" 2>&1 \
    && echo "  route_counts -> $OD/route_counts_surgical.csv" \
    || echo "  [warn] route_counts dump failed (see $OD/dump_surgical.log)"

  # 3) do-finish for PPA (density-fill + STA + power) inside singularity
  cp -f "$HERE/results/nangate45/$nick/pdmarks-all-stage/5_route.sdc" \
        "$ODIR/5_route.sdc" 2>/dev/null || echo "  [warn] no 5_route.sdc to copy"
  singularity exec -B /home -B /tmp -e "$SIF" env \
    PROJ_DIR="/home/fetzfs_projects/MISC-ytliu/watermarking" \
    OPENROAD_EXE="$OPENROAD_EXE" FLOW_HOME="$FLOW_HOME" \
    EXPERIMENTS_HOME="$HERE" WM_RESULTS_HOME="$HERE/results" \
    DESIGN="$design" DESIGN_NICKNAME="$nick" PLATFORM="nangate45" \
    WM_FLOW_VARIANT="pdmarks-all-stage" FLOW_VARIANT="$VAR" \
    WM_RESULTS="$(readlink -f "$ODIR")" \
    bash -lc "make -f '$FLOW_HOME/Makefile' \
      DESIGN_CONFIG='$FLOW_HOME/designs/nangate45/$dcfg/config.mk' \
      WORK_HOME='$HERE' do-finish" > "$OD/finish.log" 2>&1 \
    && echo "  do-finish ok" || echo "  [warn] do-finish failed (see $OD/finish.log)"
done
echo "===== all done $(date) ====="
