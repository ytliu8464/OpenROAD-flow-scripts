#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# C-only PDMarks: cts_v2 embed + route + finish.  The reference 4_cts.odb is
# already on disk under FLOW_RES.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
ensure_keys

CTS_DIR="${FLOW_HOME}/watermarking/cts_v2"
export FLOW_VARIANT="${FLOW_VARIANT:-pdmarks-c-only}"
apply_adaptive_wm_params cts
export WM_RESULTS="${WM_RESULTS_HOME}/${PLATFORM}/${DESIGN_NICKNAME}/${FLOW_VARIANT}"

log "cts embed"
DESIGN="${DESIGN}" DESIGN_NICKNAME="${DESIGN_NICKNAME}" PLATFORM="${PLATFORM}" \
  WM_FLOW_VARIANT="${WM_FLOW_VARIANT}" \
  FLOW_VARIANT="${FLOW_VARIANT}" \
  WM_RESULTS="${WM_RESULTS}" \
  "${CTS_DIR}/run_cts_wm.sh"

log "continue route + finish from watermarked 4_cts"
DESIGN="${DESIGN}" DESIGN_NICKNAME="${DESIGN_NICKNAME}" PLATFORM="${PLATFORM}" \
  WM_FLOW_VARIANT="${WM_FLOW_VARIANT}" \
  FLOW_VARIANT="${FLOW_VARIANT}" \
  WM_RESULTS="${WM_RESULTS}" \
  "${CTS_DIR}/run_ppa.sh"
log "C-only done; results under ${WM_RESULTS}"
