#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# All-stage PDMarks: P-embed -> CTS-embed -> route (with R-WM hook) -> finish.
#
# Chained pipeline:
#   1. place_ordering embed (3_place.odb -> 3_place_order_wm.odb)
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
# experiments/results/<plat>/<nickname>/ mirrors the ORFS layout.
export WM_RESULTS="${WM_RESULTS_HOME}/${PLATFORM}/${DESIGN_NICKNAME}/${FLOW_VARIANT}"
apply_adaptive_wm_params all

# 1) Placement embed
log "P embed"
DESIGN="${DESIGN}" DESIGN_NICKNAME="${DESIGN_NICKNAME}" PLATFORM="${PLATFORM}" \
WM_FLOW_VARIANT="${WM_FLOW_VARIANT}" \
FLOW_VARIANT="${FLOW_VARIANT}" \
WM_RESULTS="${WM_RESULTS}" \
WM_OUTPUT_CELL_LIST="${WM_RESULTS}/wm_place_order_embed_all_stage.csv" \
WM_VERIFY_CELL_LIST="${WM_RESULTS}/wm_place_order_verify_all_stage.csv" \
  "${PLACE_DIR}/run_place_wm.sh"

# 2) CTS up to 4_cts.odb only, starting from the watermarked placement.
#    We call `make cts` directly so the route/finish phases are not yet run.
#    DESIGN_CONFIG lives under designs/<plat>/<DESIGN_NAME>/, but OR_inputs
#    are staged under OR_inputs/.../<plat>/<DESIGN_NICKNAME>/.
SIF="${SINGULARITY_SIF:-/home/tool/singularity/images/ispd26.sif}"
log "CTS only (post-P)"
if [[ -z "${SINGULARITY_NAME:-}" ]]; then
  singularity exec -B /home -B /tmp -e "${SIF}" \
    env PROJ_DIR="${PROJ_DIR}" OPENROAD_EXE="${OPENROAD_EXE}" \
        FLOW_HOME="${FLOW_HOME}" DESIGN="${DESIGN}" \
        DESIGN_NICKNAME="${DESIGN_NICKNAME}" PLATFORM="${PLATFORM}" \
        WORK_HOME="${EXPERIMENTS_HOME}" \
        FLOW_VARIANT="${FLOW_VARIANT}" SKIP_DP_WM=1 \
        DP_ODB="${WM_RESULTS}/3_place_order_wm.odb" \
        INPUTS_DIR="${FLOW_HOME}/OR_inputs/place_wm/${PLATFORM}/${DESIGN_NICKNAME}" \
    bash -lc "make -C '${FLOW_HOME}' \
        DESIGN_CONFIG='${FLOW_HOME}/designs/${PLATFORM}/${DESIGN}/config.mk' \
        copy_inputs cts"
else
  SKIP_DP_WM=1 \
  DP_ODB="${WM_RESULTS}/3_place_order_wm.odb" \
  INPUTS_DIR="${FLOW_HOME}/OR_inputs/place_wm/${PLATFORM}/${DESIGN_NICKNAME}" \
  make -C "${FLOW_HOME}" \
        DESIGN_CONFIG="${FLOW_HOME}/designs/${PLATFORM}/${DESIGN}/config.mk" \
        FLOW_VARIANT="${FLOW_VARIANT}" WORK_HOME="${EXPERIMENTS_HOME}" copy_inputs cts
fi

# 3) CTS embed on the just-produced 4_cts.odb (under the new FLOW_VARIANT dir).
log "C embed"
DESIGN="${DESIGN}" DESIGN_NICKNAME="${DESIGN_NICKNAME}" PLATFORM="${PLATFORM}" \
WM_FLOW_VARIANT="${FLOW_VARIANT}" \
FLOW_VARIANT="${FLOW_VARIANT}" \
WM_RESULTS="${WM_RESULTS}" \
WM_CTS_INPUT="${WM_RESULTS}/4_cts.odb" \
WM_CTS_OUTPUT_ODB="${WM_RESULTS}/4_cts_wm.odb" \
WM_CTS_OUTPUT_CSV="${WM_RESULTS}/wm_cts_pairs_embed_all_stage.csv" \
WM_SDC="${WM_RESULTS}/4_cts.sdc" \
  "${CTS_DIR}/run_cts_wm.sh"

# 4) Route + finish with R-WM hook.
log "R embed (route with wrong-way bias)"
DESIGN="${DESIGN}" DESIGN_NICKNAME="${DESIGN_NICKNAME}" PLATFORM="${PLATFORM}" \
WM_FLOW_VARIANT="${FLOW_VARIANT}" \
FLOW_VARIANT="${FLOW_VARIANT}" \
WM_RESULTS="${WM_RESULTS}" \
CTS_ODB="${WM_RESULTS}/4_cts_wm.odb" \
  "${ROUTE_DIR}/run.sh"

log "all-stage done; results under ${WM_RESULTS}"
