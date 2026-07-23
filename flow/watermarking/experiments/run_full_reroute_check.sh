#!/usr/bin/env bash
# Full reroute (all nets) + route_counts, to test whether a full reroute removes
# the routing watermark (reuses wm_nets.txt dumped by the surgical run).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$HERE"
FLOW_HOME="$(cd "$HERE/../.." && pwd)"; WM="$FLOW_HOME/watermarking"
SIF="/home/tool/singularity/images/ispd26.sif"
OPENROAD_EXE="$FLOW_HOME/../../OpenROAD/build/bin/openroad"
TCL="$WM/routing_wrong_way/reroute_experiment.tcl"
OUTB="$HERE/results/phase3/removal_check"
for nick in jpeg swerv_wrapper ariane136 bp_multi; do
  IN="$HERE/results/nangate45/$nick/pdmarks-all-stage/5_route.odb"
  OD="$OUTB/$nick"; OUT="$OD/5_route_full.odb"
  echo "===== $nick : FULL reroute  $(date '+%H:%M:%S') ====="
  singularity exec -B /home -B /tmp -e "$SIF" env \
    MODE=full WM_ODB="$(readlink -f "$IN")" WM_OUT_ODB="$OUT" \
    "$OPENROAD_EXE" -exit -threads 8 "$TCL" > "$OD/full.log" 2>&1
  [ -f "$OUT" ] || { echo "  [err] no ODB"; continue; }
  echo "  $(grep -oE 'reroute_nets=[0-9]+' "$OD/full.log" | tail -1)"
  WM_ODB="$(readlink -f "$OUT")" WM_COUNTS_CSV="$OD/route_counts_full.csv" \
    OPENROAD_EXE="$OPENROAD_EXE" bash "$HERE/tools/dump_route_counts.sh" \
    > "$OD/dump_full.log" 2>&1 && echo "  route_counts_full -> ok" || echo "  [warn] dump failed"
  rm -f "$OUT"   # bound disk; keep only the route_counts
done
echo "===== full-reroute done $(date) ====="
