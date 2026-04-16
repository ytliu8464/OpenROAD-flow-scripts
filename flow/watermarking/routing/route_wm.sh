#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Run routing (NDR) watermark embed/verify inside Singularity (see README.md).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENROAD_EXE="${OPENROAD_EXE:-/home/fetzfs_projects/MISC-ytliu/watermarking/OR0415/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad}"
SIF="${SINGULARITY_SIF:-/home/tool/singularity/images/ispd26.sif}"

usage() {
  cat <<EOF
Usage:
  $0 embed
  $0 verify
  $0 all

Environment (embed):
  WM_RT_INPUT           post-CTS .odb (e.g. .../4_cts.odb)
  WM_RT_OUTPUT_ODB      output .odb with NDR on selected nets
  WM_RT_OUTPUT_CSV      optional CSV of watermark nets
  WM_MESSAGE            watermark message string
  WM_KEY                secret key string
  WM_RT_NUM_NETS        number of nets (default 100)
  WM_RT_SPACING_UM      NDR spacing on metal2/metal3 in microns (default 0.09)
  WM_RT_NDR_NAME        NDR name (default wm_spacing_ndr)
  WM_RT_TARGET_LAYERS   comma-separated layers (default metal2,metal3)

Environment (verify):
  WM_RT_VERIFY_INPUT     .odb to check (post-route recommended)
  WM_RT_VERIFY_CSV       optional per-net CSV report
  (reuse WM_MESSAGE, WM_KEY, WM_RT_NUM_NETS, WM_RT_SPACING_UM, WM_RT_NDR_NAME, WM_RT_TARGET_LAYERS)

Example:
  export WM_RT_INPUT=.../4_cts.odb WM_RT_OUTPUT_ODB=.../4_cts_rt_wm.odb WM_RT_OUTPUT_CSV=.../wm_route_nets.csv
  export WM_MESSAGE='hello' WM_KEY='secret' WM_RT_NUM_NETS=100
  $0 embed
  # after GRT+DRT:
  export WM_RT_VERIFY_INPUT=.../6_final.odb
  $0 verify
EOF
}

run_in_singularity() {
  local inner_cmd="$1"
  singularity exec -B /home -B /tmp --bind /tmp/.X11-unix -e "$SIF" \
    env \
    OPENROAD_EXE="${OPENROAD_EXE}" \
    WM_RT_INPUT="${WM_RT_INPUT:-}" \
    WM_RT_OUTPUT_ODB="${WM_RT_OUTPUT_ODB:-}" \
    WM_RT_OUTPUT_CSV="${WM_RT_OUTPUT_CSV:-}" \
    WM_RT_VERIFY_INPUT="${WM_RT_VERIFY_INPUT:-}" \
    WM_RT_VERIFY_CSV="${WM_RT_VERIFY_CSV:-}" \
    WM_MESSAGE="${WM_MESSAGE:-}" \
    WM_KEY="${WM_KEY:-}" \
    WM_RT_NUM_NETS="${WM_RT_NUM_NETS:-100}" \
    WM_RT_SPACING_UM="${WM_RT_SPACING_UM:-0.09}" \
    WM_RT_NDR_NAME="${WM_RT_NDR_NAME:-wm_spacing_ndr}" \
    WM_RT_TARGET_LAYERS="${WM_RT_TARGET_LAYERS:-metal2,metal3}" \
    WM_RT_P_NULL="${WM_RT_P_NULL:-1e-12}" \
    bash -lc "$inner_cmd"
}

case "${1:-}" in
  embed)
    run_in_singularity "export PYTHONPATH=\"${SCRIPT_DIR}:\${PYTHONPATH:-}\" ; cd \"${SCRIPT_DIR}\" ; \"${OPENROAD_EXE}\" -python -exit \"${SCRIPT_DIR}/route_watermark_embed.py\""
    ;;
  verify)
    run_in_singularity "export PYTHONPATH=\"${SCRIPT_DIR}:\${PYTHONPATH:-}\" ; cd \"${SCRIPT_DIR}\" ; \"${OPENROAD_EXE}\" -python -exit \"${SCRIPT_DIR}/route_watermark_verify.py\""
    ;;
  all)
    "$0" embed
    export WM_RT_VERIFY_INPUT="${WM_RT_OUTPUT_ODB:?set WM_RT_OUTPUT_ODB for embed before run all}"
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
