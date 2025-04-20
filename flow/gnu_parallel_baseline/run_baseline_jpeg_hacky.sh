#!/bin/bash
export OPENROAD_EXE="/home/fetzfs_projects/MLBuf/flows/OR_branch_integration/OpenROAD/build_os8/src/openroad"
export FLOW_HOME="/home/fetzfs_projects/MLBuf/flows/OpenROAD-flow-scripts/flow"

export DESIGN="jpeg"
export DESIGN_FULL_NAME="jpeg_encoder"
export FLOW_VARIANT="hacky"

export PLATFORM="nangate45"
export PROJ_DIR=`pwd | grep -o "/\S*/MLBuf"`
export INPUTS_DIR="${PROJ_DIR}/flows/OR_inputs/${DESIGN_FULL_NAME}"
export GP_ODB="${PROJ_DIR}/flows/OR_baselines/${DESIGN_FULL_NAME}/${DESIGN_FULL_NAME}_${FLOW_VARIANT}.odb"
export SKIP_GP_MLBUF="1"

make -f ${FLOW_HOME}/Makefile \
     DESIGN_CONFIG="${FLOW_HOME}/designs/${PLATFORM}/${DESIGN}/config.mk" \
     mlbuf_place_and_route
