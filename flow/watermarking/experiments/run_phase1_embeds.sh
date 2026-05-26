#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# One-shot driver: run place + cts embedders on every active bench that does
# not yet have an embed CSV on disk.  Each embed is fast (seconds) compared to
# a full ORFS flow, so this is cheap to re-run.
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

# Skip embeds that already produced CSVs in the embed FLOW_VARIANT directory.
echo "${BENCHES}" | while read -r PLAT DSGN VAR; do
  [[ -z "${PLAT}" ]] && continue
  NICK="$(_resolve_nickname "${PLAT}" "${DSGN}")"
  # ORFS writes flow/results/<plat>/<nickname>/ and experiments/results/<plat>/<nickname>/.
  REF_RES="${FLOW_HOME}/results/${PLAT}/${NICK}/${VAR}"
  OUT_RES="${WM_RESULTS_HOME}/${PLAT}/${NICK}/${EMBED_FLOW_VARIANT}"
  if [[ ! -f "${REF_RES}/3_place.odb" ]]; then
    echo "[skip] no 3_place.odb for ${PLAT}/${DSGN}/${VAR}"
    continue
  fi
  if [[ "${SKIP_PLACE:-0}" = "1" ]]; then
    echo "[skip-p] ${PLAT}/${DSGN}: SKIP_PLACE=1"
  elif [[ -f "${OUT_RES}/wm_place_order_embed.csv" ]]; then
    echo "[skip-p] ${PLAT}/${DSGN}/${EMBED_FLOW_VARIANT} already has place embed CSV"
  else
    echo "[run-p] ${PLAT}/${DSGN}/${VAR} -> ${EMBED_FLOW_VARIANT}"
    DESIGN="${DSGN}" DESIGN_NICKNAME="${NICK}" PLATFORM="${PLAT}" \
      WM_FLOW_VARIANT="${VAR}" \
      FLOW_VARIANT="${EMBED_FLOW_VARIANT}" \
      WM_RESULTS="${OUT_RES}" \
      bash "${PLACE}/run_place_wm.sh" || \
      echo "[warn] place embed exited non-zero for ${PLAT}/${DSGN}/${VAR}"
  fi
  if [[ "${SKIP_CTS:-0}" = "1" ]]; then
    echo "[skip-c] ${PLAT}/${DSGN}: SKIP_CTS=1"
  elif [[ -f "${OUT_RES}/wm_cts_pairs_embed.csv" ]]; then
    echo "[skip-c] ${PLAT}/${DSGN}/${EMBED_FLOW_VARIANT} already has cts embed CSV"
  elif [[ -f "${REF_RES}/4_cts.odb" ]]; then
    echo "[run-c] ${PLAT}/${DSGN}/${VAR} -> ${EMBED_FLOW_VARIANT}"
    DESIGN="${DSGN}" DESIGN_NICKNAME="${NICK}" PLATFORM="${PLAT}" \
      WM_FLOW_VARIANT="${VAR}" \
      FLOW_VARIANT="${EMBED_FLOW_VARIANT}" \
      WM_RESULTS="${OUT_RES}" \
      bash "${CTS}/run_cts_wm.sh" || \
      echo "[warn] cts embed exited non-zero for ${PLAT}/${DSGN}/${VAR}"
  fi
done

echo "[done] embed runs complete; refresh tab:capacity with:"
echo "       python3.11 ${HERE}/phase1_capacity.py && \\"
echo "       python3.11 ${HERE}/aggregate.py --what capacity && \\"
echo "       python3.11 ${HERE}/render_tex.py"
