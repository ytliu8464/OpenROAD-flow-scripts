#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Verify routing watermark on a post-route ODB (e.g. 6_final.odb or 5_route.odb).
# Uses the same message/key/params that were used during embed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Results directory — override via env if needed
export FLOW_RES="${FLOW_RES:-./results/nangate45/aes/wm-test1-route-ppa}"

# Input ODB to verify (post-route)
export WM_RT_VERIFY_INPUT="${WM_RT_VERIFY_INPUT:-${FLOW_RES}/6_final.odb}"

# Optional per-net CSV report output
export WM_RT_VERIFY_CSV="${WM_RT_VERIFY_CSV:-${FLOW_RES}/wm_route_nets_verify.csv}"

# Watermark parameters — must match those used during embed
export WM_MESSAGE="${WM_MESSAGE:-Routing-with-watermark-test}"
export WM_KEY="${WM_KEY:-This-is-a-secret-key}"
export WM_RT_NUM_NETS="${WM_RT_NUM_NETS:-100}"
export WM_RT_SPACING_UM="${WM_RT_SPACING_UM:-0.09}"

echo "[INFO] Verifying watermark in: ${WM_RT_VERIFY_INPUT}"
echo "[INFO] CSV report:             ${WM_RT_VERIFY_CSV}"

"${SCRIPT_DIR}/route_wm.sh" verify
