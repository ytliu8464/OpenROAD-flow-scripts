#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Convenience wrapper around keygen.py / sign_and_derive.py / verify_bundle.py.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Pick a Python >= 3.7 (needed for `from __future__ import annotations`).
# Override with GEN_KEY_PYTHON.
if [[ -n "${GEN_KEY_PYTHON:-}" ]]; then
  PYTHON_BIN="${GEN_KEY_PYTHON}"
else
  PYTHON_BIN=""
  for cand in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python3.7; do
    if command -v "$cand" >/dev/null 2>&1; then
      ver="$($cand -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || true)"
      major="${ver%%.*}"; minor="${ver##*.}"
      if [[ "$major" == "3" && "$minor" -ge 7 ]]; then
        PYTHON_BIN="$cand"
        break
      fi
    fi
  done
  if [[ -z "${PYTHON_BIN}" ]]; then
    echo "gen_key.sh: need Python >= 3.7 (set GEN_KEY_PYTHON to override)" >&2
    exit 1
  fi
fi

usage() {
  cat <<EOF
Usage:
  $0 keygen   --owner-id <id> [--out-dir keys] [--registry registry.json] [--force]
  $0 sign     --sk <sk.pem> --pk <pk.pem> --owner-id <id> --design-id <id>
              [--date YYYY-MM-DD] [--tool-name OpenROAD] [--tool-commit <hash>]
              [--tool-root <dir>] [--nonce <hex>] [--out-dir out/<design>] [--force]
  $0 verify   --bundle-dir <dir>

Produces (sign):
  <out-dir>/{M.json, sig.bin, pk.pem, bundle.json,
             seed_placement.hex, seed_cts.hex, seed_routing.hex}

Consume in downstream stages via:
  export WM_SEED_HEX=<out-dir>/seed_placement.hex
EOF
}

cmd="${1:-}"
shift || true

case "${cmd}" in
  keygen)
    exec "${PYTHON_BIN}" "${SCRIPT_DIR}/keygen.py" "$@"
    ;;
  sign)
    exec "${PYTHON_BIN}" "${SCRIPT_DIR}/sign_and_derive.py" "$@"
    ;;
  verify)
    exec "${PYTHON_BIN}" "${SCRIPT_DIR}/verify_bundle.py" "$@"
    ;;
  -h|--help|help|"")
    usage
    ;;
  *)
    echo "Unknown command: ${cmd}" >&2
    usage
    exit 1
    ;;
esac
