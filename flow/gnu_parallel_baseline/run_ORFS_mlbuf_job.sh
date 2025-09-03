#!/bin/bash
export OPENROAD_EXE="/home/fetzfs_projects/MLBuf/flows/OR_branch_integration/OpenROAD/build_os8/src/openroad"
export FLOW_HOME="/home/fetzfs_projects/MLBuf/flows/OpenROAD-flow-scripts/flow"

export DESIGN="xx"
export DESIGN_FULL_NAME="xx"
export FLOW_VARIANT="xx"

export PLATFORM="nangate45"
export PROJ_DIR=`pwd | grep -o "/\S*/MLBuf"`
export INPUTS_DIR="${PROJ_DIR}/flows/OR_inputs/${DESIGN_FULL_NAME}"
export GP_ODB="${PROJ_DIR}/flows/OR_baselines/${DESIGN_FULL_NAME}/mlbuf_${FLOW_VARIANT}/${DESIGN_FULL_NAME}_mlbuf.odb"
export SKIP_GP_MLBUF="1"

make -f ${FLOW_HOME}/Makefile \
     DESIGN_CONFIG="${FLOW_HOME}/designs/${PLATFORM}/${DESIGN}/config.mk" \
     mlbuf_place_and_route