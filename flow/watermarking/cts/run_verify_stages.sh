#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Verify CTS fanout-parity watermark across post-CTS / post-GRT / post-DRT / post-final ODBs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DESIGN="${DESIGN:-jpeg}"
PLATFORM="${PLATFORM:-asap7}"
WM_FLOW_VARIANT="${WM_FLOW_VARIANT:-base}"
PPA_FLOW_VARIANT="${PPA_FLOW_VARIANT:-wm-cts-ppa0424}"

EMBED_RES="${SCRIPT_DIR}/../../results/${PLATFORM}/${DESIGN}/${WM_FLOW_VARIANT}"
PPA_RES="${SCRIPT_DIR}/results/${PLATFORM}/${DESIGN}/${PPA_FLOW_VARIANT}"

export WM_CELL_LIST="${WM_CELL_LIST:-${EMBED_RES}/wm_cts_pairs_embed.csv}"
export WM_VERIFY_STAGES="${WM_VERIFY_STAGES:-post_cts:${EMBED_RES}/4_cts_wm.odb,post_grt:${PPA_RES}/5_1_grt.odb,post_drt:${PPA_RES}/5_route.odb,post_final:${PPA_RES}/6_final.odb}"
export WM_STAGE_REPORT="${WM_STAGE_REPORT:-${EMBED_RES}/wm_cts_stage_report.csv}"

echo "[run_verify_stages] cell list : ${WM_CELL_LIST}"
echo "[run_verify_stages] stages    : ${WM_VERIFY_STAGES}"
echo "[run_verify_stages] report    : ${WM_STAGE_REPORT}"

"${SCRIPT_DIR}/cts_wm.sh" verify_stages
