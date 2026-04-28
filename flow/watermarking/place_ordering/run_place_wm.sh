#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# gen_key/ + place_ordering embed + verify example.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_START_EPOCH="$(date +%s)"

ts() {
  date "+%Y-%m-%d %H:%M:%S"
}

log() {
  echo "[$(ts)] [run_place_wm] $*"
}

finish_log() {
  local status=$?
  local end_epoch elapsed
  end_epoch="$(date +%s)"
  elapsed=$((end_epoch - RUN_START_EPOCH))
  echo "[$(ts)] [run_place_wm] finished status=${status} elapsed=${elapsed}s"
  exit "${status}"
}
trap finish_log EXIT

DESIGN="${DESIGN:-swerv_wrapper}"
PLATFORM="${PLATFORM:-asap7}"
OWNER_ID="${OWNER_ID:-yiting}"
WM_FLOW_VARIANT="${WM_FLOW_VARIANT:-base_tcp1455}"

LOG_DIR="${SCRIPT_DIR}/wm_log"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/${DESIGN}_run_place_wm_ordering_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "${LOG_FILE}") 2>&1
log "logging to ${LOG_FILE}"
WM_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
GEN_KEY_DIR="${WM_ROOT}/gen_key"


export AES_RES="${SCRIPT_DIR}/../../results/${PLATFORM}/${DESIGN}/${WM_FLOW_VARIANT}"
mkdir -p "${AES_RES}"

KEYS_DIR="${GEN_KEY_DIR}/keys"
if [[ ! -f "${KEYS_DIR}/sk.pem" ]]; then
  log "generating owner keypair in ${KEYS_DIR}"
  ( cd "${GEN_KEY_DIR}" && ./gen_key.sh keygen --owner-id "${OWNER_ID}" --out-dir keys )
fi

BUNDLE_DIR="${GEN_KEY_DIR}/out/${DESIGN}"
SEED_PLACEMENT="${BUNDLE_DIR}/seed_placement.hex"
if [[ ! -f "${SEED_PLACEMENT}" ]]; then
  log "signing bundle for design=${DESIGN}"
  ( cd "${GEN_KEY_DIR}" && ./gen_key.sh sign \
      --sk keys/sk.pem --pk keys/pk.pem \
      --owner-id "${OWNER_ID}" --design-id "${DESIGN}" \
      --out-dir "out/${DESIGN}" --force )
fi

export WM_SEED_HEX="${SEED_PLACEMENT}"
export WM_INPUT="${AES_RES}/3_place.odb"
# --------------------- output names ---------------------
export WM_OUTPUT_ODB="${AES_RES}/3_place_order_wm_1um.odb"
export WM_OUTPUT_DEF="${AES_RES}/3_place_order_wm_1um.def"
export WM_OUTPUT_CELL_LIST="${AES_RES}/wm_place_order_embed_1um.csv"
export WM_VERIFY_CELL_LIST="${AES_RES}/wm_place_order_verify_1um.csv"
# ------------------------------------------------------------
export WM_MESSAGE="${WM_MESSAGE:-place-ordering-wm-${DESIGN}}"
export WM_GRID_NX="${WM_GRID_NX:-8}"
export WM_GRID_NY="${WM_GRID_NY:-8}"
export WM_PAIR_DIST_UM="${WM_PAIR_DIST_UM:-1}"
export WM_PAIRS_PER_TILE="${WM_PAIRS_PER_TILE:-4}"
export WM_GROUPS_PER_TILE="${WM_GROUPS_PER_TILE:-2}"
export WM_USE_GROUPS="${WM_USE_GROUPS:-0}"
export WM_SLACK_THRESHOLD_NS="${WM_SLACK_THRESHOLD_NS:-0.05}"

FLOW_DIR="$(cd "${WM_ROOT}/.." && pwd)"
if [[ -z "${WM_LIB_FILES:-}" ]]; then
  _design_config="./designs/${PLATFORM}/${DESIGN}/config.mk"
  _make_out="$(make -C "${FLOW_DIR}" print-LIB_FILES \
    DESIGN_CONFIG="${_design_config}" \
    CORNER="${WM_CORNER:-TC}" 2>/dev/null || true)"
  WM_LIB_FILES="$(echo "${_make_out}" | grep "^LIB_FILES:" | sed 's/^LIB_FILES:[[:space:]]*//')"
  if [[ -z "${WM_LIB_FILES}" ]]; then
    log "WARNING: could not auto-discover LIB_FILES" >&2
  fi
fi
export WM_LIB_FILES
export WM_SDC="${WM_SDC:-${AES_RES}/3_place.sdc}"
export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"

log "design : ${DESIGN}/${PLATFORM}/${WM_FLOW_VARIANT}"
log "seed   : ${WM_SEED_HEX}"
log "input  : ${WM_INPUT}"
log "output : ${WM_OUTPUT_ODB}"
log "grid   : ${WM_GRID_NX}x${WM_GRID_NY}, pair_dist=${WM_PAIR_DIST_UM}um, pairs/tile=${WM_PAIRS_PER_TILE}, groups/tile=${WM_GROUPS_PER_TILE}, use_groups=${WM_USE_GROUPS}"
log "timing : slack>=${WM_SLACK_THRESHOLD_NS}ns, sdc=${WM_SDC}"

log "starting embed + verify"
"${SCRIPT_DIR}/place_wm.sh" all
