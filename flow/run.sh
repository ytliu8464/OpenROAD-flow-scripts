#!/bin/bash
export PROJ_DIR="/home/fetzfs_projects/MISC-ytliu/watermarking"
export OPENROAD_EXE="${PROJ_DIR}/OR0415/OpenROAD/build/bin/openroad"
export KEPLER_FORMAL_EXE="${PROJ_DIR}/OR0415/kepler-formal/build/src/bin/kepler-formal"
export FLOW_HOME="${PROJ_DIR}/OR0415/OpenROAD-flow-scripts/flow"

export DESIGN="ariane136"
export FLOW_VARIANT="ariane136_tcp1200"

export PLATFORM="asap7"

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
    FLOW_VARIANT="${FLOW_VARIANT}" \
    bash -lc "bash \"${BASH_SOURCE[0]}\""
fi

make -f ${FLOW_HOME}/Makefile \
     DESIGN_CONFIG="${FLOW_HOME}/designs/${PLATFORM}/${DESIGN}/config.mk" \
     
