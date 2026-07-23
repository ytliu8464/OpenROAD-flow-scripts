#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
#
# Phase 2.2 parameter-sensitivity sweep on SweRV NG45 / SweRV ASAP7.
#
# Paper §sensitivity (main.tex lines 1659-1667) declares 1-D sweeps over
# D_pair, theta_HPWL, delta_guard (placement), R_max (CTS),
# f and lambda_wm (routing).  This script implements 6 of those knobs
# (the user dropped R_max and the legacy WM_CTS_NUM_PAIRS sweep) and
# wires each cell through the *full* per-module driver, so every cell
# produces an embed ODB, a verify CSV, and a 6_report.json -- enough to
# populate the paper's claimed Capacity / Extraction / PPA tradeoffs.
#
# Per-cell FLOW_VARIANT is "sens-<stage>-<knob>-<value>" so the per-bench
# results never collide and the aggregator can locate each cell by name.
#
# Knobs:
#   Placement   WM_PAIR_DIST_UM         {0.5, 1.0, 2.0}   um   (paper D_pair)
#               WM_HPWL_EPS_PAIR_DBU    {50, 100, 200}    DBU  (paper theta_HPWL)
#               WM_GUARD_DEGRADE_NS     {0.01, 0.02, 0.05} ns  (paper delta_guard)
#   CTS         WM_CTS_SIBLING_DIST_UM  {25, 50, 100}     um
#   Routing     WATERMARK_FRACTION      {0.025, 0.05, 0.10}    (paper f)
#               WATERMARK_STRENGTH      {10, 100, 1000}        (paper lambda_wm)
#
# Routing sweeps are ASAP7-skipped by default (strict-direction router ->
# Z_R/p_R structurally 0/0.5).  Set SENS_ROUTE_ASAP7=1 to force-include.
#
# Idempotent: cells whose experiments/results/<plat>/<nick>/<FLOW_VARIANT>/
# 6_report.json already exists are skipped (override with SENS_FORCE=1).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_DIR="$(cd "${HERE}/.." && pwd)"
FLOW_HOME="$(cd "${EXP_DIR}/.." && pwd)"
DRIVERS="${EXP_DIR}/drivers"

BENCHES=(
  "nangate45 swerv_wrapper base"
  "asap7     swerv_wrapper base_tcp1455"
)

# Placement knobs (paper-aligned).
PLACE_PAIR_DIST_UM=()
PLACE_HPWL_EPS=()
PLACE_GUARD_NS=()

# CTS knobs.
CTS_SIBLING=()

# Routing knobs.
ROUTE_FRACTION=(0.025 0.05 0.10)
ROUTE_STRENGTH=(10 100 1000)

mkdir -p "${HERE}/results"

# Resolve DESIGN_NICKNAME for (plat, design) by parsing config.mk; falls back
# to the full design name.
_resolve_nickname() {
  local plat="$1" dsgn="$2" cfg nick
  cfg="${FLOW_HOME}/designs/${plat}/${dsgn}/config.mk"
  if [[ -f "${cfg}" ]]; then
    nick="$(make -C "${FLOW_HOME}" --no-print-directory \
        print-DESIGN_NICKNAME DESIGN_CONFIG="${cfg}" 2>/dev/null \
      | sed -n 's/^DESIGN_NICKNAME[[:space:]]*[:=]\?[[:space:]]*//p' \
      | head -n1)"
  fi
  echo "${nick:-${dsgn}}"
}

_variant_name() {
  local stage="$1" knob="$2" value="$3"
  echo "sens-${stage}-${knob}-${value}"
}

_should_skip() {
  local plat="$1" nick="$2" variant="$3"
  [[ "${SENS_FORCE:-0}" = "1" ]] && return 1
  local rep="${EXP_DIR}/logs/${plat}/${nick}/${variant}/6_report.json"
  [[ -f "${rep}" ]]
}

