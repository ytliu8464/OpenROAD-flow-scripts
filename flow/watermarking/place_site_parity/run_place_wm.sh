#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Drive gen_key/ + place_site_parity/place_wm.sh for the AES example.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DESIGN="${DESIGN:-jpeg}"
PLATFORM="${PLATFORM:-asap7}"
OWNER_ID="${OWNER_ID:-yiting}"
WM_FLOW_VARIANT="${WM_FLOW_VARIANT:-base_tcp540}"

LOG_DIR="${SCRIPT_DIR}/wm_log"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/${DESIGN}_run_place_wm_k5_tcp540_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "${LOG_FILE}") 2>&1
echo "[run_place_wm] logging to ${LOG_FILE}"
WM_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
GEN_KEY_DIR="${WM_ROOT}/gen_key"

export AES_RES="${SCRIPT_DIR}/../../results/${PLATFORM}/${DESIGN}/${WM_FLOW_VARIANT}"
mkdir -p "${AES_RES}"

# 1) Ensure gen_key/ has a keypair and a signed bundle for this design.
KEYS_DIR="${GEN_KEY_DIR}/keys"
if [[ ! -f "${KEYS_DIR}/sk.pem" ]]; then
  echo "[run_place_wm] generating owner keypair in ${KEYS_DIR}"
  ( cd "${GEN_KEY_DIR}" && ./gen_key.sh keygen --owner-id "${OWNER_ID}" --out-dir keys )
fi

BUNDLE_DIR="${GEN_KEY_DIR}/out/${DESIGN}"
SEED_PLACEMENT="${BUNDLE_DIR}/seed_placement.hex"
if [[ ! -f "${SEED_PLACEMENT}" ]]; then
  echo "[run_place_wm] signing binding message for design=${DESIGN}"
  ( cd "${GEN_KEY_DIR}" && ./gen_key.sh sign \
      --sk keys/sk.pem --pk keys/pk.pem \
      --owner-id "${OWNER_ID}" --design-id "${DESIGN}" \
      --out-dir "out/${DESIGN}" --force )
fi

export WM_SEED_HEX="${SEED_PLACEMENT}"
export WM_INPUT="${AES_RES}/3_place.odb"
# --------------------- customized output name ---------------------
export WM_OUTPUT_ODB="${AES_RES}/3_place_siteparity_wm.odb"
export WM_OUTPUT_DEF="${AES_RES}/3_place_siteparity_wm.def"
export WM_OUTPUT_CELL_LIST="${AES_RES}/wm_cells_embed.csv"
export WM_VERIFY_CELL_LIST="${AES_RES}/wm_cells_verify.csv"
# ---------------------  ---------------------
export WM_MESSAGE="${WM_MESSAGE:-site-parity-watermark-${DESIGN}}"
export WM_GRID_NX="${WM_GRID_NX:-6}"
export WM_GRID_NY="${WM_GRID_NY:-6}"
export WM_K_PERCENT="${WM_K_PERCENT:-5}"
export WM_SLACK_THRESHOLD_NS="${WM_SLACK_THRESHOLD_NS:-0.1}"
export WM_MAX_DISP_X="${WM_MAX_DISP_X:-50}"
export WM_MAX_DISP_Y="${WM_MAX_DISP_Y:-50}"

# Liberty files for STA slack filtering.
# Auto-discovered from the flow's Makefile (TC corner) when not set by the caller.
# Override by setting WM_LIB_FILES explicitly before running this script.
FLOW_DIR="$(cd "${WM_ROOT}/.." && pwd)"
if [[ -z "${WM_LIB_FILES:-}" ]]; then
  _design_config="./designs/${PLATFORM}/${DESIGN}/config.mk"
  _make_out="$(make -C "${FLOW_DIR}" print-LIB_FILES \
    DESIGN_CONFIG="${_design_config}" \
    CORNER="${WM_CORNER:-TC}" 2>/dev/null || true)"
  WM_LIB_FILES="$(echo "${_make_out}" | grep "^LIB_FILES:" | sed 's/^LIB_FILES:[[:space:]]*//')"
  if [[ -z "${WM_LIB_FILES}" ]]; then
    echo "[run_place_wm] WARNING: could not auto-discover LIB_FILES; STA slack filter will be skipped" >&2
  else
    echo "[run_place_wm] libs    : (auto) PLATFORM=${PLATFORM} CORNER=${WM_CORNER:-TC}"
  fi
fi
export WM_LIB_FILES
export WM_SDC="${WM_SDC:-${AES_RES}/3_place.sdc}"

echo "[run_place_wm] seed   : ${WM_SEED_HEX}"
echo "[run_place_wm] grid   : ${WM_GRID_NX}x${WM_GRID_NY}, k%=${WM_K_PERCENT}, slack>=${WM_SLACK_THRESHOLD_NS}ns"
echo "[run_place_wm] input  : ${WM_INPUT}"
echo "[run_place_wm] output : ${WM_OUTPUT_ODB}"

# 2) Run embed + verify.
"${SCRIPT_DIR}/place_wm.sh" all
