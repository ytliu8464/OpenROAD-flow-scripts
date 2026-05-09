#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Verify ordering watermark across post-CTS / GRT / DRT ODBs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DESIGN="${DESIGN:-jpeg}"
PLATFORM="${PLATFORM:-asap7}"
WM_FLOW_VARIANT="${WM_FLOW_VARIANT:-base_tcp540}"

LOG_DIR="${SCRIPT_DIR}/wm_log"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/${DESIGN}_verify_stages_order_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "${LOG_FILE}") 2>&1
echo "[run_verify_stages] logging to ${LOG_FILE}"
EMBED_RES="${SCRIPT_DIR}/../../results/${PLATFORM}/${DESIGN}/${WM_FLOW_VARIANT}"

# ------ customized flow/file variant (should match run_ppa.sh's FLOW_VARIANT) -------------
PPA_FLOW_VARIANT="${PPA_FLOW_VARIANT:-base-tcp540-ppa-v2}"
export WM_CELL_LIST="${WM_CELL_LIST:-${EMBED_RES}/wm_place_order_embed_v2.csv}"
# --------------------------------------------------

PPA_RES="${SCRIPT_DIR}/results/${PLATFORM}/${DESIGN}/${PPA_FLOW_VARIANT}"
PPA_REPORTS="${SCRIPT_DIR}/reports/${PLATFORM}/${DESIGN}/${PPA_FLOW_VARIANT}"
PPA_LOGS="${SCRIPT_DIR}/logs/${PLATFORM}/${DESIGN}/${PPA_FLOW_VARIANT}"

export WM_VERIFY_STAGES="${WM_VERIFY_STAGES:-post_cts:${PPA_RES}/4_cts.odb,post_grt:${PPA_RES}/5_1_grt.odb,post_drt:${PPA_RES}/5_route.odb,post_final:${PPA_RES}/6_final.odb}"

export WM_STAGE_REPORT="${WM_STAGE_REPORT:-${EMBED_RES}/wm_place_order_stage_report.csv}"
export WM_STAGE_PPA_REPORT="${WM_STAGE_PPA_REPORT:-${EMBED_RES}/wm_place_order_stage_ppa.csv}"

export WM_REPORTS_DIR="${WM_REPORTS_DIR:-${PPA_REPORTS}}"
export WM_LOGS_DIR="${WM_LOGS_DIR:-${PPA_LOGS}}"

echo "[run_verify_stages] cell list : ${WM_CELL_LIST}"
echo "[run_verify_stages] stages    : ${WM_VERIFY_STAGES}"
echo "[run_verify_stages] report    : ${WM_STAGE_REPORT}"

"${SCRIPT_DIR}/place_wm.sh" verify_stages
