#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Reference ORFS run for a (DESIGN, PLATFORM, WM_FLOW_VARIANT) triple.
#
# Only needed when *adding* a new design to the bench matrix; for the current
# 7 testcases the reference results already exist on disk and are read by
# experiments/aggregate.py.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

log "starting reference flow for ${PLATFORM}/${DESIGN}/${WM_FLOW_VARIANT}"
SIF="${SINGULARITY_SIF:-/home/tool/singularity/images/ispd26.sif}"
if [[ -z "${SINGULARITY_NAME:-}" ]]; then
  exec singularity exec -B /home -B /tmp -e "${SIF}" \
    env PROJ_DIR="${PROJ_DIR}" OPENROAD_EXE="${OPENROAD_EXE}" \
        FLOW_HOME="${FLOW_HOME}" DESIGN="${DESIGN}" PLATFORM="${PLATFORM}" \
        WM_FLOW_VARIANT="${WM_FLOW_VARIANT}" FLOW_VARIANT="${WM_FLOW_VARIANT}" \
        bash -lc "bash \"${BASH_SOURCE[0]}\""
fi

make -C "${FLOW_HOME}" \
     DESIGN_CONFIG="${FLOW_HOME}/designs/${PLATFORM}/${DESIGN}/config.mk" \
     FLOW_VARIANT="${WM_FLOW_VARIANT}"

log "reference flow done"

