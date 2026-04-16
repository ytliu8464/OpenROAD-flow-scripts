#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Run placement watermark embed/verify inside Singularity (see README.md).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Prefer ORFS-bundled OpenROAD; override with OPENROAD_EXE
OPENROAD_EXE="${OPENROAD_EXE:-/home/fetzfs_projects/MISC-ytliu/watermarking/OR0415/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad}"
SIF="${SINGULARITY_SIF:-/home/tool/singularity/images/ispd26.sif}"

usage() {
  cat <<EOF
Usage:
  $0 embed
  $0 verify
  $0 verify_stages // disabled, please use run_verify_stages.sh instead
  $0 all

Environment (embed):
  WM_INPUT              post-DP .odb (e.g. .../3_place.odb)
  WM_OUTPUT_ODB         output watermarked .odb
  WM_OUTPUT_DEF         optional output DEF
  WM_OUTPUT_CELL_LIST   optional output CSV of watermark cells (embed)
  WM_MESSAGE            watermark message string
  WM_KEY                secret key string
  WM_NUM_CELLS          number of constraints (default 100)
  WM_MAX_DISP_X         max displacement X microns for DPL (default 50)
  WM_MAX_DISP_Y         max displacement Y microns for DPL (default 50)

Environment (verify):
  WM_VERIFY_INPUT       watermarked .odb
  WM_VERIFY_CELL_LIST   optional output CSV of verification results
  (reuse WM_MESSAGE, WM_KEY, WM_NUM_CELLS)

Environment (verify_stages) [Please use run_verify_stages.sh instead]:
  WM_CELL_LIST          embed CSV (ground truth cell names + parities)
  WM_VERIFY_STAGES      comma-separated label:odb_path pairs
  WM_STAGE_REPORT       optional per-cell x stage CSV output path
  WM_DBU_PER_MICRON     DBU per micron for Y display in failure lines (default 2000)

Example:
  export WM_INPUT=.../3_place.odb WM_OUTPUT_ODB=.../3_place_wm.odb WM_OUTPUT_DEF=.../3_place_wm.def
  export WM_MESSAGE='hello' WM_KEY='secret' WM_NUM_CELLS=100
  $0 embed
  export WM_VERIFY_INPUT=.../3_place_wm.odb
  $0 verify
EOF
}

run_in_singularity() {
  local inner_cmd="$1"
  # Forward watermark env into the container (-e starts a clean environment).
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
    # WM_VERIFY_STAGES="${WM_VERIFY_STAGES:-}" \
    WM_STAGE_REPORT="${WM_STAGE_REPORT:-}" \
    WM_DBU_PER_MICRON="${WM_DBU_PER_MICRON:-2000}" \
    WM_MESSAGE="${WM_MESSAGE:-}" \
    WM_KEY="${WM_KEY:-}" \
    WM_NUM_CELLS="${WM_NUM_CELLS:-100}" \
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
  # verify_stages)
  #   run_in_singularity "export PYTHONPATH=\"${SCRIPT_DIR}:\${PYTHONPATH:-}\" ; cd \"${SCRIPT_DIR}\" ; \"${OPENROAD_EXE}\" -python -exit \"${SCRIPT_DIR}/watermark_verify_stages.py\""
  #   ;;
  all)
    "$0" embed
    # Always verify the file we just wrote (ignore a stale WM_VERIFY_INPUT).
    export WM_VERIFY_INPUT="${WM_OUTPUT_ODB:?set WM_OUTPUT_ODB for embed before run all}"
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
