#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# ICMarks baseline (ZhangRPK25) -- chained driver.
#
# Required env:
#   DESIGN          e.g. aes
#   PLATFORM        e.g. nangate45
#   WM_FLOW_VARIANT e.g. watermarking-test1   (source reference flow variant)
#
# Optional env:
#   FLOW_VARIANT    output variant (default: baseline-icmarks)
#   BASELINE_K      override K (integer)
#   PROJ_DIR        default /home/fetzfs_projects/MISC-ytliu/watermarking
#   IC_ALPHA / IC_BETA / IC_GAMMA  GW score weights
#   IC_WIN_W / IC_WIN_H / IC_STRIDE_X / IC_STRIDE_Y  GW window/stride

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SIF="${SINGULARITY_SIF:-/home/tool/singularity/images/ispd26.sif}"
if [[ -z "${SINGULARITY_NAME:-}" ]]; then
  exec singularity exec -B /home -B /tmp -e "${SIF}" \
    env \
    DESIGN="${DESIGN}" \
    PLATFORM="${PLATFORM}" \
    WM_FLOW_VARIANT="${WM_FLOW_VARIANT}" \
    FLOW_VARIANT="${FLOW_VARIANT:-baseline-icmarks}" \
    BASELINE_K="${BASELINE_K:-}" \
    PROJ_DIR="${PROJ_DIR:-/home/fetzfs_projects/MISC-ytliu/watermarking}" \
    EXPERIMENTS_HOME="${EXPERIMENTS_HOME:-}" \
    WM_RESULTS_HOME="${WM_RESULTS_HOME:-}" \
    IC_ALPHA="${IC_ALPHA:-}" \
    IC_BETA="${IC_BETA:-}" \
    IC_GAMMA="${IC_GAMMA:-}" \
    IC_WIN_W="${IC_WIN_W:-}" \
    IC_WIN_H="${IC_WIN_H:-}" \
    IC_STRIDE_X="${IC_STRIDE_X:-}" \
    IC_STRIDE_Y="${IC_STRIDE_Y:-}" \
    SINGULARITY_NAME="ispd26" \
    bash -lc "bash \"${BASH_SOURCE[0]}\""
fi

export PROJ_DIR="${PROJ_DIR:-/home/fetzfs_projects/MISC-ytliu/watermarking}"
export OPENROAD_EXE="${OPENROAD_EXE:-${PROJ_DIR}/OR0415/OpenROAD/build/bin/openroad}"
export FLOW_HOME="${FLOW_HOME:-${PROJ_DIR}/OR0415/OpenROAD-flow-scripts/flow}"
export EXPERIMENTS_HOME="${EXPERIMENTS_HOME:-${FLOW_HOME}/watermarking/experiments}"
export WM_RESULTS_HOME="${WM_RESULTS_HOME:-${EXPERIMENTS_HOME}/results}"
export FLOW_VARIANT="${FLOW_VARIANT:-baseline-icmarks}"

WM_DIR="${FLOW_HOME}/watermarking"
GEN_KEY_DIR="${WM_DIR}/gen_key"

ts()  { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] [icmarks/run.sh] $*"; }

if [[ -z "${DESIGN:-}" || -z "${PLATFORM:-}" || -z "${WM_FLOW_VARIANT:-}" ]]; then
  echo "ERROR: DESIGN, PLATFORM, WM_FLOW_VARIANT must be set" >&2; exit 2
fi

REF_RES="${FLOW_HOME}/results/${PLATFORM}/${DESIGN}/${WM_FLOW_VARIANT}"
OUT_RES="${WM_RESULTS_HOME}/${PLATFORM}/${DESIGN}/${FLOW_VARIANT}"
mkdir -p "${OUT_RES}"
SEED_HEX="${GEN_KEY_DIR}/out/${DESIGN}/seed_placement.hex"

if [[ ! -f "${SEED_HEX}" ]]; then
  log "deriving seed for ${DESIGN} ..."
  ( cd "${GEN_KEY_DIR}" && ./gen_key.sh sign \
      --sk keys/sk.pem --pk keys/pk.pem \
      --owner-id "${OWNER_ID:-yiting}" \
      --design-id "${DESIGN}" \
      --out-dir "out/${DESIGN}" --force )
fi

log "embedding ICMarks watermark on ${PLATFORM}/${DESIGN}/${WM_FLOW_VARIANT} -> ${FLOW_VARIANT}"

INPUT_ODB="${REF_RES}/3_place.odb"
if [[ ! -f "${INPUT_ODB}" ]]; then
  echo "ERROR: source ODB not found: ${INPUT_ODB}" >&2; exit 1
fi

EMBED_SCRIPT="${SCRIPT_DIR}/embed.py"
OUT_ODB="${OUT_RES}/3_place_icmarks.odb"
EMBED_CSV="${OUT_RES}/icmarks_embed.csv"

${OPENROAD_EXE} -python -exit "${EMBED_SCRIPT}" \
  --odb "${INPUT_ODB}" \
  --out-odb "${OUT_ODB}" \
  --seed-hex "${SEED_HEX}" \
  --platform "${PLATFORM}" \
  --design "${DESIGN}" \
  --variant "${WM_FLOW_VARIANT}" \
  ${BASELINE_K:+--k "${BASELINE_K}"} \
  ${IC_ALPHA:+--alpha "${IC_ALPHA}"} \
  ${IC_BETA:+--beta "${IC_BETA}"} \
  ${IC_GAMMA:+--gamma "${IC_GAMMA}"} \
  ${IC_WIN_W:+--window-w-sites "${IC_WIN_W}"} \
  ${IC_WIN_H:+--window-h-rows "${IC_WIN_H}"} \
  ${IC_STRIDE_X:+--stride-x-sites "${IC_STRIDE_X}"} \
  ${IC_STRIDE_Y:+--stride-y-rows "${IC_STRIDE_Y}"} \
  --out-csv "${EMBED_CSV}"

log "embed complete; watermarked ODB at ${OUT_ODB}"

log "running CTS + route + finish via make wm_cts_and_route ..."
INPUTS_DIR="${FLOW_HOME}/OR_inputs/place_wm/${PLATFORM}/${DESIGN}"

make -C "${FLOW_HOME}" \
  DESIGN_CONFIG="${FLOW_HOME}/designs/${PLATFORM}/${DESIGN}/config.mk" \
  WORK_HOME="${EXPERIMENTS_HOME}" \
  FLOW_VARIANT="${FLOW_VARIANT}" \
  SKIP_DP_WM=1 \
  INPUTS_DIR="${INPUTS_DIR}" \
  DP_ODB="${OUT_ODB}" \
  wm_cts_and_route

log "verifying watermark at DRT stage ..."
DRT_ODB="${OUT_RES}/6_final.odb"
if [[ -f "${DRT_ODB}" ]]; then
  ${OPENROAD_EXE} -python -exit "${SCRIPT_DIR}/verify.py" \
    --odb "${DRT_ODB}" \
    --embed-csv "${EMBED_CSV}" \
    --stage DRT \
    --out-csv "${OUT_RES}/icmarks_verify_DRT.csv"
else
  log "WARNING: 6_final.odb not found; skipping DRT verify"
fi

log "done: results under ${OUT_RES}"
