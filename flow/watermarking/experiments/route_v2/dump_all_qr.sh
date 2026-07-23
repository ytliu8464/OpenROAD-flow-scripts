#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Batch-dump canonical per-net q_R CSVs for every NG45 routing layout the
# revised routing analysis needs.  Idempotent: skips a CSV that already exists
# unless FORCE=1.  Writes into results/phase_route_v2/<label>.csv.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP="$(cd "$HERE/.." && pwd)"; cd "$EXP"
OUT="results/phase_route_v2"; mkdir -p "$OUT"
DUMP="tools/dump_route_qr.sh"
FORCE="${FORCE:-0}"

# label  odb_path
emit() {
  local label="$1" odb="$2"
  local csv="$OUT/$label.csv"
  if [[ "$FORCE" != "1" && -s "$csv" ]]; then echo "[skip] $label (exists)"; return; fi
  if [[ ! -f "$odb" ]]; then echo "[MISS] $label : no ODB at $odb"; return; fi
  echo "[dump] $label <- $odb"
  WM_ODB="$(readlink -f "$odb")" WM_QR_CSV="$(readlink -f "$OUT")/$label.csv" \
    bash "$DUMP" >"$OUT/$label.dumplog" 2>&1 \
    && echo "   ok $(tail -1 "$OUT/$label.dumplog" | sed 's/^.*] //')" \
    || echo "   FAIL $label (see $OUT/$label.dumplog)"
}

R=results/nangate45
for d in jpeg swerv_wrapper ariane136 bp_multi; do
  emit "ronly_$d"     "$R/$d/pdmarks-r-only/5_route.odb"
  emit "allstage_$d"  "$R/$d/pdmarks-all-stage/5_route.odb"
done

# sensitivity (SweRV): f and lambda sweeps
for v in sens-r-f-0.025 sens-r-f-0.05 sens-r-f-0.10 \
         sens-r-lambda_wm-10 sens-r-lambda_wm-100 sens-r-lambda_wm-1000; do
  emit "sens_swerv_${v}" "$R/swerv_wrapper/$v/5_route.odb"
done

echo "[dump_all_qr] done -> $OUT"
