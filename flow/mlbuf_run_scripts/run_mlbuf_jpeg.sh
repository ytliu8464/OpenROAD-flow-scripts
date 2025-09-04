#!/bin/bash
export PROJ_DIR="/home/MLBuf_MLCAD"
export OPENROAD_EXE="${PROJ_DIR}/OR_branch_integration/OpenROAD/build/src/openroad"
export FLOW_HOME="/home/OpenROAD-flow-scripts/flow"

export DESIGN="jpeg"
export DESIGN_FULL_NAME="jpeg_encoder"
export FLOW_VARIANT="mlbuf-pretrained"

export PLATFORM="nangate45"
export INPUTS_DIR="${PROJ_DIR}/OR_inputs/${DESIGN_FULL_NAME}"
export GP_ODB="${PROJ_DIR}/scripts/OR_scripts/${DESIGN_FULL_NAME}/${FLOW_VARIANT}/${DESIGN_FULL_NAME}_mlbuf.odb"
export SKIP_GP_MLBUF="1"

make -f ${FLOW_HOME}/Makefile \
     DESIGN_CONFIG="${FLOW_HOME}/designs/${PLATFORM}/${DESIGN}/config.mk" \
     mlbuf_place_and_route
