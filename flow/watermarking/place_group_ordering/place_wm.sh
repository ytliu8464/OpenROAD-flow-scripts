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
  WM_PAIR_DIST_UM          max horizontal separation for pairs (default 1)
  WM_PAIRS_PER_TILE        pair quota per tile (default 4)
  WM_GROUPS_PER_TILE       triple quota per tile (default 2)
  WM_USE_GROUPS            1=enable 3-cell groups (default 0)
  WM_HPWL_EPS_PAIR_DBU     max |local HPWL delta| for a pair (default 100)
  WM_HPWL_EPS_GROUP_DBU    max perm spread for triples (default 100)
  WM_FANOUT_MAX            skip cells with fanout above this (default 16)
  WM_SLACK_THRESHOLD_NS    worst-pin slack lower bound (default 0.20)
  WM_CRIT_BIN_NS
  WM_CRIT_BIN_RELAXED_NS
  WM_TILE_DENSITY_MAX
  WM_TILE_DISP_CAP_UM
  WM_BLOCKAGE_MARGIN_SITES
  WM_PAIR_NEIGHBOR_K       only check (i, i+1)..(i, i+K) per bucket (default 2; 0=full window)
  WM_TILE_OVERSAMPLE       per-tile early stop = pairs_per_tile * this (default 4)
  WM_FANOUT_DIFF_MAX       skip pairs whose fanout differs by more (default 4)
  WM_HPWL_CACHE            1=use deduped HPWL cache (default 1)
  WM_HPWL_NET_FANOUT_MAX   ignore nets above this fanout in HPWL cost (default 64)
  WM_NEIGHBOR_SLACK_MARGIN_NS  extra slack required of net-neighbors (default 0.10)
  WM_TILE_TOUCH_FRAC_MAX   max fraction of *all* tile cells perturbed per tile (default 0.05)
  WM_TILE_TOUCH_FLOOR_PAIRS minimum pairs allowed per tile by touch cap (default 4)
  WM_POST_GUARD            1=run STA after batch and revert bad swaps (default 1)
  WM_POST_GUARD_FINAL_CHECK 1=re-STA after revert for diagnostics (default 1)
  WM_GUARD_DEGRADE_NS      slack-drop tolerance before reverting (default 0.02)
  WM_MIN_PAIRS_TOTAL       trigger capacity fallback below this count (default 64)
  WM_PAIR_NEIGHBOR_K_RELAXED fallback K-neighbor value (default 8)
  WM_HPWL_EPS_PAIR_RELAXED_DBU fallback pair HPWL eps (default 200)
  WM_SDC / WM_LIB_FILES
  WM_MAX_DISP_X / WM_MAX_DISP_Y   incremental DPL (um, default 5)

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
    WM_PAIR_DIST_UM="${WM_PAIR_DIST_UM:-1}" \
    WM_PAIRS_PER_TILE="${WM_PAIRS_PER_TILE:-4}" \
    WM_GROUPS_PER_TILE="${WM_GROUPS_PER_TILE:-2}" \
    WM_USE_GROUPS="${WM_USE_GROUPS:-0}" \
    WM_HPWL_EPS_PAIR_DBU="${WM_HPWL_EPS_PAIR_DBU:-100}" \
    WM_HPWL_EPS_GROUP_DBU="${WM_HPWL_EPS_GROUP_DBU:-100}" \
    WM_FANOUT_MAX="${WM_FANOUT_MAX:-16}" \
    WM_SLACK_THRESHOLD_NS="${WM_SLACK_THRESHOLD_NS:-0.20}" \
    WM_CRIT_BIN_NS="${WM_CRIT_BIN_NS:-0.05}" \
    WM_CRIT_BIN_RELAXED_NS="${WM_CRIT_BIN_RELAXED_NS:-0.20}" \
    WM_TILE_DENSITY_MAX="${WM_TILE_DENSITY_MAX:-1.2}" \
    WM_TILE_DISP_CAP_UM="${WM_TILE_DISP_CAP_UM:-200}" \
    WM_BLOCKAGE_MARGIN_SITES="${WM_BLOCKAGE_MARGIN_SITES:-4}" \
    WM_PAIR_NEIGHBOR_K="${WM_PAIR_NEIGHBOR_K:-2}" \
    WM_TILE_OVERSAMPLE="${WM_TILE_OVERSAMPLE:-4}" \
    WM_FANOUT_DIFF_MAX="${WM_FANOUT_DIFF_MAX:-4}" \
    WM_HPWL_CACHE="${WM_HPWL_CACHE:-1}" \
    WM_HPWL_NET_FANOUT_MAX="${WM_HPWL_NET_FANOUT_MAX:-64}" \
    WM_NEIGHBOR_SLACK_MARGIN_NS="${WM_NEIGHBOR_SLACK_MARGIN_NS:-0.10}" \
    WM_TILE_TOUCH_FRAC_MAX="${WM_TILE_TOUCH_FRAC_MAX:-0.05}" \
    WM_TILE_TOUCH_FLOOR_PAIRS="${WM_TILE_TOUCH_FLOOR_PAIRS:-4}" \
    WM_POST_GUARD="${WM_POST_GUARD:-1}" \
    WM_POST_GUARD_FINAL_CHECK="${WM_POST_GUARD_FINAL_CHECK:-1}" \
    WM_GUARD_DEGRADE_NS="${WM_GUARD_DEGRADE_NS:-0.02}" \
    WM_MIN_PAIRS_TOTAL="${WM_MIN_PAIRS_TOTAL:-64}" \
    WM_PAIR_NEIGHBOR_K_RELAXED="${WM_PAIR_NEIGHBOR_K_RELAXED:-8}" \
    WM_HPWL_EPS_PAIR_RELAXED_DBU="${WM_HPWL_EPS_PAIR_RELAXED_DBU:-200}" \
    WM_SDC="${WM_SDC:-}" \
    WM_LIB_FILES="${WM_LIB_FILES:-}" \
    WM_MAX_DISP_X="${WM_MAX_DISP_X:-5}" \
    WM_MAX_DISP_Y="${WM_MAX_DISP_Y:-5}" \
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
