#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# One-shot driver: run place + cts embedders on every active bench that does
# not yet have an embed CSV on disk.  Each embed is fast (seconds) compared to
# a full ORFS flow, so this is cheap to re-run.
#
# After this finishes, run:
#     python3.11 phase1_capacity.py
#     python3.11 aggregate.py --what capacity
# to re-derive tab:capacity.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FLOW_HOME="$(cd "${HERE}/../.." && pwd)"
PLACE="${FLOW_HOME}/watermarking/place_ordering"
CTS="${FLOW_HOME}/watermarking/cts_v2"

read -r -d '' BENCHES <<'EOF' || true
nangate45 aes            watermarking-test1
nangate45 jpeg           watermarking-test1
nangate45 swerv_wrapper  base
nangate45 ariane136      base_tcp3p5
asap7     aes            base
asap7     jpeg           base_tcp540
asap7     swerv_wrapper  base_tcp1455
EOF

# Skip place embeds that already produced an embed CSV.
echo "${BENCHES}" | while read -r PLAT DSGN VAR; do
  [[ -z "${PLAT}" ]] && continue
  RES="${FLOW_HOME}/results/${PLAT}/${DSGN}/${VAR}"
  if [[ ! -f "${RES}/3_place.odb" ]]; then
    echo "[skip] no 3_place.odb for ${PLAT}/${DSGN}/${VAR}"
    continue
  fi
  if [[ -f "${RES}/wm_place_order_embed_v2.csv" ]]; then
    echo "[skip-p] ${PLAT}/${DSGN}/${VAR} already has place embed CSV"
  else
    echo "[run-p] ${PLAT}/${DSGN}/${VAR}"
    DESIGN="${DSGN}" PLATFORM="${PLAT}" WM_FLOW_VARIANT="${VAR}" \
      bash "${PLACE}/run_place_wm.sh" || \
      echo "[warn] place embed exited non-zero for ${PLAT}/${DSGN}/${VAR}"
  fi
  if [[ -f "${RES}/wm_cts_pairs_embed.csv" ]]; then
    echo "[skip-c] ${PLAT}/${DSGN}/${VAR} already has cts embed CSV"
  elif [[ -f "${RES}/4_cts.odb" ]]; then
    echo "[run-c] ${PLAT}/${DSGN}/${VAR}"
    DESIGN="${DSGN}" PLATFORM="${PLAT}" WM_FLOW_VARIANT="${VAR}" \
      bash "${CTS}/run_cts_wm.sh" || \
      echo "[warn] cts embed exited non-zero for ${PLAT}/${DSGN}/${VAR}"
  fi
done

echo "[done] embed runs complete; refresh tab:capacity with:"
echo "       python3.11 ${HERE}/phase1_capacity.py && \\"
echo "       python3.11 ${HERE}/aggregate.py --what capacity && \\"
echo "       python3.11 ${HERE}/render_tex.py"
