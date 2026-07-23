#!/usr/bin/env bash
# Corrected removal+PPA: reroute with the watermark TAG CLEARED (WM_CLEAR_TAG=1)
# so the watermark-aware DRT does not re-apply the wrong-way penalty.
# For each NG45 design x {surgical, full}: reroute -> route_counts -> do-finish.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$HERE"
FLOW_HOME="$(cd "$HERE/../.." && pwd)"; WM="$FLOW_HOME/watermarking"
SIF="/home/tool/singularity/images/ispd26.sif"
OPENROAD_EXE="$FLOW_HOME/../../OpenROAD/build/bin/openroad"
TCL="$WM/routing_wrong_way/reroute_experiment.tcl"
OUTB="$HERE/results/phase3/removal_check"
ROWS=("jpeg jpeg jpeg" "swerv_wrapper swerv_wrapper swerv_wrapper"
      "ariane136 ariane136 ariane136" "bp_multi_top bp_multi bp_multi_top")
for row in "${ROWS[@]}"; do
  read -r design nick dcfg <<< "$row"
  IN="$HERE/results/nangate45/$nick/pdmarks-all-stage/5_route.odb"
  OD="$OUTB/$nick"; mkdir -p "$OD"
  for mode in surgical full; do
    VAR="atk-$mode-cleared-$design"
    ODIR="$HERE/results/nangate45/$nick/$VAR"; mkdir -p "$ODIR"
    OUT_ODB="$ODIR/5_route.odb"
    echo "===== $nick $mode (tag cleared)  $(date '+%H:%M:%S') ====="
    singularity exec -B /home -B /tmp -e "$SIF" env \
      MODE="$mode" WM_CLEAR_TAG=1 WM_ODB="$(readlink -f "$IN")" WM_OUT_ODB="$OUT_ODB" \
      "$OPENROAD_EXE" -exit -threads 8 "$TCL" > "$OD/${mode}_cleared.log" 2>&1
    [ -f "$OUT_ODB" ] || { echo "  [err] no ODB"; continue; }
    echo "  $(grep -oE 'reroute_nets=[0-9]+ fixed_nets=[0-9]+ tags_cleared=[0-9]+' "$OD/${mode}_cleared.log" | tail -1)"
    WM_ODB="$(readlink -f "$OUT_ODB")" WM_COUNTS_CSV="$OD/route_counts_${mode}_cleared.csv" \
      OPENROAD_EXE="$OPENROAD_EXE" bash "$HERE/tools/dump_route_counts.sh" \
      > "$OD/dump_${mode}_cleared.log" 2>&1 && echo "  route_counts ok" || echo "  [warn] dump failed"
    cp -f "$HERE/results/nangate45/$nick/pdmarks-all-stage/5_route.sdc" "$ODIR/5_route.sdc" 2>/dev/null || true
    singularity exec -B /home -B /tmp -e "$SIF" env \
      PROJ_DIR="/home/fetzfs_projects/MISC-ytliu/watermarking" OPENROAD_EXE="$OPENROAD_EXE" \
      FLOW_HOME="$FLOW_HOME" EXPERIMENTS_HOME="$HERE" WM_RESULTS_HOME="$HERE/results" \
      DESIGN="$design" DESIGN_NICKNAME="$nick" PLATFORM="nangate45" \
      WM_FLOW_VARIANT="pdmarks-all-stage" FLOW_VARIANT="$VAR" WM_RESULTS="$(readlink -f "$ODIR")" \
      bash -lc "make -f '$FLOW_HOME/Makefile' DESIGN_CONFIG='$FLOW_HOME/designs/nangate45/$dcfg/config.mk' WORK_HOME='$HERE' do-finish" \
      > "$OD/finish_${mode}_cleared.log" 2>&1 && echo "  do-finish ok" || echo "  [warn] finish failed"
    rm -f "$OUT_ODB"
  done
done
echo "===== v2 done $(date) ====="
