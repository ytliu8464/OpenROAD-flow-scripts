#!/bin/bash
# SPDX-License-Identifier: BSD-3-Clause
# CTS + GRT + DRT starting from placement-order watermarked ODB.

set -euo pipefail

export PROJ_DIR="${PROJ_DIR:-/home/fetzfs_projects/MISC-ytliu/watermarking}"
export OPENROAD_EXE="${OPENROAD_EXE:-${PROJ_DIR}/OR0415/OpenROAD/build/bin/openroad}"
export KEPLER_FORMAL_EXE="${KEPLER_FORMAL_EXE:-${PROJ_DIR}/OR0415/kepler-formal/build/src/bin/kepler-formal}"
export FLOW_HOME="${FLOW_HOME:-${PROJ_DIR}/OR0415/OpenROAD-flow-scripts/flow}"

export DESIGN="${DESIGN:-jpeg}"
export PLATFORM="${PLATFORM:-asap7}"
export WM_FLOW_VARIANT="${WM_FLOW_VARIANT:-base_tcp540}"

# --------------------- flow variant ---------------------
export FLOW_VARIANT="${FLOW_VARIANT:-base-tcp540-ppa-1um}"
export DP_ODB="${FLOW_HOME}/results/${PLATFORM}/${DESIGN}/${WM_FLOW_VARIANT}/3_place_order_wm_1um.odb"
# ------------------------------------------------------------

SIF="${SINGULARITY_SIF:-/home/tool/singularity/images/ispd26.sif}"
if [[ -z "${SINGULARITY_NAME:-}" ]]; then
  exec singularity exec -B /home -B /tmp --bind /tmp/.X11-unix -e "${SIF}" \
    env \
    PROJ_DIR="${PROJ_DIR}" \
    OPENROAD_EXE="${OPENROAD_EXE}" \
    KEPLER_FORMAL_EXE="${KEPLER_FORMAL_EXE}" \
    FLOW_HOME="${FLOW_HOME}" \
    DESIGN="${DESIGN}" \
    PLATFORM="${PLATFORM}" \
    WM_FLOW_VARIANT="${WM_FLOW_VARIANT}" \
    FLOW_VARIANT="${FLOW_VARIANT}" \
    bash -lc "bash \"${BASH_SOURCE[0]}\""
fi

export INPUTS_DIR="${FLOW_HOME}/OR_inputs/place_wm/${PLATFORM}/${DESIGN}"
export SKIP_DP_WM="1"

make -f "${FLOW_HOME}/Makefile" \
     DESIGN_CONFIG="${FLOW_HOME}/designs/${PLATFORM}/${DESIGN}/config.mk" \
     wm_cts_and_route
