#!/bin/bash
export OPENROAD_EXE="/home/fetzfs_projects/MLBuf/flows/OR_branch_integration/OpenROAD/build_os8/src/openroad"
export FLOW_HOME="/home/fetzfs_projects/MLBuf/flows/OpenROAD-flow-scripts/flow"

export DESIGN="ibex"
export DESIGN_FULL_NAME="ibex"
export PLATFORM="nangate45"
export FLOW_VARIANT="rsz_default"

export PROJ_DIR=`pwd | grep -o "/\S*/MLBuf"`
export INPUTS_DIR="${PROJ_DIR}/flows/OR_inputs/${DESIGN_FULL_NAME}"
export GP_ODB="${PROJ_DIR}/flows/OR_baselines/${DESIGN_FULL_NAME}/${DESIGN_FULL_NAME}_${FLOW_VARIANT}.odb"
export SKIP_GP_MLBUF="1"
DESIGN_CONFIG="${FLOW_HOME}/designs/nangate45/${DESIGN}/config.mk" make mlbuf_place_and_route