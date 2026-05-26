#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Buffer-insertion baseline (SunGX06) -- chained driver.
#
# Required env:
#   DESIGN          e.g. aes
#   PLATFORM        e.g. nangate45
#   WM_FLOW_VARIANT e.g. watermarking-test1   (source reference flow variant)
#
# Optional env:
#   FLOW_VARIANT    output variant (default: baseline-bufins)
#   BASELINE_K      override K (integer)
#   PROJ_DIR        default /home/fetzfs_projects/MISC-ytliu/watermarking

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Auto-re-exec inside Singularity when run from the host.
SIF="${SINGULARITY_SIF:-/home/tool/singularity/images/ispd26.sif}"
if [[ -z "${SINGULARITY_NAME:-}" ]]; then
  exec singularity exec -B /home -B /tmp -e "${SIF}" \
    env \
    DESIGN="${DESIGN}" \
    PLATFORM="${PLATFORM}" \
    WM_FLOW_VARIANT="${WM_FLOW_VARIANT}" \
    FLOW_VARIANT="${FLOW_VARIANT:-baseline-bufins}" \
    BASELINE_K="${BASELINE_K:-}" \
    PROJ_DIR="${PROJ_DIR:-/home/fetzfs_projects/MISC-ytliu/watermarking}" \
    EXPERIMENTS_HOME="${EXPERIMENTS_HOME:-}" \
    WM_RESULTS_HOME="${WM_RESULTS_HOME:-}" \
    SINGULARITY_NAME="ispd26" \
    bash -lc "bash \"${BASH_SOURCE[0]}\""
fi

export PROJ_DIR="${PROJ_DIR:-/home/fetzfs_projects/MISC-ytliu/watermarking}"
export OPENROAD_EXE="${OPENROAD_EXE:-${PROJ_DIR}/OR0415/OpenROAD/build/bin/openroad}"
export FLOW_HOME="${FLOW_HOME:-${PROJ_DIR}/OR0415/OpenROAD-flow-scripts/flow}"
export EXPERIMENTS_HOME="${EXPERIMENTS_HOME:-${FLOW_HOME}/watermarking/experiments}"
export WM_RESULTS_HOME="${WM_RESULTS_HOME:-${EXPERIMENTS_HOME}/results}"
export FLOW_VARIANT="${FLOW_VARIANT:-baseline-bufins}"

WM_DIR="${FLOW_HOME}/watermarking"
GEN_KEY_DIR="${WM_DIR}/gen_key"

ts()  { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] [bufins/run.sh] $*"; }

if [[ -z "${DESIGN:-}" || -z "${PLATFORM:-}" || -z "${WM_FLOW_VARIANT:-}" ]]; then
  echo "ERROR: DESIGN, PLATFORM, WM_FLOW_VARIANT must be set" >&2; exit 2
fi

REF_RES="${FLOW_HOME}/results/${PLATFORM}/${DESIGN}/${WM_FLOW_VARIANT}"
OUT_RES="${WM_RESULTS_HOME}/${PLATFORM}/${DESIGN}/${FLOW_VARIANT}"
mkdir -p "${OUT_RES}"
SEED_HEX="${GEN_KEY_DIR}/out/${DESIGN}/seed_routing.hex"

# ---- ensure keys exist ----
if [[ ! -f "${SEED_HEX}" ]]; then
  log "deriving seed for ${DESIGN} ..."
  ( cd "${GEN_KEY_DIR}" && ./gen_key.sh sign \
      --sk keys/sk.pem --pk keys/pk.pem \
      --owner-id "${OWNER_ID:-yiting}" \
      --design-id "${DESIGN}" \
      --out-dir "out/${DESIGN}" --force )
fi

# ---- source ODB: 4_cts.odb from reference flow ----
log "embedding buffer-insertion watermark on ${PLATFORM}/${DESIGN}/${WM_FLOW_VARIANT} -> ${FLOW_VARIANT}"

INPUT_ODB="${REF_RES}/4_cts.odb"
if [[ ! -f "${INPUT_ODB}" ]]; then
  # Try 4_1_cts.odb as fallback
  INPUT_ODB="${REF_RES}/4_1_cts.odb"
fi
if [[ ! -f "${INPUT_ODB}" ]]; then
  echo "ERROR: source 4_cts.odb not found in ${REF_RES}" >&2; exit 1
fi

EMBED_SCRIPT="${SCRIPT_DIR}/embed.py"
OUT_ODB="${OUT_RES}/4_cts_bufins.odb"
EMBED_CSV="${OUT_RES}/buffer_insertion_embed.csv"

${OPENROAD_EXE} -python -exit "${EMBED_SCRIPT}" \
  --odb "${INPUT_ODB}" \
  --out-odb "${OUT_ODB}" \
  --seed-hex "${SEED_HEX}" \
  --platform "${PLATFORM}" \
  --design "${DESIGN}" \
  --variant "${WM_FLOW_VARIANT}" \
  ${BASELINE_K:+--k "${BASELINE_K}"} \
  --out-csv "${EMBED_CSV}"

log "embed complete; watermarked ODB at ${OUT_ODB}"

# ---- continue flow (route + finish) via wm_route_wrong_way ----
# We use copy_inputs_v2 which sets 4_cts.odb from CTS_ODB; then standard route+finish.
log "running route + finish via make wm_route_wrong_way ..."

# Use the CTS-stage inputs directory (contains netlist, libs, etc.)
INPUTS_DIR="${FLOW_HOME}/OR_inputs/cts_wm/${PLATFORM}/${DESIGN}"
# Fallback to place_wm inputs if cts_wm doesn't exist
if [[ ! -d "${INPUTS_DIR}" ]]; then
  INPUTS_DIR="${FLOW_HOME}/OR_inputs/place_wm/${PLATFORM}/${DESIGN}"
fi

make -C "${FLOW_HOME}" \
  DESIGN_CONFIG="${FLOW_HOME}/designs/${PLATFORM}/${DESIGN}/config.mk" \
  WORK_HOME="${EXPERIMENTS_HOME}" \
  FLOW_VARIANT="${FLOW_VARIANT}" \
  SKIP_RT_WM=1 \
  INPUTS_DIR="${INPUTS_DIR}" \
  CTS_ODB="${OUT_ODB}" \
  wm_route_wrong_way

# ---- post-flow verification ----
log "verifying watermark at DRT stage ..."
DRT_ODB="${OUT_RES}/6_final.odb"
if [[ -f "${DRT_ODB}" ]]; then
  ${OPENROAD_EXE} -python -exit "${SCRIPT_DIR}/verify.py" \
    --odb "${DRT_ODB}" \
    --embed-csv "${EMBED_CSV}" \
    --stage DRT \
    --out-csv "${OUT_RES}/buffer_insertion_verify_DRT.csv"
else
  log "WARNING: 6_final.odb not found; skipping DRT verify"
fi

log "done: results under ${OUT_RES}"
