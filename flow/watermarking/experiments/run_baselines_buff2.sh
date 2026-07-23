#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Run all 5 prior-work baselines x 8 paper designs = 40 chained ORFS flows to
# populate the baseline rows of tab:ppa_ng45 and tab:ppa_asap7.
#
# Each baseline run.sh auto-re-execs inside Singularity (ispd26.sif), embeds its
# placement/CTS watermark on the reference ODB, runs `make wm_cts_and_route`
# (CTS + route + finish), and verifies at the DRT stage.  Outputs land under
#   experiments/results/<plat>/<nickname>/baseline-<method>/
#   experiments/logs/<plat>/<nickname>/baseline-<method>/6_report.json
# which is exactly where phase1_ppa.py looks.
#
# Usage:
#   bash run_baselines_all.sh                 # all 5 methods, all 8 designs
#   bash run_baselines_all.sh --only kahng    # one method (repeatable)
#   SKIP_DONE=1 bash run_baselines_all.sh     # skip designs whose 6_report.json exists
#   BASELINES="kahng icmarks" bash run_baselines_all.sh
#
# Notes:
#  * BP uses DESIGN=bp_multi_top (config) but DESIGN_NICKNAME=bp_multi (results);
#    the per-method run.sh honor DESIGN_NICKNAME for all filesystem paths.
#  * Each flow takes minutes-to-hours; run under nohup/tmux for the full sweep.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${HERE}/baselines"

# method -> run.sh
declare -A DRIVER=(
  [buffer_insertion]="${BASE}/buffer_insertion/run.sh"
)
# method -> FLOW_VARIANT written under experiments/{results,logs}
declare -A VARIANT=(
  [buffer_insertion]="baseline-bufins"
)

BASELINES="${BASELINES:-kahng cell_scattering buffer_insertion icmarks automarks}"
ONLY=()
for a in "$@"; do
  case "$a" in
    --only) shift; ;;            # handled below via $2 pattern; keep simple
  esac
done
# simple --only parsing
args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
  if [[ "${args[$i]}" == "--only" ]]; then ONLY+=("${args[$((i+1))]}"); fi
done
[[ ${#ONLY[@]} -gt 0 ]] && BASELINES="${ONLY[*]}"

SKIP_DONE="${SKIP_DONE:-0}"
export OWNER_ID="${OWNER_ID:-yiting}"

# 8 paper designs: "platform design ref_variant nickname"
read -r -d '' BENCHES <<'EOF' || true
asap7     jpeg           base_tcp540        jpeg
asap7     swerv_wrapper  base_tcp1455       swerv_wrapper
asap7     cva6           base_tcp950        cva6
asap7     ariane         base_fixed         ariane
EOF

ts()  { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] [run_baselines_all] $*"; }

pass=0; skip=0; fail=0; FAILED=()

while read -r PLAT DSGN VAR NICK; do
  [[ -z "${PLAT:-}" ]] && continue
  for m in ${BASELINES}; do
    drv="${DRIVER[$m]:-}"
    fv="${VARIANT[$m]:-}"
    if [[ -z "${drv}" ]]; then log "unknown baseline '${m}'"; continue; fi
    report="${HERE}/logs/${PLAT}/${NICK}/${fv}/6_report.json"
    if [[ "${SKIP_DONE}" == "1" && -f "${report}" ]]; then
      log "SKIP  ${PLAT}/${NICK}/${fv} (done)"; ((skip++)) || true; continue
    fi
    log "START ${m} on ${PLAT}/${DSGN} (nick=${NICK}, ref=${VAR})"
    if DESIGN="${DSGN}" DESIGN_NICKNAME="${NICK}" PLATFORM="${PLAT}" \
       WM_FLOW_VARIANT="${VAR}" FLOW_VARIANT="${fv}" \
       bash "${drv}"; then
      log "OK    ${m} ${PLAT}/${NICK}"; ((pass++)) || true
    else
      log "FAIL  ${m} ${PLAT}/${NICK}"; FAILED+=("${m}:${PLAT}/${NICK}"); ((fail++)) || true
    fi
  done
done <<< "${BENCHES}"

log "================================================"
log "Done: ${pass} passed, ${skip} skipped, ${fail} failed"
for r in "${FAILED[@]:-}"; do [[ -n "${r}" ]] && log "  FAILED ${r}"; done
log ""
log "Next: refresh the PPA tables with"
log "  python3.11 ${HERE}/phase1_ppa.py"
log "  python3.11 ${HERE}/aggregate.py --what ppa"
log "  python3.11 ${HERE}/render_tex.py"
[[ ${fail} -gt 0 ]] && exit 1 || exit 0
