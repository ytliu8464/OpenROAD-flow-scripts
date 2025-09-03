#!/bin/bash
export PROJ_DIR="/home/fetzfs_projects/timer_calibration/MLBuf_reproduce/MLBuf_MLCAD"
#export OPENROAD_EXE="${PROJ_DIR}/OR_branch_integration/OpenROAD/build/src/openroad"
export OPENROAD_EXE="/home/fetzfs_projects/MLBuf/flows/OR_branch_integration/OpenROAD/build_os8/src/openroad"
export FLOW_HOME="/home/fetzfs_projects/timer_calibration/ORFS_mlbuf_erc/OpenROAD-flow-scripts/flow"

export DESIGN="jpeg"
export DESIGN_FULL_NAME="jpeg_encoder"
export FLOW_VARIANT="no_timing"

export PLATFORM="nangate45"
export INPUTS_DIR="${PROJ_DIR}/OR_inputs/${DESIGN_FULL_NAME}"
export GP_ODB="${PROJ_DIR}/scripts/OR_scripts/${DESIGN_FULL_NAME}/${DESIGN_FULL_NAME}_${FLOW_VARIANT}.odb"
export SKIP_GP_MLBUF="1"

make -f ${FLOW_HOME}/Makefile \
     DESIGN_CONFIG="${FLOW_HOME}/designs/${PLATFORM}/${DESIGN}/config.mk" \
     mlbuf_place_and_route