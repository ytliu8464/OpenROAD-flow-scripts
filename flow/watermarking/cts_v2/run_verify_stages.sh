#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Verify CTS fanout-parity watermark across post-CTS / post-GRT / post-DRT / post-final ODBs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FLOW_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
EXPERIMENTS_HOME="${EXPERIMENTS_HOME:-${FLOW_DIR}/watermarking/experiments}"
WM_RESULTS_HOME="${WM_RESULTS_HOME:-${EXPERIMENTS_HOME}/results}"

DESIGN="${DESIGN:-swerv_wrapper}"
# DESIGN_NICKNAME = on-disk name ORFS uses (defaults to DESIGN).
DESIGN_NICKNAME="${DESIGN_NICKNAME:-${DESIGN}}"
PLATFORM="${PLATFORM:-asap7}"
WM_FLOW_VARIANT="${WM_FLOW_VARIANT:-base_tcp1455}"
FLOW_VARIANT="${FLOW_VARIANT:-${WM_FLOW_VARIANT}}"
PPA_FLOW_VARIANT="${PPA_FLOW_VARIANT:-base-tcp1455-ppa-run2}"

# Embed-result / PPA-result dirs use DESIGN_NICKNAME.
EMBED_RES="${WM_RESULTS:-${WM_RESULTS_HOME}/${PLATFORM}/${DESIGN_NICKNAME}/${FLOW_VARIANT}}"
PPA_RES="${SCRIPT_DIR}/results/${PLATFORM}/${DESIGN_NICKNAME}/${PPA_FLOW_VARIANT}"

export WM_CELL_LIST="${WM_CELL_LIST:-${EMBED_RES}/wm_cts_pairs_embed.csv}"
export WM_VERIFY_STAGES="${WM_VERIFY_STAGES:-post_cts:${EMBED_RES}/4_cts_wm.odb,post_grt:${PPA_RES}/5_1_grt.odb,post_drt:${PPA_RES}/5_route.odb,post_final:${PPA_RES}/6_final.odb}"
export WM_STAGE_REPORT="${WM_STAGE_REPORT:-${EMBED_RES}/wm_cts_stage_report.csv}"

echo "[run_verify_stages] cell list : ${WM_CELL_LIST}"
echo "[run_verify_stages] stages    : ${WM_VERIFY_STAGES}"
echo "[run_verify_stages] report    : ${WM_STAGE_REPORT}"

"${SCRIPT_DIR}/cts_wm.sh" verify_stages
