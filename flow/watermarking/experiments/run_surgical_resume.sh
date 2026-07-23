#!/usr/bin/env bash
set +e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$HERE"
FLOW_HOME="$(cd "$HERE/../.." && pwd)"; WM="$FLOW_HOME/watermarking"
SIF="/home/tool/singularity/images/ispd26.sif"
OPENROAD_EXE="$FLOW_HOME/../../OpenROAD/build/bin/openroad"
TCL="$WM/routing_wrong_way/reroute_experiment.tcl"
OUTB="$HERE/results/phase3/removal_check"

do_dump_finish () {  # nick design dcfg var odb
  local nick="$1" design="$2" dcfg="$3" var="$4" odb="$5" OD="$OUTB/$1"
  echo "[$(date '+%H:%M:%S')] $nick: dump route_counts"
  WM_ODB="$(readlink -f "$odb")" WM_COUNTS_CSV="$OD/route_counts_surgical_cleared.csv" \
    OPENROAD_EXE="$OPENROAD_EXE" bash "$HERE/tools/dump_route_counts.sh" > "$OD/dump_surgical_cleared.log" 2>&1
  echo "[$(date '+%H:%M:%S')] $nick: dump rc=$?  ; do-finish"
  local ODIR; ODIR="$(dirname "$odb")"
  cp -f "$HERE/results/nangate45/$nick/pdmarks-all-stage/5_route.sdc" "$ODIR/5_route.sdc" 2>/dev/null
  singularity exec -B /home -B /tmp -e "$SIF" env \
    PROJ_DIR="/home/fetzfs_projects/MISC-ytliu/watermarking" OPENROAD_EXE="$OPENROAD_EXE" \
    FLOW_HOME="$FLOW_HOME" EXPERIMENTS_HOME="$HERE" WM_RESULTS_HOME="$HERE/results" \
    DESIGN="$design" DESIGN_NICKNAME="$nick" PLATFORM="nangate45" \
    WM_FLOW_VARIANT="pdmarks-all-stage" FLOW_VARIANT="$var" WM_RESULTS="$(readlink -f "$ODIR")" \
    bash -lc "make -f '$FLOW_HOME/Makefile' DESIGN_CONFIG='$FLOW_HOME/designs/nangate45/$dcfg/config.mk' WORK_HOME='$HERE' do-finish" > "$OD/finish_surgical_cleared.log" 2>&1
  echo "[$(date '+%H:%M:%S')] $nick: finish rc=$?"
  rm -f "$odb"
}

# 1) swerv: reroute already done -> just dump + finish from existing ODB
SW_ODB="$HERE/results/nangate45/swerv_wrapper/atk-surgical-cleared-swerv_wrapper/5_route.odb"
[ -f "$SW_ODB" ] && do_dump_finish swerv_wrapper swerv_wrapper swerv_wrapper atk-surgical-cleared-swerv_wrapper "$SW_ODB"

# 2) ariane + bp: full surgical (tag cleared)
for row in "ariane136 ariane136 ariane136" "bp_multi_top bp_multi bp_multi_top"; do
  read -r design nick dcfg <<< "$row"
  IN="$HERE/results/nangate45/$nick/pdmarks-all-stage/5_route.odb"
  OD="$OUTB/$nick"; mkdir -p "$OD"
  VAR="atk-surgical-cleared-$design"
  ODIR="$HERE/results/nangate45/$nick/$VAR"; mkdir -p "$ODIR"; OUT_ODB="$ODIR/5_route.odb"
  echo "[$(date '+%H:%M:%S')] $nick: surgical reroute (tag cleared)"
  singularity exec -B /home -B /tmp -e "$SIF" env \
    MODE=surgical WM_CLEAR_TAG=1 WM_ODB="$(readlink -f "$IN")" WM_OUT_ODB="$OUT_ODB" \
    "$OPENROAD_EXE" -exit -threads 8 "$TCL" > "$OD/surgical_cleared.log" 2>&1
  echo "[$(date '+%H:%M:%S')] $nick: reroute rc=$? $(grep -oE 'tags_cleared=[0-9]+' "$OD/surgical_cleared.log" | tail -1)"
  [ -f "$OUT_ODB" ] && do_dump_finish "$nick" "$design" "$dcfg" "$VAR" "$OUT_ODB"
done
echo "[$(date '+%H:%M:%S')] surgical-resume done"
