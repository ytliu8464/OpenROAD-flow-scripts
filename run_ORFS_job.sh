#!/bin/bash
export OPENROAD_EXE="/home/fetzfs_projects/MLBuf/flows/OR_branch_integration/OpenROAD/build_os8/src/openroad"
export FLOW_HOME="/home/fetzfs_projects/MLBuf/flows/OpenROAD-flow-scripts/flow"

export DESIGN="ibex"
export WORK_DIR="$(pwd)/no_timing"
mkdir -p ${WORK_DIR}

export PROJ_DIR=`pwd | grep -o "/\S*/MLBuf"`
export INPUTS_DIR="${PROJ_DIR}/flows/OR_inputs/${DESIGN}"
export WORK_HOME="${WORK_DIR}"
DESIGN_CONFIG="${FLOW_HOME}/designs/nangate45/${DESIGN}/config.mk" make