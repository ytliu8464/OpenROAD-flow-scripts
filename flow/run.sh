#!/bin/bash
export PROJ_DIR="/home/fetzfs_projects/MISC-ytliu/watermarking"
export OPENROAD_EXE="${PROJ_DIR}/OR0415/OpenROAD/build/bin/openroad"
export KEPLER_FORMAL_EXE="${PROJ_DIR}/OR0415/kepler-formal/build/src/bin/kepler-formal"
export FLOW_HOME="${PROJ_DIR}/OR0415/OpenROAD-flow-scripts/flow"

export DESIGN="jpeg"
# export DESIGN_FULL_NAME="aes_cipher_top"
export FLOW_VARIANT="watermarking-test1"

export PLATFORM="nangate45"
# export INPUTS_DIR="${PROJ_DIR}/OR_inputs/${DESIGN_FULL_NAME}"
# export GP_ODB="${PROJ_DIR}/scripts/OR_scripts/${DESIGN_FULL_NAME}/${DESIGN_FULL_NAME}_${FLOW_VARIANT}.odb"
# export SKIP_GP_MLBUF="1"

make -f ${FLOW_HOME}/Makefile \
     DESIGN_CONFIG="${FLOW_HOME}/designs/${PLATFORM}/${DESIGN}/config.mk" \
    #  mlbuf_place_and_route
