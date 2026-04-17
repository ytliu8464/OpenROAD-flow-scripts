#!/bin/bash
export PROJ_DIR="/home/fetzfs_projects/MISC-ytliu/watermarking"
export OPENROAD_EXE="${PROJ_DIR}/OR0415/OpenROAD/build/bin/openroad"
export KEPLER_FORMAL_EXE="${PROJ_DIR}/OR0415/kepler-formal/build/src/bin/kepler-formal"
export FLOW_HOME="${PROJ_DIR}/OR0415/OpenROAD-flow-scripts/flow"

export DESIGN="aes"
export PLATFORM="nangate45"
export WM_FLOW_VARIANT="watermarking-test1"
export FLOW_VARIANT="route-wm-wrong-way"

export WATERMARK_MESSAGE="This-is-a-drt-wm-test"
export WATERMARK_FRACTION="0.05"
export WATERMARK_STRENGTH="100.0"
export WATERMARK_P="0.4"

WM_DIR="${FLOW_HOME}/watermarking/routing_wrong_way"
export PRE_GLOBAL_ROUTE_TCL="${WM_DIR}/pre_route_watermark.tcl"
export POST_DETAIL_ROUTE_TCL="${WM_DIR}/post_route_watermark.tcl"

export INPUTS_DIR="${FLOW_HOME}/OR_inputs/route_wm/${PLATFORM}/${DESIGN}"
export CTS_ODB="${FLOW_HOME}/results/${PLATFORM}/${DESIGN}/${WM_FLOW_VARIANT}/4_cts.odb"
export SKIP_RT_WM="1"
# export SKIP_RT_WM_WW="1"

make -f ${FLOW_HOME}/Makefile \
     DESIGN_CONFIG="${FLOW_HOME}/designs/${PLATFORM}/${DESIGN}/config.mk" \
     OPENROAD_EXE="${PROJ_DIR}/OR0415/OpenROAD/build/bin/openroad" \
     wm_route_wrong_way
