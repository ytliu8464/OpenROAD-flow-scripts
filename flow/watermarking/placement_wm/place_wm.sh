#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Run the ordering placement watermark embed / verify under OpenROAD-Python.
#
# Runs natively by default.  Set SINGULARITY_SIF to run inside a container
# instead (see ../wm_env.sh).
#
# Every tunable is documented in README.md and defaulted in watermark_embed.py.
# This wrapper does NOT re-declare defaults -- it only forwards what the caller
# actually set, so the argparse defaults stay the single source of truth.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../wm_env.sh"

usage() {
  cat <<'EOF'
Usage:
  place_wm.sh embed          embed the watermark (writes ODB + ground-truth CSV)
  place_wm.sh verify         verify one ODB against the embed CSV
  place_wm.sh verify_stages  verify across a list of stage ODBs
  place_wm.sh all            embed, then verify the result

Required (embed):
  WM_INPUT              post-detailed-placement .odb
  WM_OUTPUT_ODB         watermarked .odb to write
  WM_SEED_HEX           seed_placement.hex from gen_key/

Required (verify):
  WM_VERIFY_INPUT       watermarked or suspect .odb
  WM_CELL_LIST          embed CSV (ground truth)

All other WM_* tunables are optional; see README.md for the full table and
watermark_embed.py for the authoritative defaults.
EOF
}

# Forward the WM_* knobs the caller actually set, plus the resolved OpenROAD
# path.  A knob the caller did NOT set is absent from the child environment, so
# the embedder's argparse default applies -- that is what keeps the defaults in
# one place.  (wm_env.sh's own WM_HOME / WM_RESULTS_HOME reach the child by
# ordinary export inheritance; the embedders do not read them.)
run_wm_python() {
  local script="$1"
  local env_args=(PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"
                  OPENROAD_EXE="${OPENROAD_EXE}"
                  PYTHONPATH="${SCRIPT_DIR}:${PYTHONPATH:-}")
  local name
  while IFS= read -r name; do
    env_args+=("${name}=${!name}")
  done < <(compgen -v | grep -E '^WM_' || true)

  wm_require_openroad
  ( cd "${SCRIPT_DIR}" && \
    wm_exec env "${env_args[@]}" \
      "${OPENROAD_EXE}" -python -exit "${SCRIPT_DIR}/${script}" )
}

case "${1:-}" in
  embed)
    run_wm_python watermark_embed.py
    ;;
  verify)
    run_wm_python watermark_verify.py
    ;;
  verify_stages)
    run_wm_python watermark_verify_stages.py
    ;;
  all)
    "$0" embed
    export WM_VERIFY_INPUT="${WM_OUTPUT_ODB:?set WM_OUTPUT_ODB before 'all'}"
    export WM_CELL_LIST="${WM_OUTPUT_CELL_LIST:?set WM_OUTPUT_CELL_LIST before 'all'}"
    "$0" verify
    ;;
  -h|--help|help|"")
    usage
    ;;
  *)
    echo "Unknown command: $1" >&2
    usage
    exit 1
    ;;
esac
