#!/bin/bash
export OPENROAD_EXE="/home/fetzfs_projects/MLBuf/flows/OR_branch_integration/OpenROAD_old/build_os8/src/openroad"
#export OPENROAD_EXE="/home/fetzfs_projects/MLBuf/MLBuf_eval/OR_integration/OpenROAD/build/src/openroad"
#export OPENROAD_EXE="/home/fetzfs_projects/MLBuf/flows/OR_branch_integration/OpenROAD/build/src/openroad"
export FLOW_HOME="/home/fetzfs_projects/MLBuf/flows/OpenROAD-flow-scripts/flow"

export DESIGN="ibex"
export DESIGN_FULL_NAME="ibex"
export FLOW_VARIANT="v4__cn20_diffArea001_cmax20_3"

export PLATFORM="nangate45"
export PROJ_DIR=`pwd | grep -o "/\S*/MLBuf"`
export INPUTS_DIR="${PROJ_DIR}/flows/OR_inputs/${DESIGN_FULL_NAME}"
export GP_ODB="${PROJ_DIR}/flows/OR_baselines/${DESIGN_FULL_NAME}/mlbuf_${FLOW_VARIANT}/${DESIGN_FULL_NAME}_mlbuf.odb"
export SKIP_GP_MLBUF="1"

make -f ${FLOW_HOME}/Makefile \
     DESIGN_CONFIG="${FLOW_HOME}/designs/${PLATFORM}/${DESIGN}/config.mk" \
     mlbuf_place_and_route
