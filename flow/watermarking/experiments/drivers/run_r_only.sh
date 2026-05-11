#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# R-only PDMarks: routing wrong-way watermark only.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
ensure_keys

ROUTE_DIR="${FLOW_HOME}/watermarking/routing_wrong_way"
export FLOW_VARIANT="${FLOW_VARIANT:-pdmarks-r-only}"

DESIGN="${DESIGN}" PLATFORM="${PLATFORM}" WM_FLOW_VARIANT="${WM_FLOW_VARIANT}" \
  FLOW_VARIANT="${FLOW_VARIANT}" "${ROUTE_DIR}/run.sh"
log "R-only done"
