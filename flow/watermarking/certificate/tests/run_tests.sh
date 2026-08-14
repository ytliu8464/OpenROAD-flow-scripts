#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Run the PDMarks certificate test suite.
#
# Everything here is stdlib-only by design: no OpenROAD, no numpy, no
# scikit-learn, and no `cryptography`.  Tests that need an optional dependency
# (the cross-backend AES-GCM check) skip themselves when it is absent, and the
# openssl cross-checks skip when `openssl` is not on PATH.
#
#   bash certificate/tests/run_tests.sh              # everything
#   bash certificate/tests/run_tests.sh test_cert    # one module
#
# WM_PYTHON overrides the interpreter (see wm_env.sh:wm_python).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/../../wm_env.sh"

PY="$(wm_python)"
echo "[tests] interpreter: ${PY} ($("${PY}" -c 'import sys; print(sys.version.split()[0])'))"
echo "[tests] openssl    : $(command -v openssl || echo '(absent -- DER cross-checks will skip)')"
"${PY}" - <<'PY'
try:
    import cryptography
    print(f"[tests] cryptography: {cryptography.__version__}")
except Exception:
    print("[tests] cryptography: (absent -- backend-equivalence check will skip)")
PY

if [[ $# -gt 0 ]]; then
  exec "${PY}" -m unittest -v "$@"
fi

# -s == -t so the tests directory is its own top-level; that keeps it importable
# without an __init__.py and without polluting the watermarking package space.
cd "${HERE}"
exec "${PY}" -m unittest discover -s . -t . -v
