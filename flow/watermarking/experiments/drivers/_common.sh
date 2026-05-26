# SPDX-License-Identifier: BSD-3-Clause
# Shared bash setup for PDMarks experiment drivers.
#
# Required env: DESIGN, PLATFORM, WM_FLOW_VARIANT
# Optional env: OWNER_ID, FLOW_VARIANT, PROJ_DIR, DESIGN_NICKNAME
#
# DESIGN holds the ORFS DESIGN_NAME (matches designs/<plat>/<DESIGN>/config.mk
# and gen_key/out/<DESIGN>/).  DESIGN_NICKNAME is the name ORFS uses on disk
# for results / logs (flow/results/<plat>/<DESIGN_NICKNAME>/, etc.); it
# defaults to DESIGN unless the design's config.mk overrides it (e.g.
# bp_multi_top -> bp_multi).  This script auto-discovers DESIGN_NICKNAME from
# config.mk when the caller does not set it explicitly.

set -euo pipefail

export PROJ_DIR="${PROJ_DIR:-/home/fetzfs_projects/MISC-ytliu/watermarking}"
export OPENROAD_EXE="${OPENROAD_EXE:-${PROJ_DIR}/OR0415/OpenROAD/build/bin/openroad}"
export KEPLER_FORMAL_EXE="${KEPLER_FORMAL_EXE:-${PROJ_DIR}/OR0415/kepler-formal/build/src/bin/kepler-formal}"
export FLOW_HOME="${FLOW_HOME:-${PROJ_DIR}/OR0415/OpenROAD-flow-scripts/flow}"
export EXPERIMENTS_HOME="${EXPERIMENTS_HOME:-${FLOW_HOME}/watermarking/experiments}"
export WM_RESULTS_HOME="${WM_RESULTS_HOME:-${EXPERIMENTS_HOME}/results}"
export OWNER_ID="${OWNER_ID:-yiting}"

if [[ -z "${DESIGN:-}" || -z "${PLATFORM:-}" || -z "${WM_FLOW_VARIANT:-}" ]]; then
  echo "[drivers/_common.sh] DESIGN, PLATFORM, WM_FLOW_VARIANT must be set" >&2
  exit 2
fi

# Auto-discover DESIGN_NICKNAME from the design's config.mk if the caller did
# not export it.  Falls back to ${DESIGN}.
if [[ -z "${DESIGN_NICKNAME:-}" ]]; then
  _design_config="${FLOW_HOME}/designs/${PLATFORM}/${DESIGN}/config.mk"
  if [[ -f "${_design_config}" ]]; then
    DESIGN_NICKNAME="$(make -C "${FLOW_HOME}" --no-print-directory \
        print-DESIGN_NICKNAME DESIGN_CONFIG="${_design_config}" 2>/dev/null \
      | sed -n 's/^DESIGN_NICKNAME[[:space:]]*[:=]\?[[:space:]]*//p' \
      | head -n1)"
  fi
  export DESIGN_NICKNAME="${DESIGN_NICKNAME:-${DESIGN}}"
fi

# ORFS writes flow/results/<plat>/<nickname>/ and flow/logs/<plat>/<nickname>/.
export FLOW_RES="${FLOW_HOME}/results/${PLATFORM}/${DESIGN_NICKNAME}/${WM_FLOW_VARIANT}"
export FLOW_LOG="${FLOW_HOME}/logs/${PLATFORM}/${DESIGN_NICKNAME}/${WM_FLOW_VARIANT}"

ts()  { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] [$(basename "$0")] $*"; }

source "$(dirname "${BASH_SOURCE[0]}")/adaptive_params.sh"

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
