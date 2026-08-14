#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Run the CTS fanout-parity watermark embed / verify under OpenROAD-Python.
#
# Runs natively by default.  Set SINGULARITY_SIF to run inside a container
# instead (see ../wm_env.sh).
#
# Every tunable is documented in README.md and defaulted in
# cts_watermark_embed.py.  This wrapper does NOT re-declare defaults -- it only
# forwards what the caller actually set, so the argparse defaults stay the
# single source of truth.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../wm_env.sh"

usage() {
  cat <<'EOF'
Usage:
  cts_wm.sh embed          embed the watermark (writes ODB + ground-truth CSV)
  cts_wm.sh verify         verify one ODB against the embed CSV
  cts_wm.sh verify_stages  verify across a list of stage ODBs
  cts_wm.sh all            embed, then verify the result

Required (embed):
  WM_CTS_INPUT          post-CTS .odb (e.g. .../4_cts.odb)
  WM_CTS_OUTPUT_ODB     watermarked .odb to write
  WM_CTS_OUTPUT_CSV     ground-truth CSV of embedded pairs
  WM_SEED_HEX           seed_cts.hex from gen_key/

Required (verify):
  WM_CTS_VERIFY_INPUT   .odb to check
  WM_CELL_LIST          embed CSV (ground truth)

Required (verify_stages):
  WM_CELL_LIST          embed CSV (ground truth)
  WM_VERIFY_STAGES      'label:odb,label:odb,...'

All other WM_CTS_* tunables are optional; see README.md for the full table and
cts_watermark_embed.py for the authoritative defaults.

Example:
  export WM_SEED_HEX=.../gen_key/out/aes/seed_cts.hex
  export WM_CTS_INPUT=.../4_cts.odb
  export WM_CTS_OUTPUT_ODB=.../4_cts_wm.odb
  export WM_CTS_OUTPUT_CSV=.../wm_cts_pairs_embed.csv
  ./cts_wm.sh all
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
    run_wm_python cts_watermark_embed.py
    ;;
  verify)
    run_wm_python cts_watermark_verify.py
    ;;
  verify_stages)
    run_wm_python cts_watermark_verify_stages.py
    ;;
  all)
    "$0" embed
    export WM_CTS_VERIFY_INPUT="${WM_CTS_OUTPUT_ODB:?set WM_CTS_OUTPUT_ODB before 'all'}"
    export WM_CELL_LIST="${WM_CTS_OUTPUT_CSV:?set WM_CTS_OUTPUT_CSV before 'all'}"
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
