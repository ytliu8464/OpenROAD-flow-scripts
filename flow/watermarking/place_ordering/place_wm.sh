#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Run pairwise / group ordering placement watermark embed/verify inside Singularity.

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
  WM_INPUT / WM_OUTPUT_ODB / WM_OUTPUT_DEF / WM_OUTPUT_CELL_LIST
  WM_SEED_HEX             seed_placement.hex from gen_key/
  WM_GRID_NX / WM_GRID_NY
  WM_PAIR_DIST_UM          max horizontal separation for pairs (default 5)
  WM_PAIRS_PER_TILE        pair quota per tile (default 4)
  WM_GROUPS_PER_TILE       triple quota per tile (default 2)
  WM_USE_GROUPS            1=enable 3-cell groups (default 1)
  WM_HPWL_EPS_PAIR_DBU     max |local HPWL delta| for a pair (default 500)
  WM_HPWL_EPS_GROUP_DBU    max perm spread for triples (default 500)
  WM_FANOUT_MAX
  WM_SLACK_THRESHOLD_NS
  WM_CRIT_BIN_NS
  WM_TILE_DENSITY_MAX
  WM_TILE_DISP_CAP_UM
  WM_BLOCKAGE_MARGIN_SITES
  WM_SDC / WM_LIB_FILES
  WM_MAX_DISP_X / WM_MAX_DISP_Y   incremental DPL (um, default 50)

Environment (verify):
  WM_VERIFY_INPUT  watermarked (or suspect) .odb
  WM_CELL_LIST     embed CSV (ground truth)

Environment (verify_stages):
  WM_CELL_LIST
  WM_VERIFY_STAGES  'label:odb,label:odb,...'
  WM_STAGE_REPORT   optional per-stage summary CSV
  WM_STAGE_PPA_REPORT
  WM_REPORTS_DIR / WM_LOGS_DIR
EOF
}

run_in_singularity() {
  local inner_cmd="$1"
  singularity exec -B /home -B /tmp --bind /tmp/.X11-unix -e "$SIF" \
    env \
    PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}" \
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
    WM_SEED_HEX="${WM_SEED_HEX:-}" \
    WM_MESSAGE="${WM_MESSAGE:-}" \
    WM_GRID_NX="${WM_GRID_NX:-8}" \
    WM_GRID_NY="${WM_GRID_NY:-8}" \
    WM_PAIR_DIST_UM="${WM_PAIR_DIST_UM:-5}" \
    WM_PAIRS_PER_TILE="${WM_PAIRS_PER_TILE:-4}" \
    WM_GROUPS_PER_TILE="${WM_GROUPS_PER_TILE:-2}" \
    WM_USE_GROUPS="${WM_USE_GROUPS:-1}" \
    WM_HPWL_EPS_PAIR_DBU="${WM_HPWL_EPS_PAIR_DBU:-500}" \
    WM_HPWL_EPS_GROUP_DBU="${WM_HPWL_EPS_GROUP_DBU:-500}" \
    WM_FANOUT_MAX="${WM_FANOUT_MAX:-16}" \
    WM_SLACK_THRESHOLD_NS="${WM_SLACK_THRESHOLD_NS:-0.05}" \
    WM_CRIT_BIN_NS="${WM_CRIT_BIN_NS:-0.05}" \
    WM_TILE_DENSITY_MAX="${WM_TILE_DENSITY_MAX:-1.2}" \
    WM_TILE_DISP_CAP_UM="${WM_TILE_DISP_CAP_UM:-200}" \
    WM_BLOCKAGE_MARGIN_SITES="${WM_BLOCKAGE_MARGIN_SITES:-4}" \
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
    export WM_VERIFY_INPUT="${WM_OUTPUT_ODB:?set WM_OUTPUT_ODB before all}"
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
