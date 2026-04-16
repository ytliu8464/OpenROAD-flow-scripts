#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Verify watermark cells across post-CTS / post-GRT / post-DRT ODBs (see README.md).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENROAD_EXE="${OPENROAD_EXE:-/home/fetzfs_projects/MISC-ytliu/watermarking/OR0415/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad}"
SIF="${SINGULARITY_SIF:-/home/tool/singularity/images/ispd26.sif}"

# Default: AES watermarking-test1 + PPA results from this repo layout
export AES_RES="${AES_RES:-../../results/nangate45/aes/watermarking-test1}"
PPA_RES="${PPA_RES:-${SCRIPT_DIR}/results/nangate45/aes/watermarking-test1-ppa}"

export WM_CELL_LIST="${WM_CELL_LIST:-${AES_RES}/wm_cells_embed.csv}"
export WM_VERIFY_STAGES="${WM_VERIFY_STAGES:-post_cts:${PPA_RES}/4_cts.odb,post_grt:${PPA_RES}/5_1_grt.odb,post_drt:${PPA_RES}/5_route.odb,post_fill:${PPA_RES}/6_final.odb}"
export WM_STAGE_REPORT="${WM_STAGE_REPORT:-${PPA_RES}/wm_stage_report.csv}"
export WM_DBU_PER_MICRON="${WM_DBU_PER_MICRON:-2000}"

singularity exec -B /home -B /tmp --bind /tmp/.X11-unix -e "$SIF" \
  env \
  OPENROAD_EXE="${OPENROAD_EXE}" \
  WM_CELL_LIST="${WM_CELL_LIST}" \
  WM_VERIFY_STAGES="${WM_VERIFY_STAGES}" \
  WM_STAGE_REPORT="${WM_STAGE_REPORT}" \
  WM_DBU_PER_MICRON="${WM_DBU_PER_MICRON}" \
  bash -lc "export PYTHONPATH=\"${SCRIPT_DIR}:\${PYTHONPATH:-}\" ; cd \"${SCRIPT_DIR}\" ; \"${OPENROAD_EXE}\" -python -exit \"${SCRIPT_DIR}/watermark_verify_stages.py\""
