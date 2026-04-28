#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Run CTS fanout-parity watermark embed/verify inside Singularity (see README.md).

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
  WM_CTS_INPUT              post-CTS .odb (e.g. .../4_cts.odb)
  WM_CTS_OUTPUT_ODB         output watermarked .odb
  WM_CTS_OUTPUT_CSV         optional CSV of embed pairs (ground truth)
  WM_SEED_HEX               path to seed_cts.hex (from gen_key/)
  WM_CTS_NUM_PAIRS          target successful embed count (default 32)
  WM_CTS_SIBLING_DIST_UM    sibling geographic cap in microns (default 20)
  WM_CTS_DELTA_SITES        boundary-FF delta in site pitches (default 2)
  WM_CTS_FANOUT_MARGIN      min fanout slack below max_fanout (default 2)
  WM_CTS_SLEW_HEADROOM_FRAC min slew margin pure-LCB channel (default 0.20)
  WM_CTS_SKEW_SLACK_PS      extra worst skew pure-LCB (default 20)
  WM_CTS_MAX_FANOUT         fallback Liberty max_fanout (default 32)
  WM_CTS_MAX_TRANSITION_NS  fallback Liberty max_transition ns (default 0.4)
  WM_CTS_MAX_CAP_FF         fallback Liberty max_capacitance fF (default 50)
  WM_CTS_MAX_ATTEMPTS       boundary-FF attempts per pair (default 3)
  WM_CTS_R_MAX              quasi-leaf max repair_fanout (default 2)
  WM_CTS_QL_*               quasi-leaf stricter slew/cap/slack skew (see README)
  WM_CTS_AVOID_HOLD_REPAIR  1=skip quasi_leaf with hold repair hint (default 1)
  WM_CTS_CHANNEL_BUDGET     auto|pure_only|quasi_only|N:M
  WM_SDC                    optional SDC to read for STA

Environment (verify):
  WM_CTS_VERIFY_INPUT       .odb to check
  WM_CELL_LIST              embed CSV (ground truth)
  WM_CTS_VERIFY_CSV         optional per-pair verification CSV

Environment (verify_stages):
  WM_CELL_LIST              embed CSV (ground truth)
  WM_VERIFY_STAGES          'label:odb,label:odb,...'
  WM_STAGE_REPORT           optional per-pair x stage CSV

Example:
  export WM_CTS_INPUT=.../4_cts.odb WM_CTS_OUTPUT_ODB=.../4_cts_wm.odb
  export WM_CTS_OUTPUT_CSV=.../wm_cts_pairs_embed.csv
  export WM_SEED_HEX=.../gen_key/out/aes/seed_cts.hex
  $0 embed
  export WM_CTS_VERIFY_INPUT=.../4_cts_wm.odb WM_CELL_LIST=.../wm_cts_pairs_embed.csv
  $0 verify
EOF
}

