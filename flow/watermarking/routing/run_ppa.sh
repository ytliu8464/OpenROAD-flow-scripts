#!/bin/bash
export PROJ_DIR="/home/fetzfs_projects/MISC-ytliu/watermarking"
export OPENROAD_EXE="${PROJ_DIR}/OR0415/OpenROAD/build/bin/openroad"
export KEPLER_FORMAL_EXE="${PROJ_DIR}/OR0415/kepler-formal/build/src/bin/kepler-formal"
export FLOW_HOME="${PROJ_DIR}/OR0415/OpenROAD-flow-scripts/flow"

export DESIGN="aes"
export PLATFORM="nangate45"
export WM_FLOW_VARIANT="watermarking-test1"
export FLOW_VARIANT="wm-test1-route-ppa"

export INPUTS_DIR="${FLOW_HOME}/OR_inputs/route_wm/${PLATFORM}/${DESIGN}"
export CTS_ODB="${FLOW_HOME}/results/${PLATFORM}/${DESIGN}/${WM_FLOW_VARIANT}/4_cts_rt_wm.odb"
export SKIP_RT_WM="1"

make -f ${FLOW_HOME}/Makefile \
     DESIGN_CONFIG="${FLOW_HOME}/designs/${PLATFORM}/${DESIGN}/config.mk" \
     wm_route
