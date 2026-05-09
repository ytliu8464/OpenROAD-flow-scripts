#!/bin/bash
# SPDX-License-Identifier: BSD-3-Clause
# Post-CTS-watermark GRT+DRT PPA, starting from the watermarked 4_cts_wm.odb.

set -euo pipefail

export PROJ_DIR="/home/fetzfs_projects/MISC-ytliu/watermarking"
export OPENROAD_EXE="${PROJ_DIR}/OR0415/OpenROAD/build/bin/openroad"
export KEPLER_FORMAL_EXE="${PROJ_DIR}/OR0415/kepler-formal/build/src/bin/kepler-formal"
export FLOW_HOME="${PROJ_DIR}/OR0415/OpenROAD-flow-scripts/flow"

export DESIGN="${DESIGN:-swerv_wrapper}"
export PLATFORM="${PLATFORM:-asap7}"
export WM_FLOW_VARIANT="${WM_FLOW_VARIANT:-base_tcp1455}"

# --------------------- customized flow/file variant ---------------------
export FLOW_VARIANT="${FLOW_VARIANT:-base-tcp1455-ppa-run2}"
export CTS_ODB="${FLOW_HOME}/results/${PLATFORM}/${DESIGN}/${WM_FLOW_VARIANT}/4_cts_wm.odb"
# ---------------------  ---------------------


# Automatically re-exec inside Singularity when run from the host.
# SINGULARITY_NAME is set by the runtime whenever we are already inside a container.
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

export INPUTS_DIR="${FLOW_HOME}/OR_inputs/cts_wm/${PLATFORM}/${DESIGN}"
export SKIP_RT_WM="1"

make -f "${FLOW_HOME}/Makefile" \
     DESIGN_CONFIG="${FLOW_HOME}/designs/${PLATFORM}/${DESIGN}/config.mk" \
     wm_cts

