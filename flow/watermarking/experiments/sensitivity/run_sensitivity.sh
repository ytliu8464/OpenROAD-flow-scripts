#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Phase 2.2 parameter sensitivity sweep on SweRV NG45 / SweRV ASAP7.
#
# Sweeps three knobs per stage and labels each variant FLOW_VARIANT so the
# PPA harness can pick them up automatically.  All sweeps run the embed
# *only* (no PPA round-trip) since the paper's sensitivity table reports
# extraction-rate / Pc / capacity changes, not full PPA.  If you do want
# PPA deltas, set SENS_PPA=1 to also run the corresponding driver/run_*_only.sh
# for each point.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FLOW_HOME="$(cd "${HERE}/../../.." && pwd)"
PLACE="${FLOW_HOME}/watermarking/place_ordering"
CTS="${FLOW_HOME}/watermarking/cts_v2"

BENCHES=(
  "nangate45 swerv_wrapper base"
  "asap7     swerv_wrapper base_tcp1455"
)

# Placement sweeps (overrides exposed by run_place_wm.sh).
PLACE_PAIRS_PER_TILE=(2 4 8)
PLACE_GRID=(4 6 8)
PLACE_HPWL_EPS=(50 100 200)

# CTS sweeps.
CTS_PAIRS=(16 32 64)
CTS_SIBLING=(25 50 100)

# Routing sweeps.
ROUTE_FRACTION=(0.025 0.05 0.10)
ROUTE_STRENGTH=(10 100 1000)

mkdir -p "${HERE}/results"

run_place_sweep() {
  local plat="$1" dsgn="$2" var="$3"
  for ppt in "${PLACE_PAIRS_PER_TILE[@]}"; do
    local tag="sens_p_ppt${ppt}"
    DESIGN="${dsgn}" PLATFORM="${plat}" WM_FLOW_VARIANT="${var}" \
    WM_PAIRS_PER_TILE="${ppt}" \
    WM_LOG_TAG="${tag}" \
      bash "${PLACE}/run_place_wm.sh" >/dev/null 2>&1 || true
  done
  for g in "${PLACE_GRID[@]}"; do
    local tag="sens_p_grid${g}"
    DESIGN="${dsgn}" PLATFORM="${plat}" WM_FLOW_VARIANT="${var}" \
    WM_GRID="${g}x${g}" WM_LOG_TAG="${tag}" \
      bash "${PLACE}/run_place_wm.sh" >/dev/null 2>&1 || true
  done
  for eps in "${PLACE_HPWL_EPS[@]}"; do
    local tag="sens_p_hpwl${eps}"
    DESIGN="${dsgn}" PLATFORM="${plat}" WM_FLOW_VARIANT="${var}" \
    WM_HPWL_EPS="${eps}" WM_LOG_TAG="${tag}" \
      bash "${PLACE}/run_place_wm.sh" >/dev/null 2>&1 || true
  done
}

run_cts_sweep() {
  local plat="$1" dsgn="$2" var="$3"
  for p in "${CTS_PAIRS[@]}"; do
    local tag="sens_c_pairs${p}"
    DESIGN="${dsgn}" PLATFORM="${plat}" WM_FLOW_VARIANT="${var}" \
    WM_CTS_PAIRS="${p}" WM_LOG_TAG="${tag}" \
      bash "${CTS}/run_cts_wm.sh" >/dev/null 2>&1 || true
  done
  for s in "${CTS_SIBLING[@]}"; do
    local tag="sens_c_sib${s}"
    DESIGN="${dsgn}" PLATFORM="${plat}" WM_FLOW_VARIANT="${var}" \
    WM_CTS_SIBLING_DIST_UM="${s}" WM_LOG_TAG="${tag}" \
      bash "${CTS}/run_cts_wm.sh" >/dev/null 2>&1 || true
  done
}

run_route_sweep() {
  local plat="$1" dsgn="$2" var="$3"
  for f in "${ROUTE_FRACTION[@]}"; do
    DESIGN="${dsgn}" PLATFORM="${plat}" WM_FLOW_VARIANT="${var}" \
    WATERMARK_FRACTION="${f}" \
    FLOW_VARIANT="sens-r-f${f}" \
      bash "${FLOW_HOME}/watermarking/routing_wrong_way/run.sh" \
      >"${HERE}/results/route_f${f}_${plat}_${dsgn}.log" 2>&1 || true
  done
  for s in "${ROUTE_STRENGTH[@]}"; do
    DESIGN="${dsgn}" PLATFORM="${plat}" WM_FLOW_VARIANT="${var}" \
    WATERMARK_STRENGTH="${s}" \
    FLOW_VARIANT="sens-r-lwm${s}" \
      bash "${FLOW_HOME}/watermarking/routing_wrong_way/run.sh" \
      >"${HERE}/results/route_lwm${s}_${plat}_${dsgn}.log" 2>&1 || true
  done
}

for spec in "${BENCHES[@]}"; do
  read -r plat dsgn var <<<"$spec"
  echo "[sensitivity] sweeping ${plat}/${dsgn}/${var}"
  run_place_sweep "$plat" "$dsgn" "$var"
  run_cts_sweep   "$plat" "$dsgn" "$var"
  if [[ "${SENS_ROUTE:-0}" = "1" ]]; then
    run_route_sweep "$plat" "$dsgn" "$var"
  fi
done
echo "[sensitivity] done.  Aggregate with experiments/sensitivity/aggregate_sensitivity.py"
