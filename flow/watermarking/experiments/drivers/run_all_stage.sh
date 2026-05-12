#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# All-stage PDMarks: P-embed -> CTS-embed -> route (with R-WM hook) -> finish.
#
# Chained pipeline:
#   1. place_ordering embed (3_place.odb -> 3_place_order_wm_v2.odb)
#   2. make wm_cts_and_route up to CTS only, starting from the watermarked
#      placement.  We use `make cts` with DP_ODB pointing at the watermarked
#      placement; this writes 4_cts.odb under the new FLOW_VARIANT.
#   3. cts_v2 embed (4_cts.odb -> 4_cts_wm.odb)
#   4. make wm_route_wrong_way with CTS_ODB=4_cts_wm.odb and
#      PRE_GLOBAL_ROUTE_TCL=routing_wrong_way/pre_route_watermark.tcl, which
#      reads seed_routing.hex and biases DRT.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
ensure_keys

PLACE_DIR="${FLOW_HOME}/watermarking/place_ordering"
CTS_DIR="${FLOW_HOME}/watermarking/cts_v2"
ROUTE_DIR="${FLOW_HOME}/watermarking/routing_wrong_way"
export FLOW_VARIANT="${FLOW_VARIANT:-pdmarks-all-stage}"
PPA_RES="${FLOW_HOME}/results/${PLATFORM}/${DESIGN}/${FLOW_VARIANT}"

# 1) Placement embed
log "P embed"
DESIGN="${DESIGN}" PLATFORM="${PLATFORM}" WM_FLOW_VARIANT="${WM_FLOW_VARIANT}" \
WM_OUTPUT_CELL_LIST="${FLOW_RES}/wm_place_order_embed_all_stage.csv" \
WM_VERIFY_CELL_LIST="${FLOW_RES}/wm_place_order_verify_all_stage.csv" \
  "${PLACE_DIR}/run_place_wm.sh"

# 2) CTS up to 4_cts.odb only, starting from the watermarked placement.
#    We call `make cts` directly so the route/finish phases are not yet run.
SIF="${SINGULARITY_SIF:-/home/tool/singularity/images/ispd26.sif}"
log "CTS only (post-P)"
if [[ -z "${SINGULARITY_NAME:-}" ]]; then
  singularity exec -B /home -B /tmp -e "${SIF}" \
    env PROJ_DIR="${PROJ_DIR}" OPENROAD_EXE="${OPENROAD_EXE}" \
        FLOW_HOME="${FLOW_HOME}" DESIGN="${DESIGN}" PLATFORM="${PLATFORM}" \
        FLOW_VARIANT="${FLOW_VARIANT}" SKIP_DP_WM=1 \
        DP_ODB="${FLOW_RES}/3_place_order_wm.odb" \
        INPUTS_DIR="${FLOW_HOME}/OR_inputs/place_wm/${PLATFORM}/${DESIGN}" \
    bash -lc "make -C '${FLOW_HOME}' \
        DESIGN_CONFIG='${FLOW_HOME}/designs/${PLATFORM}/${DESIGN}/config.mk' \
        copy_inputs cts"
else
  SKIP_DP_WM=1 \
  DP_ODB="${FLOW_RES}/3_place_order_wm.odb" \
  INPUTS_DIR="${FLOW_HOME}/OR_inputs/place_wm/${PLATFORM}/${DESIGN}" \
  make -C "${FLOW_HOME}" \
        DESIGN_CONFIG="${FLOW_HOME}/designs/${PLATFORM}/${DESIGN}/config.mk" \
        FLOW_VARIANT="${FLOW_VARIANT}" copy_inputs cts
fi

# 3) CTS embed on the just-produced 4_cts.odb (under the new FLOW_VARIANT dir).
log "C embed"
DESIGN="${DESIGN}" PLATFORM="${PLATFORM}" \
WM_FLOW_VARIANT="${FLOW_VARIANT}" \
WM_CTS_INPUT="${PPA_RES}/4_cts.odb" \
WM_CTS_OUTPUT_ODB="${PPA_RES}/4_cts_wm.odb" \
WM_CTS_OUTPUT_CSV="${PPA_RES}/wm_cts_pairs_embed_all_stage.csv" \
  "${CTS_DIR}/run_cts_wm.sh"

# 4) Route + finish with R-WM hook.
log "R embed (route with wrong-way bias)"
DESIGN="${DESIGN}" PLATFORM="${PLATFORM}" \
WM_FLOW_VARIANT="${FLOW_VARIANT}" \
FLOW_VARIANT="${FLOW_VARIANT}-routed" \
CTS_ODB="${PPA_RES}/4_cts_wm.odb" \
  "${ROUTE_DIR}/run.sh"

log "all-stage done; results under flow/results/${PLATFORM}/${DESIGN}/${FLOW_VARIANT}-routed"
