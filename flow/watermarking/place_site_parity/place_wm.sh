#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Run site-parity placement watermark embed/verify inside Singularity.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENROAD_EXE="${OPENROAD_EXE:-/home/fetzfs_projects/MISC-ytliu/watermarking/OR0415/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad}"
SIF="${SINGULARITY_SIF:-/home/tool/singularity/images/ispd26.sif}"

usage() {
  cat <<EOF
Usage:
  $0 embed
  $0 verify
  $0 verify_stages
  $0 all

Environment (embed):
  WM_INPUT                post-DP .odb (e.g. .../3_place.odb)
  WM_OUTPUT_ODB           output watermarked .odb
  WM_OUTPUT_DEF           optional output DEF
  WM_OUTPUT_CELL_LIST     optional CSV of embed results
  WM_SEED_HEX             path to seed_placement.hex (from gen_key/)
  WM_GRID_NX / WM_GRID_NY tile grid (default 8 x 8)
  WM_K_PERCENT            % of cells per tile to watermark (default 5)
  WM_SLACK_THRESHOLD_NS   worst-pin-slack lower bound (default 0.1 ns)
  WM_SDC                  optional SDC to read before STA
  WM_MAX_DISP_X/Y         incremental DPL max displacement in microns (default 50)
  WM_MESSAGE              optional human-readable tag

Environment (verify):
  WM_VERIFY_INPUT         watermarked .odb
  WM_CELL_LIST            embed CSV (preferred ground truth)
  WM_SEED_HEX             required only if WM_CELL_LIST is absent
  (reuses WM_GRID_NX/NY, WM_K_PERCENT, WM_SLACK_THRESHOLD_NS, WM_SDC)
  WM_VERIFY_CELL_LIST     optional output CSV

Environment (verify_stages):
  WM_CELL_LIST            embed CSV (ground truth)
  WM_VERIFY_STAGES        'label:odb,label:odb,...'
  WM_STAGE_REPORT         optional output CSV
  WM_STAGE_PPA_REPORT     optional per-stage PPA CSV (WNS/TNS/wirelength/power)
  WM_REPORTS_DIR          optional ORFS reports dir override for PPA extraction
  WM_LOGS_DIR             optional ORFS logs dir override for PPA extraction
  WM_DBU_PER_MICRON       DBU-per-um for display (default 2000)
EOF
}

run_in_singularity() {
  local inner_cmd="$1"
  singularity exec -B /home -B /tmp --bind /tmp/.X11-unix -e "$SIF" \
    env \
    OPENROAD_EXE="${OPENROAD_EXE}" \
    WM_INPUT="${WM_INPUT:-}" \
    WM_OUTPUT_ODB="${WM_OUTPUT_ODB:-}" \
    WM_OUTPUT_DEF="${WM_OUTPUT_DEF:-}" \
    WM_OUTPUT_CELL_LIST="${WM_OUTPUT_CELL_LIST:-}" \
    WM_VERIFY_INPUT="${WM_VERIFY_INPUT:-}" \
    WM_VERIFY_CELL_LIST="${WM_VERIFY_CELL_LIST:-}" \
    WM_CELL_LIST="${WM_CELL_LIST:-}" \
    WM_VERIFY_STAGES="${WM_VERIFY_STAGES:-}" \
    WM_STAGE_REPORT="${WM_STAGE_REPORT:-}" \
    WM_STAGE_PPA_REPORT="${WM_STAGE_PPA_REPORT:-}" \
    WM_REPORTS_DIR="${WM_REPORTS_DIR:-}" \
    WM_LOGS_DIR="${WM_LOGS_DIR:-}" \
    WM_DBU_PER_MICRON="${WM_DBU_PER_MICRON:-2000}" \
    WM_SEED_HEX="${WM_SEED_HEX:-}" \
    WM_MESSAGE="${WM_MESSAGE:-}" \
    WM_GRID_NX="${WM_GRID_NX:-8}" \
    WM_GRID_NY="${WM_GRID_NY:-8}" \
    WM_K_PERCENT="${WM_K_PERCENT:-5}" \
    WM_SLACK_THRESHOLD_NS="${WM_SLACK_THRESHOLD_NS:-0.1}" \
    WM_SDC="${WM_SDC:-}" \
    WM_LIB_FILES="${WM_LIB_FILES:-}" \
    WM_MAX_DISP_X="${WM_MAX_DISP_X:-50}" \
    WM_MAX_DISP_Y="${WM_MAX_DISP_Y:-50}" \
    bash -lc "$inner_cmd"
}

case "${1:-}" in
  embed)
    run_in_singularity "export PYTHONPATH=\"${SCRIPT_DIR}:\${PYTHONPATH:-}\" ; cd \"${SCRIPT_DIR}\" ; \"${OPENROAD_EXE}\" -python -exit \"${SCRIPT_DIR}/watermark_embed.py\""
    ;;
  verify)
    run_in_singularity "export PYTHONPATH=\"${SCRIPT_DIR}:\${PYTHONPATH:-}\" ; cd \"${SCRIPT_DIR}\" ; \"${OPENROAD_EXE}\" -python -exit \"${SCRIPT_DIR}/watermark_verify.py\""
    ;;
  verify_stages)
    run_in_singularity "export PYTHONPATH=\"${SCRIPT_DIR}:\${PYTHONPATH:-}\" ; cd \"${SCRIPT_DIR}\" ; \"${OPENROAD_EXE}\" -python -exit \"${SCRIPT_DIR}/watermark_verify_stages.py\""
    ;;
  all)
    "$0" embed
    export WM_VERIFY_INPUT="${WM_OUTPUT_ODB:?set WM_OUTPUT_ODB before run all}"
    export WM_CELL_LIST="${WM_OUTPUT_CELL_LIST:-}"
    "$0" verify
    ;;
  -h|--help|help|"")
    usage
    ;;
  *)
    echo "Unknown command: $1" >&2
    usage
    exit 1
    ;;
esac
