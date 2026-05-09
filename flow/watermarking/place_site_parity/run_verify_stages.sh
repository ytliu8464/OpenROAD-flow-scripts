#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Verify site-parity watermark across post-CTS / post-GRT / post-DRT ODBs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

LOG_DIR="${SCRIPT_DIR}/wm_log"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/run_verify_stages_k5_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "${LOG_FILE}") 2>&1
echo "[run_verify_stages] logging to ${LOG_FILE}"

DESIGN="${DESIGN:-swerv_wrapper}"
PLATFORM="${PLATFORM:-asap7}"
WM_FLOW_VARIANT="${WM_FLOW_VARIANT:-base_tcp1455}"

# --------------------- customized flow/file variant ---------------------
PPA_FLOW_VARIANT="${PPA_FLOW_VARIANT:-base-tcp1455-ppa}"
# ---------------------  ---------------------

EMBED_RES="${SCRIPT_DIR}/../../results/${PLATFORM}/${DESIGN}/${WM_FLOW_VARIANT}"
PPA_RES="${SCRIPT_DIR}/results/${PLATFORM}/${DESIGN}/${PPA_FLOW_VARIANT}"
PPA_REPORTS="${SCRIPT_DIR}/reports/${PLATFORM}/${DESIGN}/${PPA_FLOW_VARIANT}"
PPA_LOGS="${SCRIPT_DIR}/logs/${PLATFORM}/${DESIGN}/${PPA_FLOW_VARIANT}"

export WM_CELL_LIST="${WM_CELL_LIST:-${EMBED_RES}/wm_cells_embed.csv}"
export WM_VERIFY_STAGES="${WM_VERIFY_STAGES:-post_cts:${PPA_RES}/4_cts.odb,post_grt:${PPA_RES}/5_1_grt.odb,post_drt:${PPA_RES}/5_route.odb,post_final:${PPA_RES}/6_final.odb}"

# --------------------- customized output name ---------------------
export WM_STAGE_REPORT="${WM_STAGE_REPORT:-${EMBED_RES}/wm_stage_report.csv}"
export WM_STAGE_PPA_REPORT="${WM_STAGE_PPA_REPORT:-${EMBED_RES}/wm_stage_ppa.csv}"
# ---------------------------------------------------------------

export WM_REPORTS_DIR="${WM_REPORTS_DIR:-${PPA_REPORTS}}"
export WM_LOGS_DIR="${WM_LOGS_DIR:-${PPA_LOGS}}"
export WM_DBU_PER_MICRON="${WM_DBU_PER_MICRON:-1000}"

echo "[run_verify_stages] cell list : ${WM_CELL_LIST}"
echo "[run_verify_stages] stages    : ${WM_VERIFY_STAGES}"
echo "[run_verify_stages] report    : ${WM_STAGE_REPORT}"
echo "[run_verify_stages] PPA csv   : ${WM_STAGE_PPA_REPORT}"
echo "[run_verify_stages] reports   : ${WM_REPORTS_DIR}"
echo "[run_verify_stages] logs      : ${WM_LOGS_DIR}"

"${SCRIPT_DIR}/place_wm.sh" verify_stages
