#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# P-only PDMarks: place_ordering embed + flow continuation through finish.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
ensure_keys

PLACE_DIR="${FLOW_HOME}/watermarking/place_ordering"
export FLOW_VARIANT="${FLOW_VARIANT:-pdmarks-p-only}"

log "place embed"
DESIGN="${DESIGN}" PLATFORM="${PLATFORM}" WM_FLOW_VARIANT="${WM_FLOW_VARIANT}" \
  "${PLACE_DIR}/run_place_wm.sh"

log "continue flow (CTS + route + finish) from watermarked 3_place"
DESIGN="${DESIGN}" PLATFORM="${PLATFORM}" WM_FLOW_VARIANT="${WM_FLOW_VARIANT}" \
  FLOW_VARIANT="${FLOW_VARIANT}" \
  DP_ODB="${FLOW_RES}/3_place_order_wm_v2.odb" \
  "${PLACE_DIR}/run_ppa.sh"
log "P-only done; results under flow/results/${PLATFORM}/${DESIGN}/${FLOW_VARIANT}"
