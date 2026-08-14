#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Dump per-net canonical (wrong_way, total) planar wirelength + geometric
# features from a routed ODB.  See tools/dump_route_qr.py.
# Inputs (env):
#   WM_ODB     absolute path to the routed .odb to read
#   WM_QR_CSV  absolute path to write the CSV to
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/../../wm_env.sh"

: "${WM_ODB:?WM_ODB must be set}"
: "${WM_QR_CSV:?WM_QR_CSV must be set}"

wm_require_openroad
exec wm_exec env WM_ODB="${WM_ODB}" WM_QR_CSV="${WM_QR_CSV}" \
  "${OPENROAD_EXE}" -python -exit "${HERE}/dump_route_qr.py"
