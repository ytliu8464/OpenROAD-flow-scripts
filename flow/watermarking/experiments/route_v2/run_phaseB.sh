#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Phase-B chain: waits for compute_tables.py (tab:ppa / survival / sensitivity)
# to finish, then runs the remaining routing-v2 regenerations serially so they
# do not contend for the oversubscribed host:
#   1. routing_attack_fig.py   (fig:routing_attack + post-attack T_R,p_R)
#   2. wrong_key_routing.py     (tab:wrong-key routing panel, SweRV NG45)
#   3. targeted_routing.py      (tab:targeted_auc routing rows; sklearn->singularity)
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP="$(cd "$HERE/.." && pwd)"; cd "$EXP"
OUT="results/phase_route_v2"
SIF="${SINGULARITY_SIF:-/home/tool/singularity/images/ispd26.sif}"

echo "[phaseB] waiting for compute_tables sensitivity JSON ..."
until [ -s "$OUT/route_v2_sensitivity.json" ]; do sleep 15; done
# give the driver a moment to also flush ppa/survival
sleep 3
echo "[phaseB] compute_tables done; starting downstream jobs $(date '+%H:%M:%S')"

echo "[phaseB] 1/3 routing_attack_fig.py"
python3.12 -u route_v2/routing_attack_fig.py > "$OUT/routing_attack_fig.out" 2>&1 \
  && echo "   ok" || echo "   FAIL (see $OUT/routing_attack_fig.out)"

echo "[phaseB] 2/3 wrong_key_routing.py (SweRV NG45)"
python3.12 -u route_v2/wrong_key_routing.py --design swerv_wrapper \
    --design-id swerv_wrapper --seed-name swerv_wrapper -n 5000 --fraction 0.01 \
  > "$OUT/wrong_key_routing.out" 2>&1 \
  && echo "   ok" || echo "   FAIL (see $OUT/wrong_key_routing.out)"

echo "[phaseB] 3/3 targeted_routing.py (sklearn in singularity)"
singularity exec -B /home "$SIF" python3 -u route_v2/targeted_routing.py \
  > "$OUT/targeted_routing.out" 2>&1 \
  && echo "   ok" || echo "   FAIL (see $OUT/targeted_routing.out)"

echo "[phaseB] all done $(date '+%H:%M:%S')"
