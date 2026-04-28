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
# Embed reads/writes ODB under flow/results/${PLATFORM}/${DESIGN}/${WM_FLOW_VARIANT}.
# Use ``base`` for default ASAP7 SDC; ``base_tcp540``/``base_tcp1455`` for tighter
# corner sweeps. Should match the WM_FLOW_VARIANT used by run_ppa.sh.
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
export WM_OUTPUT_ODB="${AES_RES}/3_place_order_wm_v2.odb"
export WM_OUTPUT_DEF="${AES_RES}/3_place_order_wm_v2.def"
export WM_OUTPUT_CELL_LIST="${AES_RES}/wm_place_order_embed_v2.csv"
export WM_VERIFY_CELL_LIST="${AES_RES}/wm_place_order_verify_v2.csv"
# ------------------------------------------------------------
export WM_MESSAGE="${WM_MESSAGE:-place-ordering-wm-${DESIGN}}"
export WM_GRID_NX="${WM_GRID_NX:-6}"
export WM_GRID_NY="${WM_GRID_NY:-6}"
export WM_PAIR_DIST_UM="${WM_PAIR_DIST_UM:-1}"
export WM_PAIRS_PER_TILE="${WM_PAIRS_PER_TILE:-4}"
export WM_GROUPS_PER_TILE="${WM_GROUPS_PER_TILE:-2}"
export WM_USE_GROUPS="${WM_USE_GROUPS:-0}"
export WM_SLACK_THRESHOLD_NS="${WM_SLACK_THRESHOLD_NS:-0.10}"
export WM_NEIGHBOR_SLACK_MARGIN_NS="${WM_NEIGHBOR_SLACK_MARGIN_NS:-0.00}"
export WM_HPWL_EPS_PAIR_DBU="${WM_HPWL_EPS_PAIR_DBU:-100}"
export WM_HPWL_EPS_GROUP_DBU="${WM_HPWL_EPS_GROUP_DBU:-100}"
export WM_PAIR_NEIGHBOR_K="${WM_PAIR_NEIGHBOR_K:-3}"
export WM_TILE_OVERSAMPLE="${WM_TILE_OVERSAMPLE:-4}"
export WM_FANOUT_DIFF_MAX="${WM_FANOUT_DIFF_MAX:-4}"
export WM_HPWL_CACHE="${WM_HPWL_CACHE:-1}"
export WM_HPWL_NET_FANOUT_MAX="${WM_HPWL_NET_FANOUT_MAX:-64}"
export WM_MIN_PAIRS_TOTAL="${WM_MIN_PAIRS_TOTAL:-64}"
export WM_PAIR_NEIGHBOR_K_RELAXED="${WM_PAIR_NEIGHBOR_K_RELAXED:-8}"
export WM_HPWL_EPS_PAIR_RELAXED_DBU="${WM_HPWL_EPS_PAIR_RELAXED_DBU:-200}"
export WM_CRIT_BIN_RELAXED_NS="${WM_CRIT_BIN_RELAXED_NS:-0.20}"
export WM_TILE_TOUCH_FRAC_MAX="${WM_TILE_TOUCH_FRAC_MAX:-0.05}"
export WM_TILE_TOUCH_FLOOR_PAIRS="${WM_TILE_TOUCH_FLOOR_PAIRS:-4}"
export WM_POST_GUARD="${WM_POST_GUARD:-1}"
export WM_POST_GUARD_FINAL_CHECK="${WM_POST_GUARD_FINAL_CHECK:-1}"
export WM_GUARD_DEGRADE_NS="${WM_GUARD_DEGRADE_NS:-0.02}"
export WM_MAX_DISP_X="${WM_MAX_DISP_X:-5}"
export WM_MAX_DISP_Y="${WM_MAX_DISP_Y:-5}"

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
log "cascade: neighbor_k=${WM_PAIR_NEIGHBOR_K} oversample=${WM_TILE_OVERSAMPLE} fanout_diff=${WM_FANOUT_DIFF_MAX} hpwl_eps=${WM_HPWL_EPS_PAIR_DBU} cache=${WM_HPWL_CACHE} net_fanout_max=${WM_HPWL_NET_FANOUT_MAX}"
log "fallback: min_pairs=${WM_MIN_PAIRS_TOTAL} neighbor_k=${WM_PAIR_NEIGHBOR_K_RELAXED} hpwl_eps=${WM_HPWL_EPS_PAIR_RELAXED_DBU} crit_bin=${WM_CRIT_BIN_RELAXED_NS}ns"
log "timing : slack>=${WM_SLACK_THRESHOLD_NS}ns, neighbor_margin=${WM_NEIGHBOR_SLACK_MARGIN_NS}ns, post_guard=${WM_POST_GUARD}, guard_degrade=${WM_GUARD_DEGRADE_NS}ns, sdc=${WM_SDC}"
log "tile   : touch_frac<=${WM_TILE_TOUCH_FRAC_MAX} (floor=${WM_TILE_TOUCH_FLOOR_PAIRS} pairs), dpl_max_disp=${WM_MAX_DISP_X}/${WM_MAX_DISP_Y}um"

log "starting embed + verify"
"${SCRIPT_DIR}/place_wm.sh" all
