# SPDX-License-Identifier: BSD-3-Clause
# Shared bash setup for PDMarks experiment drivers.
#
# Required env: DESIGN, PLATFORM, WM_FLOW_VARIANT
# Optional env: OWNER_ID, FLOW_VARIANT, PROJ_DIR

set -euo pipefail

export PROJ_DIR="${PROJ_DIR:-/home/fetzfs_projects/MISC-ytliu/watermarking}"
export OPENROAD_EXE="${OPENROAD_EXE:-${PROJ_DIR}/OR0415/OpenROAD/build/bin/openroad}"
export KEPLER_FORMAL_EXE="${KEPLER_FORMAL_EXE:-${PROJ_DIR}/OR0415/kepler-formal/build/src/bin/kepler-formal}"
export FLOW_HOME="${FLOW_HOME:-${PROJ_DIR}/OR0415/OpenROAD-flow-scripts/flow}"
export OWNER_ID="${OWNER_ID:-yiting}"

if [[ -z "${DESIGN:-}" || -z "${PLATFORM:-}" || -z "${WM_FLOW_VARIANT:-}" ]]; then
  echo "[drivers/_common.sh] DESIGN, PLATFORM, WM_FLOW_VARIANT must be set" >&2
  exit 2
fi

export FLOW_RES="${FLOW_HOME}/results/${PLATFORM}/${DESIGN}/${WM_FLOW_VARIANT}"
export FLOW_LOG="${FLOW_HOME}/logs/${PLATFORM}/${DESIGN}/${WM_FLOW_VARIANT}"

ts()  { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] [$(basename "$0")] $*"; }

ensure_keys() {
  local gen="${FLOW_HOME}/watermarking/gen_key"
  if [[ ! -f "${gen}/keys/sk.pem" ]]; then
    log "generating owner keypair in ${gen}/keys"
    ( cd "${gen}" && ./gen_key.sh keygen --owner-id "${OWNER_ID}" --out-dir keys )
  fi
  if [[ ! -f "${gen}/out/${DESIGN}/seed_routing.hex" ]]; then
    log "signing bundle for ${DESIGN}"
    ( cd "${gen}" && ./gen_key.sh sign --sk keys/sk.pem --pk keys/pk.pem \
        --owner-id "${OWNER_ID}" --design-id "${DESIGN}" \
        --out-dir "out/${DESIGN}" --force )
  fi
}
