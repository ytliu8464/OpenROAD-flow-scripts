#!/bin/bash
# Kerckhoffs-compliant routing watermark run.
#
# Reads K_R from gen_key/out/${DESIGN}/seed_routing.hex (32 bytes) and passes
# its hex serialization as -key_hex to set_routing_watermark via
# pre_route_watermark.tcl.  Falls back to the legacy WATERMARK_MESSAGE path
# only if the seed file is missing.

set -euo pipefail

export PROJ_DIR="${PROJ_DIR:-/home/fetzfs_projects/MISC-ytliu/watermarking}"
export OPENROAD_EXE="${OPENROAD_EXE:-${PROJ_DIR}/OR0415/OpenROAD/build/bin/openroad}"
export KEPLER_FORMAL_EXE="${KEPLER_FORMAL_EXE:-${PROJ_DIR}/OR0415/kepler-formal/build/src/bin/kepler-formal}"
export FLOW_HOME="${FLOW_HOME:-${PROJ_DIR}/OR0415/OpenROAD-flow-scripts/flow}"

export DESIGN="${DESIGN:-aes}"
export PLATFORM="${PLATFORM:-nangate45}"
export OWNER_ID="${OWNER_ID:-yiting}"
export WM_FLOW_VARIANT="${WM_FLOW_VARIANT:-watermarking-test1}"
export FLOW_VARIANT="${FLOW_VARIANT:-route-wm-wrong-way}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${SCRIPT_DIR}/wm_log"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/${DESIGN}_run_route_wm_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "${LOG_FILE}") 2>&1
echo "[run_route_wm] logging to ${LOG_FILE}"

# Watermark parameters (paper Eq. eq:routing_selection / eq:routing_pvalue).
export WATERMARK_FRACTION="${WATERMARK_FRACTION:-0.02}"     # f
export WATERMARK_STRENGTH="${WATERMARK_STRENGTH:-100.0}"    # lambda_wm
export WATERMARK_P="${WATERMARK_P:-0.4}"                    # report cutoff

# Automatically re-exec inside Singularity when run from the host.
# SINGULARITY_NAME is set by the runtime whenever we are already inside a container.
SIF="${SINGULARITY_SIF:-/home/tool/singularity/images/ispd26.sif}"
if [[ -z "${SINGULARITY_NAME:-}" ]]; then
  exec singularity exec -B /home -B /tmp --bind /tmp/.X11-unix -e "${SIF}" \
    env \
    PROJ_DIR="${PROJ_DIR}" \
    OPENROAD_EXE="${OPENROAD_EXE}" \
    KEPLER_FORMAL_EXE="${KEPLER_FORMAL_EXE}" \
    FLOW_HOME="${FLOW_HOME}" \
    DESIGN="${DESIGN}" \
    PLATFORM="${PLATFORM}" \
    OWNER_ID="${OWNER_ID}" \
    WM_FLOW_VARIANT="${WM_FLOW_VARIANT}" \
    FLOW_VARIANT="${FLOW_VARIANT}" \
    WATERMARK_FRACTION="${WATERMARK_FRACTION}" \
    WATERMARK_STRENGTH="${WATERMARK_STRENGTH}" \
    WATERMARK_P="${WATERMARK_P}" \
    CTS_ODB="${CTS_ODB:-}" \
    bash -lc "bash \"${BASH_SOURCE[0]}\""
fi

WM_DIR="${FLOW_HOME}/watermarking/routing_wrong_way"
GEN_KEY_DIR="${FLOW_HOME}/watermarking/gen_key"
BUNDLE_DIR="${GEN_KEY_DIR}/out/${DESIGN}"
SEED_ROUTING="${BUNDLE_DIR}/seed_routing.hex"

# Auto-derive the routing stage seed if it is missing.
KEYS_DIR="${GEN_KEY_DIR}/keys"
if [[ ! -f "${KEYS_DIR}/sk.pem" ]]; then
  echo "[run] generating owner keypair in ${KEYS_DIR}"
  ( cd "${GEN_KEY_DIR}" && ./gen_key.sh keygen --owner-id "${OWNER_ID}" \
        --out-dir keys )
fi
if [[ ! -f "${SEED_ROUTING}" ]]; then
  echo "[run] signing bundle for design=${DESIGN}"
  ( cd "${GEN_KEY_DIR}" && ./gen_key.sh sign \
        --sk keys/sk.pem --pk keys/pk.pem \
        --owner-id "${OWNER_ID}" --design-id "${DESIGN}" \
        --out-dir "out/${DESIGN}" --force )
fi
export WM_SEED_HEX="${SEED_ROUTING}"

export PRE_GLOBAL_ROUTE_TCL="${WM_DIR}/pre_route_watermark.tcl"
export POST_DETAIL_ROUTE_TCL="${WM_DIR}/post_route_watermark.tcl"

export INPUTS_DIR="${FLOW_HOME}/OR_inputs/route_wm/${PLATFORM}/${DESIGN}"
export CTS_ODB="${CTS_ODB:-${FLOW_HOME}/results/${PLATFORM}/${DESIGN}/${WM_FLOW_VARIANT}/4_cts.odb}"
export SKIP_RT_WM="1"

echo "[run] seed_routing : ${WM_SEED_HEX}"
echo "[run] fraction f    : ${WATERMARK_FRACTION}"
echo "[run] strength lwm  : ${WATERMARK_STRENGTH}"

make -f ${FLOW_HOME}/Makefile \
     DESIGN_CONFIG="${FLOW_HOME}/designs/${PLATFORM}/${DESIGN}/config.mk" \
     OPENROAD_EXE="${OPENROAD_EXE}" \
     wm_route_wrong_way