# Run one (stage, knob, value) cell.  Picks the right per-module driver
# based on the stage; sets FLOW_VARIANT so embed + PPA write to the
# sensitivity-namespaced output dir; passes only the swept env var.
_run_cell() {
  local stage="$1" knob_env="$2" value="$3"
  local plat="$4" dsgn="$5" wm_var="$6" nick="$7"
  local label="$8"   # short label for the FLOW_VARIANT (e.g. "D_pair", "f")
  local variant
  variant="$(_variant_name "${stage}" "${label}" "${value}")"
  local log_dir="${HERE}/results"
  mkdir -p "${log_dir}"

  if _should_skip "${plat}" "${nick}" "${variant}"; then
    echo "[sens skip-done] ${plat}/${nick}/${variant}"
    return 0
  fi

  local driver
  case "${stage}" in
    p) driver="${DRIVERS}/run_p_only.sh" ;;
    c) driver="${DRIVERS}/run_c_only.sh" ;;
    r) driver="${DRIVERS}/run_r_only.sh" ;;
    *) echo "[sens] unknown stage '${stage}'"; return 1 ;;
  esac

  echo "[sens] ${plat}/${dsgn} ${variant} (${knob_env}=${value})"
  local log="${log_dir}/${variant}_${plat}_${dsgn}.log"
  # Force-disable the adaptive layer for the *swept* knob -- adaptive
  # defaults are applied only to env vars that are unset, so this is
  # enough to make our value the one the embedder uses.  All other knobs
  # stay adaptive, matching the paper's "one-knob-at-a-time" semantic.
  env \
    DESIGN="${dsgn}" \
    DESIGN_NICKNAME="${nick}" \
    PLATFORM="${plat}" \
    WM_FLOW_VARIANT="${wm_var}" \
    FLOW_VARIANT="${variant}" \
    "${knob_env}=${value}" \
    bash "${driver}" \
    >"${log}" 2>&1 \
    || echo "[sens FAIL] ${plat}/${nick}/${variant} (see ${log})"
}

run_place_sweep() {
  local plat="$1" dsgn="$2" var="$3" nick="$4"
  for v in "${PLACE_PAIR_DIST_UM[@]}"; do
    _run_cell p WM_PAIR_DIST_UM        "${v}" "${plat}" "${dsgn}" "${var}" "${nick}" "D_pair"
  done
  for v in "${PLACE_HPWL_EPS[@]}"; do
    _run_cell p WM_HPWL_EPS_PAIR_DBU   "${v}" "${plat}" "${dsgn}" "${var}" "${nick}" "theta_HPWL"
  done
  for v in "${PLACE_GUARD_NS[@]}"; do
    _run_cell p WM_GUARD_DEGRADE_NS    "${v}" "${plat}" "${dsgn}" "${var}" "${nick}" "delta_guard"
  done
}

run_cts_sweep() {
  local plat="$1" dsgn="$2" var="$3" nick="$4"
  for v in "${CTS_SIBLING[@]}"; do
    _run_cell c WM_CTS_SIBLING_DIST_UM "${v}" "${plat}" "${dsgn}" "${var}" "${nick}" "sibling_um"
  done
}

run_route_sweep() {
  local plat="$1" dsgn="$2" var="$3" nick="$4"
  # Routing channel structurally undefined on ASAP7 (strict-direction
  # router -> 0 wrong-way segments) unless overridden.
  if [[ "${plat}" = "asap7" && "${SENS_ROUTE_ASAP7:-0}" != "1" ]]; then
    echo "[sens] routing sweep skipped for ${plat}/${dsgn} (set SENS_ROUTE_ASAP7=1 to force)"
    return 0
  fi
  for v in "${ROUTE_FRACTION[@]}"; do
    _run_cell r WATERMARK_FRACTION  "${v}" "${plat}" "${dsgn}" "${var}" "${nick}" "f"
  done
  for v in "${ROUTE_STRENGTH[@]}"; do
    _run_cell r WATERMARK_STRENGTH  "${v}" "${plat}" "${dsgn}" "${var}" "${nick}" "lambda_wm"
  done
}

for spec in "${BENCHES[@]}"; do
  read -r plat dsgn var <<<"$spec"
  nick="$(_resolve_nickname "${plat}" "${dsgn}")"
  echo "[sensitivity] sweeping ${plat}/${dsgn}/${var} (nickname=${nick})"
  run_place_sweep "$plat" "$dsgn" "$var" "$nick"
  run_cts_sweep   "$plat" "$dsgn" "$var" "$nick"
  if [[ "${SENS_ROUTE:-1}" = "1" ]]; then
    run_route_sweep "$plat" "$dsgn" "$var" "$nick"
  fi
done
echo "[sensitivity] done.  Verify with sensitivity/verify_sweep.py"
echo "[sensitivity] then aggregate with sensitivity/aggregate_sensitivity.py"
