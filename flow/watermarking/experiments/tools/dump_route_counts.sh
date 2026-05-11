#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Dump per-net (wrong-way, total) routed segment counts from an ODB.
# Inputs (env):
#   WM_ODB         absolute path to the routed .odb to read
#   WM_COUNTS_CSV  absolute path to write the CSV to
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${WM_ODB:?WM_ODB must be set}"
: "${WM_COUNTS_CSV:?WM_COUNTS_CSV must be set}"
OPENROAD_EXE="${OPENROAD_EXE:-/home/fetzfs_projects/MISC-ytliu/watermarking/OR0415/OpenROAD/build/bin/openroad}"
SIF="${SINGULARITY_SIF:-/home/tool/singularity/images/ispd26.sif}"

if [[ -z "${SINGULARITY_NAME:-}" ]]; then
  exec singularity exec -B /home /home/tool/singularity/images/ispd26.sif \
    env WM_ODB="${WM_ODB}" WM_COUNTS_CSV="${WM_COUNTS_CSV}" \
        OPENROAD_EXE="${OPENROAD_EXE}" \
        "${OPENROAD_EXE}" -python -exit "${HERE}/dump_route_counts.py"
else
  exec "${OPENROAD_EXE}" -python -exit "${HERE}/dump_route_counts.py"
fi
echo "[dump_route_counts] done -> ${WM_COUNTS_CSV}"
