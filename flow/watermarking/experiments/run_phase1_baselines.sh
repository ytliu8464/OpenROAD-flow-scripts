#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Run 7 designs x 2 baselines = 14 chained ORFS flows.
#
# Usage:
#   bash run_phase1_baselines.sh [--skip-cellscatter] [--skip-bufins]
#
# Optional env:
#   OWNER_ID    (default: yiting)
#   BASELINE_K  integer override for K per design (applied to all designs)
#   SKIP_DONE   set to 1 to skip any design/baseline whose 6_report.json already exists

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ_DIR="${PROJ_DIR:-/home/fetzfs_projects/MISC-ytliu/watermarking}"
FLOW_HOME="${FLOW_HOME:-${PROJ_DIR}/OR0415/OpenROAD-flow-scripts/flow}"

CELLSCATTER_SH="${SCRIPT_DIR}/baselines/cell_scattering/run.sh"
BUFINS_SH="${SCRIPT_DIR}/baselines/buffer_insertion/run.sh"

export OWNER_ID="${OWNER_ID:-yiting}"
export BASELINE_K="${BASELINE_K:-}"
SKIP_DONE="${SKIP_DONE:-0}"

RUN_CELLSCATTER=1
RUN_BUFINS=1

for arg in "$@"; do
  case "${arg}" in
    --skip-cellscatter) RUN_CELLSCATTER=0 ;;
    --skip-bufins)      RUN_BUFINS=0 ;;
    *) echo "unknown arg: ${arg}" >&2; exit 2 ;;
  esac
done

ts()  { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] [run_phase1_baselines] $*"; }

# Bench matrix (matches ACTIVE_BENCHES in bench_matrix.py)
declare -a BENCHES=(
  "nangate45:aes:watermarking-test1"
  "nangate45:jpeg:watermarking-test1"
  "nangate45:swerv_wrapper:base"
  "nangate45:ariane136:base_tcp3p5"
  "asap7:aes:base"
  "asap7:jpeg:base_tcp540"
  "asap7:swerv_wrapper:base_tcp1455"
)

pass=0
skip=0
fail=0
FAILED=()

run_one() {
  local plat="$1" design="$2" wm_variant="$3" flow_variant="$4" driver="$5"
  local report="${FLOW_HOME}/logs/${plat}/${design}/${flow_variant}/6_report.json"

  if [[ "${SKIP_DONE}" == "1" && -f "${report}" ]]; then
    log "SKIP  ${plat}/${design}/${flow_variant} (already done)"
    ((skip++)) || true
    return 0
  fi

  log "START ${plat}/${design} -> ${flow_variant}"
  local t0="${SECONDS}"
  if DESIGN="${design}" PLATFORM="${plat}" \
     WM_FLOW_VARIANT="${wm_variant}" \
     FLOW_VARIANT="${flow_variant}" \
     BASELINE_K="${BASELINE_K}" \
     bash "${driver}" ; then
    local elapsed=$(( SECONDS - t0 ))
    log "OK    ${plat}/${design}/${flow_variant} in ${elapsed}s"
    ((pass++)) || true
  else
    log "FAIL  ${plat}/${design}/${flow_variant}"
    FAILED+=("${plat}/${design}/${flow_variant}")
    ((fail++)) || true
  fi
}

for bench in "${BENCHES[@]}"; do
  IFS=':' read -r plat design wm_var <<< "${bench}"

  if [[ "${RUN_CELLSCATTER}" == "1" ]]; then
    run_one "${plat}" "${design}" "${wm_var}" "baseline-cellscatter" "${CELLSCATTER_SH}"
  fi
  if [[ "${RUN_BUFINS}" == "1" ]]; then
    run_one "${plat}" "${design}" "${wm_var}" "baseline-bufins" "${BUFINS_SH}"
  fi
done

log "========================================"
log "Done: ${pass} passed, ${skip} skipped, ${fail} failed out of $((pass+skip+fail)) total"
if [[ ${fail} -gt 0 ]]; then
  log "Failed runs:"
  for r in "${FAILED[@]}"; do log "  ${r}"; done
  exit 1
fi
log "All baseline runs completed."
log ""
log "Next steps:"
log "  python3.11 phase1_ppa.py"
log "  python3.11 aggregate.py --what ppa"
log "  python3.11 render_tex.py"
