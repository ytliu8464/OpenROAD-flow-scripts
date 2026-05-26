#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
#
# One-shot: unlock every existing atk_p_*.odb and atk_tp_*.odb in place,
# using each design's pdmarks-all-stage/3_place_order_wm.odb as the
# reference for "which cells were legitimately locked at embed time."
# The per-attack patch in attack_placement.py already does this on new
# attacks; this script catches up the ones that were written before the
# patch landed.
#
# Idempotent: re-running on an already-unlocked ODB is a no-op (the script
# reports "LOCKED -> PLACED: 0").
#
# Usage:
#   bash tools/unlock_all_atk_placement.sh [glob-of-extra-odbs ...]
#
# Default sweeps both atk_p_* and atk_tp_*; extra globs let you target a
# subset (e.g. "results/phase3/raw/atk_p_nangate45_aes_*.odb").

set -euo pipefail

HERE=$(cd "$(dirname "$0")/.." && pwd)
RAW="$HERE/results/phase3/raw"

SIF=${SINGULARITY_SIF:-/home/tool/singularity/images/ispd26.sif}
ORE=${OPENROAD_EXE:-/home/fetzfs_projects/MISC-ytliu/watermarking/OR0415/OpenROAD/build/bin/openroad}

if [[ $# -gt 0 ]]; then
  files=("$@")
else
  shopt -s nullglob
  files=("$RAW"/atk_p_*.odb "$RAW"/atk_tp_*.odb)
fi

if [[ ${#files[@]} -eq 0 ]]; then
  echo "[unlock-all] no atk_p_*/atk_tp_* ODBs found under $RAW"
  exit 0
fi

n_ok=0
n_fail=0
for odb in "${files[@]}"; do
  name=$(basename "$odb")
  # Filenames are atk_{p,tp}_<platform>_<design>_qs<q>.odb
  rest=${name#atk_*_}      # strip leading atk_ + initial + _
  plat=${rest%%_*}
  rest=${rest#*_}
  qs=${rest##*_qs}
  qs=${qs%.odb}
  design=${rest%_qs*}

  # design_nickname mapping: only bp_multi_top -> bp_multi today
  nick=$design
  [[ $design == "bp_multi_top" ]] && nick=bp_multi

  ref="$HERE/results/$plat/$nick/pdmarks-all-stage/3_place_order_wm.odb"
  if [[ ! -f $ref ]]; then
    echo "[unlock-all][skip] no reference ODB for $name (expected $ref)"
    n_fail=$((n_fail+1))
    continue
  fi

  echo "[unlock-all] $name (ref: $plat/$nick/pdmarks-all-stage)"
  if WM_ODB="$odb" WM_OUT_ODB="$odb" WM_REF_ODB="$ref" \
       singularity exec -B /home "$SIF" "$ORE" -python -exit \
         "$HERE/tools/unlock_atk_placement_odb.py" 2>&1 | grep '\[unlock\]'; then
    n_ok=$((n_ok+1))
  else
    n_fail=$((n_fail+1))
  fi
done

echo "[unlock-all] done: $n_ok ok, $n_fail failed (of ${#files[@]} files)"
