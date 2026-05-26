#!/bin/bash
# SPDX-License-Identifier: BSD-3-Clause
# CTS + GRT + DRT starting from placement-order watermarked ODB.

set -euo pipefail

export PROJ_DIR="${PROJ_DIR:-/home/fetzfs_projects/MISC-ytliu/watermarking}"
export OPENROAD_EXE="${OPENROAD_EXE:-${PROJ_DIR}/OR0415/OpenROAD/build/bin/openroad}"
export KEPLER_FORMAL_EXE="${KEPLER_FORMAL_EXE:-${PROJ_DIR}/OR0415/kepler-formal/build/src/bin/kepler-formal}"
export FLOW_HOME="${FLOW_HOME:-${PROJ_DIR}/OR0415/OpenROAD-flow-scripts/flow}"
export EXPERIMENTS_HOME="${EXPERIMENTS_HOME:-${FLOW_HOME}/watermarking/experiments}"
export WM_RESULTS_HOME="${WM_RESULTS_HOME:-${EXPERIMENTS_HOME}/results}"

export DESIGN="${DESIGN:-swerv_wrapper}"
# DESIGN_NICKNAME = on-disk name ORFS uses (defaults to DESIGN).
export DESIGN_NICKNAME="${DESIGN_NICKNAME:-${DESIGN}}"
export PLATFORM="${PLATFORM:-asap7}"
export WM_FLOW_VARIANT="${WM_FLOW_VARIANT:-base_tcp1455}"

# --------------------- flow variant ---------------------
# Output PPA logs/results live under flow_variant; defaults pair with
# run_place_wm.sh's wm_1um output suffix.  experiments/results/<plat>/<nickname>/
# mirrors the ORFS layout.
export FLOW_VARIANT="${FLOW_VARIANT:-base-tcp1455-ppa}"
export WM_RESULTS="${WM_RESULTS:-${WM_RESULTS_HOME}/${PLATFORM}/${DESIGN_NICKNAME}/${FLOW_VARIANT}}"
export DP_ODB="${DP_ODB:-${WM_RESULTS}/3_place_order_wm.odb}"
# ------------------------------------------------------------

SIF="${SINGULARITY_SIF:-/home/tool/singularity/images/ispd26.sif}"
if [[ -z "${SINGULARITY_NAME:-}" ]]; then
  exec singularity exec -B /home -B /tmp --bind /tmp/.X11-unix -e "${SIF}" \
    env \
    PROJ_DIR="${PROJ_DIR}" \
    OPENROAD_EXE="${OPENROAD_EXE}" \
    KEPLER_FORMAL_EXE="${KEPLER_FORMAL_EXE}" \
    FLOW_HOME="${FLOW_HOME}" \
    EXPERIMENTS_HOME="${EXPERIMENTS_HOME}" \
    WM_RESULTS_HOME="${WM_RESULTS_HOME}" \
    DESIGN="${DESIGN}" \
    DESIGN_NICKNAME="${DESIGN_NICKNAME}" \
    PLATFORM="${PLATFORM}" \
    WM_FLOW_VARIANT="${WM_FLOW_VARIANT}" \
    FLOW_VARIANT="${FLOW_VARIANT}" \
    WM_RESULTS="${WM_RESULTS}" \
    DP_ODB="${DP_ODB}" \
    bash -lc "bash \"${BASH_SOURCE[0]}\""
fi

# Pre-staged OR_inputs live under DESIGN_NICKNAME; DESIGN_CONFIG uses DESIGN_NAME.
export INPUTS_DIR="${FLOW_HOME}/OR_inputs/place_wm/${PLATFORM}/${DESIGN_NICKNAME}"
export SKIP_DP_WM="1"

make -f "${FLOW_HOME}/Makefile" \
     DESIGN_CONFIG="${FLOW_HOME}/designs/${PLATFORM}/${DESIGN}/config.mk" \
     WORK_HOME="${EXPERIMENTS_HOME}" \
     wm_cts_and_route
