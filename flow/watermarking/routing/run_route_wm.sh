#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Example env for AES / NanGate45: embed NDR on post-CTS ODB from the default flow.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Results relative to flow/watermarking/routing -> flow/results/...
export FLOW_RES="${FLOW_RES:-../../results/nangate45/aes/watermarking-test1}"

export WM_RT_INPUT="${WM_RT_INPUT:-${FLOW_RES}/4_cts.odb}"
export WM_RT_OUTPUT_ODB="${WM_RT_OUTPUT_ODB:-${FLOW_RES}/4_cts_rt_wm.odb}"
export WM_RT_OUTPUT_CSV="${WM_RT_OUTPUT_CSV:-${FLOW_RES}/wm_route_nets_embed.csv}"

export WM_MESSAGE="${WM_MESSAGE:-Routing-with-watermark-test}"
export WM_KEY="${WM_KEY:-This-is-a-secret-key}"
export WM_RT_NUM_NETS="${WM_RT_NUM_NETS:-100}"
export WM_RT_SPACING_UM="${WM_RT_SPACING_UM:-0.09}"

# After global + detailed route, verify the final DB, e.g.:
#   export WM_RT_VERIFY_INPUT="${FLOW_RES}/6_final.odb"
#   export WM_RT_VERIFY_CSV="${FLOW_RES}/wm_route_nets_verify.csv"
#   ./run_route_wm.sh verify

cmd="${1:-embed}"
if [[ "$cmd" == "all" ]]; then
  export WM_RT_VERIFY_INPUT="${WM_RT_VERIFY_INPUT:-${WM_RT_OUTPUT_ODB}}"
fi

"${SCRIPT_DIR}/route_wm.sh" "$cmd"
