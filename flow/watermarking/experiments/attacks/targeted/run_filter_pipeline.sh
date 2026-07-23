#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Filter the unfiltered CTS E_C feature CSV down to the embedder's true
# pre-attempt feasible set, then re-run the supervised classifier and dump
# the resulting AUC/precision/recall for each design.
#
# Outputs land alongside the existing diag/rank CSVs but with the
# "_filtered" suffix so the originals are preserved.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_DIR="$(cd "${HERE}/../.." && pwd)"
FLOW_HOME="$(cd "${EXP_DIR}/../.." && pwd)"
WM_HOME="${FLOW_HOME}/watermarking"

SIF="${SINGULARITY_SIF:-/home/tool/singularity/images/ispd26.sif}"
SINGULARITY="$(command -v singularity || echo /usr/local/bin/singularity)"
OPENROAD_EXE="${OPENROAD_EXE:-${FLOW_HOME}/../../OpenROAD/build/bin/openroad}"
SKLEARN_PY="${WM_SKLEARN_PY:-${EXP_DIR}/sbpy}"

DATASETS_DIR="${EXP_DIR}/results/phase3/raw/datasets"
LOG_DIR="${EXP_DIR}/results/phase3/raw/logs"
mkdir -p "${DATASETS_DIR}" "${LOG_DIR}"

# 10 active benches: "platform design wm_flow_variant"
# (matches experiments/bench_matrix.py / experiments/run_phase1_embeds.sh)
read -r -d '' BENCHES <<'EOF' || true
nangate45 aes            watermarking-test1
nangate45 jpeg           watermarking-test1
nangate45 swerv_wrapper  base
nangate45 ariane136      base_tcp3p5
nangate45 bp_multi_top   base_tcp3p2
asap7     aes            base
asap7     jpeg           base_tcp540
asap7     swerv_wrapper  base_tcp1455
asap7     ariane         base_fixed
asap7     cva6           base_tcp950
EOF

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

_resolve_libs() {
  local plat="$1" dsgn="$2" cfg out
  cfg="${FLOW_HOME}/designs/${plat}/${dsgn}/config.mk"
  # Run from FLOW_HOME with an absolute DESIGN_CONFIG path so the platform
  # auto-discovery in scripts/variables.mk resolves correctly.
  out=$(cd "${FLOW_HOME}" && make --no-print-directory print-LIB_FILES \
        DESIGN_CONFIG="${cfg}" 2>/dev/null)
  echo "${out}" | sed -n 's/^LIB_FILES:[[:space:]]*//p' | head -n1
}

# Find the watermarked 4_cts_wm.odb the embedder produced.  The all-stage
# embed variant is the default; fall back to the c-only and embed-only
# variants in that order.
_resolve_wm_odb() {
  local plat="$1" nick="$2"
  for variant in pdmarks-all-stage pdmarks-c-only pdmarks-embed-only; do
    local p="${EXP_DIR}/results/${plat}/${nick}/${variant}/4_cts_wm.odb"
    if [[ -f "${p}" ]]; then echo "${p}"; return 0; fi
  done
  return 1
}

# Find the reference 4_cts.sdc.
_resolve_sdc() {
  local plat="$1" nick="$2" var="$3"
  local p="${FLOW_HOME}/results/${plat}/${nick}/${var}/4_cts.sdc"
  [[ -f "${p}" ]] && echo "${p}" && return 0
  p="${FLOW_HOME}/results/${plat}/${nick}/${var}/3_place.sdc"
  [[ -f "${p}" ]] && echo "${p}" && return 0
  return 1
}

echo "[run_filter] starting ${OPENROAD_EXE}"
echo "[run_filter] datasets: ${DATASETS_DIR}"

for spec in $(seq 0 9); do
  read -r _PLAT _DSGN _VAR <<< "$(echo "${BENCHES}" | sed -n "$((spec+1))p")"
  [[ -z "${_PLAT:-}" ]] && continue
  _NICK="$(_resolve_nickname "${_PLAT}" "${_DSGN}")"
  SLUG="${_PLAT}_${_DSGN}"

  FEAT_IN="${DATASETS_DIR}/feat_${SLUG}_cts.csv"
  FEAT_OUT="${DATASETS_DIR}/feat_${SLUG}_cts_filtered.csv"
  DIAG_OUT="${DATASETS_DIR}/diag_${SLUG}_cts_filtered.json"
  RANK_OUT="${DATASETS_DIR}/rank_${SLUG}_cts_filtered.csv"

  if [[ ! -f "${FEAT_IN}" ]]; then
    echo "[skip] ${SLUG}: no ${FEAT_IN}"
    continue
  fi

  WM_ODB="$(_resolve_wm_odb "${_PLAT}" "${_NICK}")" || { echo "[skip] ${SLUG}: no 4_cts_wm.odb"; continue; }
  WM_LIB_FILES="$(_resolve_libs "${_PLAT}" "${_DSGN}")"
  WM_SDC="$(_resolve_sdc "${_PLAT}" "${_NICK}" "${_VAR}")" || { echo "[warn] ${SLUG}: no sdc"; WM_SDC=""; }
  WM_SETRC="${FLOW_HOME}/platforms/${_PLAT}/setRC.tcl"
  WM_SEED_HEX="${WM_HOME}/gen_key/out/${_DSGN}/seed_cts.hex"

  if [[ ! -f "${WM_SEED_HEX}" ]]; then
    echo "[skip] ${SLUG}: no seed at ${WM_SEED_HEX}"
    continue
  fi

  echo "[run_filter] ${SLUG}"
  echo "  odb : ${WM_ODB}"
  echo "  sdc : ${WM_SDC}"
  echo "  seed: ${WM_SEED_HEX}"

  FILTER_LOG="${LOG_DIR}/filter_${SLUG}_cts.log"
  export WM_ODB WM_LIB_FILES WM_SDC WM_SETRC WM_SEED_HEX
  export WM_FEAT_IN="${FEAT_IN}" WM_FEAT_OUT="${FEAT_OUT}"
  set +e
  "${SINGULARITY}" exec -B /home "${SIF}" "${OPENROAD_EXE}" \
      -python -exit "${HERE}/filter_cts_feasibility.py" \
      > "${FILTER_LOG}" 2>&1
  rc=$?
  set -e
  if [[ ${rc} -ne 0 || ! -f "${FEAT_OUT}" ]]; then
    echo "  filter FAILED rc=${rc}, see ${FILTER_LOG}"
    tail -3 "${FILTER_LOG}" || true
    continue
  fi
  grep -E "^\[filter_cts\]" "${FILTER_LOG}" | tail -2 || true

  CLASSIFY_LOG="${LOG_DIR}/classify_${SLUG}_cts_filtered.log"
  set +e
  "${SKLEARN_PY}" "${HERE}/classify.py" \
      --features "${FEAT_OUT}" \
      --out-json "${DIAG_OUT}" \
      --out-ranking "${RANK_OUT}" \
      > "${CLASSIFY_LOG}" 2>&1
  rc=$?
  set -e
  if [[ ${rc} -ne 0 ]]; then
    echo "  classify FAILED rc=${rc}, see ${CLASSIFY_LOG}"
    tail -3 "${CLASSIFY_LOG}" || true
    continue
  fi
  cat "${DIAG_OUT}" | head -1
done

echo "[run_filter] done"
