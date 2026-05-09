#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Drive gen_key/ + cts_wm.sh for the AES example (NanGate45).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

LOG_DIR="${SCRIPT_DIR}/wm_log"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/aes_run_cts_wm_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "${LOG_FILE}") 2>&1
echo "[run_place_wm] logging to ${LOG_FILE}"
WM_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
GEN_KEY_DIR="${WM_ROOT}/gen_key"

DESIGN="${DESIGN:-aes}"
PLATFORM="${PLATFORM:-asap7}"
OWNER_ID="${OWNER_ID:-yiting}"
WM_FLOW_VARIANT="${WM_FLOW_VARIANT:-base_tcp612}"

export FLOW_RES="${SCRIPT_DIR}/../../results/${PLATFORM}/${DESIGN}/${WM_FLOW_VARIANT}"
mkdir -p "${FLOW_RES}"

# 1) Ensure gen_key/ has a keypair and a signed bundle with seed_cts.hex.
KEYS_DIR="${GEN_KEY_DIR}/keys"
if [[ ! -f "${KEYS_DIR}/sk.pem" ]]; then
  echo "[run_cts_wm] generating owner keypair in ${KEYS_DIR}"
  ( cd "${GEN_KEY_DIR}" && ./gen_key.sh keygen --owner-id "${OWNER_ID}" --out-dir keys )
fi

BUNDLE_DIR="${GEN_KEY_DIR}/out/${DESIGN}"
SEED_CTS="${BUNDLE_DIR}/seed_cts.hex"
if [[ ! -f "${SEED_CTS}" ]]; then
  echo "[run_cts_wm] signing binding message for design=${DESIGN}"
  ( cd "${GEN_KEY_DIR}" && ./gen_key.sh sign \
      --sk keys/sk.pem --pk keys/pk.pem \
      --owner-id "${OWNER_ID}" --design-id "${DESIGN}" \
      --out-dir "out/${DESIGN}" --force )
fi

export WM_SEED_HEX="${SEED_CTS}"
export WM_CTS_INPUT="${WM_CTS_INPUT:-${FLOW_RES}/4_cts.odb}"
# --------------------- customized output name ---------------------
export WM_CTS_OUTPUT_ODB="${WM_CTS_OUTPUT_ODB:-${FLOW_RES}/4_cts_wm.odb}"
export WM_CTS_OUTPUT_CSV="${WM_CTS_OUTPUT_CSV:-${FLOW_RES}/wm_cts_pairs_embed.csv}"
# --------------------- customized output name ---------------------

export WM_CTS_NUM_PAIRS="${WM_CTS_NUM_PAIRS:-32}"
export WM_CTS_SIBLING_DIST_UM="${WM_CTS_SIBLING_DIST_UM:-50}"
export WM_CTS_DELTA_SITES="${WM_CTS_DELTA_SITES:-10}"
export WM_CTS_FANOUT_MARGIN="${WM_CTS_FANOUT_MARGIN:-2}"
export WM_CTS_SLEW_HEADROOM_FRAC="${WM_CTS_SLEW_HEADROOM_FRAC:-0.20}"
export WM_CTS_SKEW_SLACK_PS="${WM_CTS_SKEW_SLACK_PS:-20}"
export WM_CTS_MAX_FANOUT="${WM_CTS_MAX_FANOUT:-32}"
export WM_CTS_MAX_TRANSITION_NS="${WM_CTS_MAX_TRANSITION_NS:-0.4}"
export WM_CTS_MAX_ATTEMPTS="${WM_CTS_MAX_ATTEMPTS:-3}"

# Liberty files for STA timing checks (skew/slew headroom).
# Auto-discovered from the ORFS Makefile when not set by the caller.
FLOW_DIR="$(cd "${WM_ROOT}/.." && pwd)"
if [[ -z "${WM_LIB_FILES:-}" ]]; then
  _design_config="./designs/${PLATFORM}/${DESIGN}/config.mk"
  _make_out="$(make -C "${FLOW_DIR}" print-LIB_FILES \
    DESIGN_CONFIG="${_design_config}" \
    CORNER="${WM_CORNER:-BC}" 2>/dev/null || true)"
  WM_LIB_FILES="$(echo "${_make_out}" | grep "^LIB_FILES:" | sed 's/^LIB_FILES:[[:space:]]*//')"
  if [[ -z "${WM_LIB_FILES}" ]]; then
    echo "[run_cts_wm] WARNING: could not auto-discover LIB_FILES; timing checks will be best-effort" >&2
  else
    echo "[run_cts_wm] libs    : (auto) PLATFORM=${PLATFORM} CORNER=${WM_CORNER:-BC}"
  fi
fi
export WM_LIB_FILES

export WM_SDC="${WM_SDC:-${FLOW_RES}/4_cts.sdc}"
export WM_SETRC="${WM_SETRC:-${FLOW_DIR}/platforms/${PLATFORM}/setRC.tcl}"

echo "[run_cts_wm] seed   : ${WM_SEED_HEX}"
echo "[run_cts_wm] input  : ${WM_CTS_INPUT}"
echo "[run_cts_wm] output : ${WM_CTS_OUTPUT_ODB}"
echo "[run_cts_wm] csv    : ${WM_CTS_OUTPUT_CSV}"
echo "[run_cts_wm] pairs  : ${WM_CTS_NUM_PAIRS}, sibling<=${WM_CTS_SIBLING_DIST_UM}um, delta=${WM_CTS_DELTA_SITES} sites"

cmd="${1:-all}"
"${SCRIPT_DIR}/cts_wm.sh" "${cmd}"
