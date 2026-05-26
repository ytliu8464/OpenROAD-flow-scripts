#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# P-only PDMarks: place_ordering embed + flow continuation through finish.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
ensure_keys

PLACE_DIR="${FLOW_HOME}/watermarking/place_ordering"
export FLOW_VARIANT="${FLOW_VARIANT:-pdmarks-p-only}"
apply_adaptive_wm_params place
# experiments/results/<plat>/<nickname>/ mirrors the ORFS layout.
export WM_RESULTS="${WM_RESULTS_HOME}/${PLATFORM}/${DESIGN_NICKNAME}/${FLOW_VARIANT}"

log "place embed"
DESIGN="${DESIGN}" DESIGN_NICKNAME="${DESIGN_NICKNAME}" PLATFORM="${PLATFORM}" \
  WM_FLOW_VARIANT="${WM_FLOW_VARIANT}" \
  FLOW_VARIANT="${FLOW_VARIANT}" \
  WM_RESULTS="${WM_RESULTS}" \
  "${PLACE_DIR}/run_place_wm.sh"

log "continue flow (CTS + route + finish) from watermarked 3_place"
DESIGN="${DESIGN}" DESIGN_NICKNAME="${DESIGN_NICKNAME}" PLATFORM="${PLATFORM}" \
  WM_FLOW_VARIANT="${WM_FLOW_VARIANT}" \
  FLOW_VARIANT="${FLOW_VARIANT}" \
  WM_RESULTS="${WM_RESULTS}" \
  DP_ODB="${WM_RESULTS}/3_place_order_wm.odb" \
  "${PLACE_DIR}/run_ppa.sh"
log "P-only done; results under ${WM_RESULTS}"
