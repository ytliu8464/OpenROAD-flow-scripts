#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
#
# Matched-thread reroute timing: for each design, wall-clock a SURGICAL reroute
# (watermark nets only) and a FULL reroute (all nets), both starting only from
# the fully-routed watermarked ODB, both at the SAME thread count, run back to
# back on the SAME node.  Captures whole-process wall (setup + reload + route +
# write) plus the pin-access / detail-route split from the log.
#
#   Usage: bash run_reroute_timing.sh [THREADS] [DRY] [only_plat] [only_design]
#   Env:   THREADS (default nproc), DRY=1 (stop after net selection)
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$HERE"
FLOW_HOME="$(cd "$HERE/../.." && pwd)"
WM="$FLOW_HOME/watermarking"
SIF="${SINGULARITY_SIF:-/home/tool/singularity/images/ispd26.sif}"
OPENROAD_EXE="${OPENROAD_EXE:-$FLOW_HOME/../../OpenROAD/build/bin/openroad}"
TCL="$WM/routing_wrong_way/reroute_experiment.tcl"

THREADS="${1:-$(nproc)}"
DRY="${2:-0}"
ONLY_PLAT="${3:-}"; ONLY_DESIGN="${4:-}"

OUTDIR="$HERE/results/phase3/reroute_timing"; mkdir -p "$OUTDIR"
TMP_ODB="$OUTDIR/_tmp_out.odb"                       # reused -> bounds disk to 1 ODB
SUMMARY="$OUTDIR/reroute_timing_summary.csv"
[ -f "$SUMMARY" ] || echo "platform,design,mode,threads,reroute_nets,fixed_nets,wall_s,pinaccess_s,detailroute_s,ok" > "$SUMMARY"

DESIGNS=(
  "nangate45 jpeg"          "nangate45 swerv_wrapper"
  "nangate45 ariane136"     "nangate45 bp_multi"
  "asap7 jpeg"              "asap7 swerv_wrapper"
  "asap7 cva6"              "asap7 ariane"
)

hms_to_s () {  # "HH:MM:SS" or "MM:SS" -> seconds
  awk -F: '{ n=NF; s=0; for(i=1;i<=n;i++){ s = s*60 + $i } print s }' <<<"$1"
}

run_one () {
  local plat="$1" nick="$2" mode="$3"
  local odb="$HERE/results/$plat/$nick/pdmarks-all-stage/5_route.odb"
  local log="$OUTDIR/${plat}_${nick}_${mode}.log"
  if [ ! -f "$odb" ]; then
    echo "[skip] $plat/$nick: no input ODB ($odb)"; return
  fi
  echo "[run ] $plat/$nick mode=$mode threads=$THREADS  $(date '+%H:%M:%S')"
  local t0 t1 wall rc
  t0=$(date +%s.%N)
  singularity exec -B /home -B /tmp -e "$SIF" env \
    MODE="$mode" WM_ODB="$(readlink -f "$odb")" WM_OUT_ODB="$TMP_ODB" \
    WM_FRAC=0.02 DRY="$DRY" \
    "$OPENROAD_EXE" -exit -threads "$THREADS" "$TCL" > "$log" 2>&1
  rc=$?
  t1=$(date +%s.%N)
  wall=$(awk "BEGIN{printf \"%.1f\", $t1-$t0}")

  local nre nfx pa dr ok
  nre=$(grep -oE "reroute_nets=[0-9]+" "$log" | tail -1 | cut -d= -f2); nre="${nre:-NA}"
  nfx=$(grep -oE "fixed_nets=[0-9]+"   "$log" | tail -1 | cut -d= -f2); nfx="${nfx:-NA}"
  # pin access elapsed: the DRT-0267 line right after "Complete pin access"
  pa=$(awk '/Complete pin access/{f=1} f&&/elapsed time =/{match($0,/elapsed time = ([0-9:]+)/,m); print m[1]; exit}' "$log")
  pa=${pa:+$(hms_to_s "$pa")}; pa="${pa:-NA}"
  # detail route total: last "elapsed time" inside detail routing region
  dr=$(awk '/Start detail routing/{f=1} f&&/elapsed time =/{match($0,/elapsed time = ([0-9:]+)/,m); last=m[1]} END{print last}' "$log")
  dr=${dr:+$(hms_to_s "$dr")}; dr="${dr:-NA}"
  ok=$([ $rc -eq 0 ] && grep -q "reroute_experiment: DONE\|DRY run" "$log" && echo 1 || echo 0)

  echo "$plat,$nick,$mode,$THREADS,$nre,$nfx,$wall,$pa,$dr,$ok" >> "$SUMMARY"
  echo "       -> wall=${wall}s reroute_nets=$nre pinaccess=${pa}s detailroute=${dr}s ok=$ok"
  rm -f "$TMP_ODB"
}

echo "===== reroute timing: threads=$THREADS dry=$DRY node=$(hostname) $(date) ====="
for entry in "${DESIGNS[@]}"; do
  read -r plat nick <<< "$entry"
  [ -n "$ONLY_PLAT" ] && [ "$plat" != "$ONLY_PLAT" ] && continue
  [ -n "$ONLY_DESIGN" ] && [ "$nick" != "$ONLY_DESIGN" ] && continue
  run_one "$plat" "$nick" surgical      # surgical first (faster -> early signal)
  run_one "$plat" "$nick" full
done
echo "===== done $(date) ; summary -> $SUMMARY ====="
