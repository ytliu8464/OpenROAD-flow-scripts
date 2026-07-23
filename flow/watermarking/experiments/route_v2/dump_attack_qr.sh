#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Dump canonical q_R for the surgical routing-removal ("cleared") attacked
# layouts used by fig:routing_attack.  The cleared dirs keep 6_final.odb (the
# 5_route.odb was removed to bound disk); filler cells do not change signal
# routing, so 6_final's per-net q_R equals the post-attack detailed route.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP="$(cd "$HERE/.." && pwd)"; cd "$EXP"
OUT="results/phase_route_v2"; mkdir -p "$OUT"
FORCE="${FORCE:-0}"

# label  nick  attackdir
rows=(
  "cleared_jpeg          jpeg           atk-surgical-cleared-jpeg"
  "cleared_swerv_wrapper swerv_wrapper  atk-surgical-cleared-swerv_wrapper"
  "cleared_ariane136     ariane136      atk-surgical-cleared-ariane136"
  "cleared_bp_multi      bp_multi       atk-surgical-cleared-bp_multi_top"
)
for r in "${rows[@]}"; do
  read -r label nick adir <<< "$r"
  csv="$OUT/$label.csv"
  if [[ "$FORCE" != "1" && -s "$csv" ]]; then echo "[skip] $label"; continue; fi
  odb=""
  for cand in "results/nangate45/$nick/$adir/5_route.odb" \
              "results/nangate45/$nick/$adir/6_final.odb"; do
    [[ -f "$cand" ]] && { odb="$cand"; break; }
  done
  [[ -z "$odb" ]] && { echo "[MISS] $label : no ODB in $adir"; continue; }
  echo "[dump] $label <- $odb"
  WM_ODB="$(readlink -f "$odb")" WM_QR_CSV="$(readlink -f "$OUT")/$label.csv" \
    bash tools/dump_route_qr.sh >"$OUT/$label.dumplog" 2>&1 \
    && echo "   ok $(tail -1 "$OUT/$label.dumplog" | sed 's/^.*] //')" \
    || echo "   FAIL $label (see $OUT/$label.dumplog)"
done
echo "[dump_attack_qr] done"