run_in_singularity() {
  local inner_cmd="$1"
  singularity exec -B /home -B /tmp --bind /tmp/.X11-unix -e "$SIF" \
    env \
    OPENROAD_EXE="${OPENROAD_EXE}" \
    WM_CTS_INPUT="${WM_CTS_INPUT:-}" \
    WM_CTS_OUTPUT_ODB="${WM_CTS_OUTPUT_ODB:-}" \
    WM_CTS_OUTPUT_CSV="${WM_CTS_OUTPUT_CSV:-}" \
    WM_CTS_VERIFY_INPUT="${WM_CTS_VERIFY_INPUT:-}" \
    WM_CTS_VERIFY_CSV="${WM_CTS_VERIFY_CSV:-}" \
    WM_CELL_LIST="${WM_CELL_LIST:-}" \
    WM_VERIFY_STAGES="${WM_VERIFY_STAGES:-}" \
    WM_STAGE_REPORT="${WM_STAGE_REPORT:-}" \
    WM_SEED_HEX="${WM_SEED_HEX:-}" \
    WM_SDC="${WM_SDC:-}" \
    WM_LIB_FILES="${WM_LIB_FILES:-}" \
    WM_SETRC="${WM_SETRC:-}" \
    WM_CTS_NUM_PAIRS="${WM_CTS_NUM_PAIRS:-32}" \
    WM_CTS_SIBLING_DIST_UM="${WM_CTS_SIBLING_DIST_UM:-20}" \
    WM_CTS_DELTA_SITES="${WM_CTS_DELTA_SITES:-2}" \
    WM_CTS_FANOUT_MARGIN="${WM_CTS_FANOUT_MARGIN:-2}" \
    WM_CTS_SLEW_HEADROOM_FRAC="${WM_CTS_SLEW_HEADROOM_FRAC:-0.20}" \
    WM_CTS_SKEW_SLACK_PS="${WM_CTS_SKEW_SLACK_PS:-20}" \
    WM_CTS_MAX_FANOUT="${WM_CTS_MAX_FANOUT:-32}" \
    WM_CTS_MAX_TRANSITION_NS="${WM_CTS_MAX_TRANSITION_NS:-0.4}" \
    WM_CTS_MAX_CAP_FF="${WM_CTS_MAX_CAP_FF:-50}" \
    WM_CTS_MAX_ATTEMPTS="${WM_CTS_MAX_ATTEMPTS:-3}" \
    WM_CTS_R_MAX="${WM_CTS_R_MAX:-2}" \
    WM_CTS_QL_SLEW_HEADROOM_FRAC="${WM_CTS_QL_SLEW_HEADROOM_FRAC:-}" \
    WM_CTS_QL_CAP_HEADROOM_FRAC="${WM_CTS_QL_CAP_HEADROOM_FRAC:-0.20}" \
    WM_CTS_QL_SETUP_SLACK_PS="${WM_CTS_QL_SETUP_SLACK_PS:-50}" \
    WM_CTS_QL_HOLD_SLACK_PS="${WM_CTS_QL_HOLD_SLACK_PS:-30}" \
    WM_CTS_QL_SKEW_SLACK_PS="${WM_CTS_QL_SKEW_SLACK_PS:-}" \
    WM_CTS_AVOID_HOLD_REPAIR="${WM_CTS_AVOID_HOLD_REPAIR:-1}" \
    WM_CTS_CHANNEL_BUDGET="${WM_CTS_CHANNEL_BUDGET:-auto}" \
    bash -lc "$inner_cmd"
}

case "${1:-}" in
  embed)
    run_in_singularity "export PYTHONPATH=\"${SCRIPT_DIR}:\${PYTHONPATH:-}\" ; cd \"${SCRIPT_DIR}\" ; \"${OPENROAD_EXE}\" -python -exit \"${SCRIPT_DIR}/cts_watermark_embed.py\""
    ;;
  verify)
    run_in_singularity "export PYTHONPATH=\"${SCRIPT_DIR}:\${PYTHONPATH:-}\" ; cd \"${SCRIPT_DIR}\" ; \"${OPENROAD_EXE}\" -python -exit \"${SCRIPT_DIR}/cts_watermark_verify.py\""
    ;;
  verify_stages)
    run_in_singularity "export PYTHONPATH=\"${SCRIPT_DIR}:\${PYTHONPATH:-}\" ; cd \"${SCRIPT_DIR}\" ; \"${OPENROAD_EXE}\" -python -exit \"${SCRIPT_DIR}/cts_watermark_verify_stages.py\""
    ;;
  all)
    "$0" embed
    export WM_CTS_VERIFY_INPUT="${WM_CTS_OUTPUT_ODB:?set WM_CTS_OUTPUT_ODB before run all}"
    export WM_CELL_LIST="${WM_CTS_OUTPUT_CSV:?set WM_CTS_OUTPUT_CSV before run all}"
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
