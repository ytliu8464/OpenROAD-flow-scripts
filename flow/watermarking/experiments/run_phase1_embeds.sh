#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# One-shot driver: run place + cts embedders on every active bench that does
# not yet have an embed CSV on disk.  Each embed is fast (seconds) compared to
# a full ORFS flow, so this is cheap to re-run.
#
# Adaptive parameters:
#   By default this script sources drivers/_common.sh (which sources
#   drivers/adaptive_params.sh) and calls ``apply_adaptive_wm_params`` per
#   bench before each embed.  The adaptive layer reads the reference flow's
#   WNS / TCP and sets platform-aware knobs (e.g. WM_CTS_SIBLING_DIST_UM=14
#   on ASAP7 vs 50 on NG45, smaller WM_PAIR_DIST_UM on ASAP7, etc.) so
#   capacity numbers match the rest of the paper's tables.
#   Disable with PDMARKS_ADAPTIVE_PARAMS=0 if you want the embedders' raw
#   defaults (50 µm sibling everywhere, etc.).
#
# Per-stage toggles (handy when you only want to refresh one stage --
# e.g. after a CTS-embed code change that adds a new log line):
#     SKIP_PLACE=1 bash run_phase1_embeds.sh   # only re-embed CTS
#     SKIP_CTS=1   bash run_phase1_embeds.sh   # only re-embed placement
# Default behavior (both unset) is to embed both stages, skipping whichever
# already has the corresponding CSV on disk.
#
# After this finishes, run:
#     python3.11 phase1_capacity.py
#     python3.11 aggregate.py --what capacity
# to re-derive tab:capacity.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FLOW_HOME="$(cd "${HERE}/../.." && pwd)"
EXPERIMENTS_HOME="${EXPERIMENTS_HOME:-${HERE}}"
WM_RESULTS_HOME="${WM_RESULTS_HOME:-${EXPERIMENTS_HOME}/results}"
PLACE="${FLOW_HOME}/watermarking/place_ordering"
CTS="${FLOW_HOME}/watermarking/cts_v2"
EMBED_FLOW_VARIANT="${FLOW_VARIANT:-${PHASE1_EMBED_FLOW_VARIANT:-pdmarks-embed-only}}"

echo "[info] embed FLOW_VARIANT=${EMBED_FLOW_VARIANT}"

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

# Resolve DESIGN_NICKNAME for a given (platform, design) by parsing the
# design's config.mk; falls back to DSGN.
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

# Run each bench in a subshell so:
#   - drivers/_common.sh's `set -euo pipefail` / `exit 2` don't kill the outer loop
#   - adaptive params for one bench don't leak into the next
#   - any per-bench env-var exports are scoped correctly
echo "${BENCHES}" | while read -r _PLAT _DSGN _VAR; do
  [[ -z "${_PLAT}" ]] && continue
  _NICK="$(_resolve_nickname "${_PLAT}" "${_DSGN}")"
  _REF_RES="${FLOW_HOME}/results/${_PLAT}/${_NICK}/${_VAR}"
  _OUT_RES="${WM_RESULTS_HOME}/${_PLAT}/${_NICK}/${EMBED_FLOW_VARIANT}"
  if [[ ! -f "${_REF_RES}/3_place.odb" ]]; then
    echo "[skip] no 3_place.odb for ${_PLAT}/${_DSGN}/${_VAR}"
    continue
  fi

  (
    # Subshell: source _common.sh, which exports FLOW_HOME / FLOW_RES / FLOW_LOG
    # for the reference run and sources adaptive_params.sh.  After this,
    # `apply_adaptive_wm_params <stage>` reads the reference 6_report.json
    # + clock_period.txt to pick platform-aware embed knobs.
    export DESIGN="${_DSGN}"
    export DESIGN_NICKNAME="${_NICK}"
    export PLATFORM="${_PLAT}"
    export WM_FLOW_VARIANT="${_VAR}"
    export FLOW_VARIANT="${EMBED_FLOW_VARIANT}"
    export WM_RESULTS="${_OUT_RES}"
    # shellcheck source=drivers/_common.sh
    source "${HERE}/drivers/_common.sh"

    # ---- placement embed ----
    if [[ "${SKIP_PLACE:-0}" = "1" ]]; then
      echo "[skip-p] ${PLATFORM}/${DESIGN}: SKIP_PLACE=1"
    elif [[ -f "${WM_RESULTS}/wm_place_order_embed.csv" ]]; then
      echo "[skip-p] ${PLATFORM}/${DESIGN}/${FLOW_VARIANT} already has place embed CSV"
    else
      apply_adaptive_wm_params placement
      echo "[run-p] ${PLATFORM}/${DESIGN}/${WM_FLOW_VARIANT} -> ${FLOW_VARIANT}"
      bash "${PLACE}/run_place_wm.sh" || \
        echo "[warn] place embed exited non-zero for ${PLATFORM}/${DESIGN}/${WM_FLOW_VARIANT}"
    fi

    # ---- cts embed ----
    if [[ "${SKIP_CTS:-0}" = "1" ]]; then
      echo "[skip-c] ${PLATFORM}/${DESIGN}: SKIP_CTS=1"
    elif [[ -f "${WM_RESULTS}/wm_cts_pairs_embed.csv" ]]; then
      echo "[skip-c] ${PLATFORM}/${DESIGN}/${FLOW_VARIANT} already has cts embed CSV"
    elif [[ -f "${_REF_RES}/4_cts.odb" ]]; then
      apply_adaptive_wm_params cts
      echo "[run-c] ${PLATFORM}/${DESIGN}/${WM_FLOW_VARIANT} -> ${FLOW_VARIANT}"
      bash "${CTS}/run_cts_wm.sh" || \
        echo "[warn] cts embed exited non-zero for ${PLATFORM}/${DESIGN}/${WM_FLOW_VARIANT}"
    fi
  ) || echo "[warn] bench ${_PLAT}/${_DSGN}/${_VAR} subshell exited non-zero"
done

echo "[done] embed runs complete; refresh tab:capacity with:"
echo "       python3.11 ${HERE}/phase1_capacity.py && \\"
echo "       python3.11 ${HERE}/aggregate.py --what capacity && \\"
echo "       python3.11 ${HERE}/render_tex.py"
