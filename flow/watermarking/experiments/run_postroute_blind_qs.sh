#!/usr/bin/env bash
# All-stage blind attack (placement swap + CTS sink-move + reroute watermark
# nets), then post-route PPA -- keeps the watermarked flow.
#   Usage: bash run_postroute_blind_qs.sh <platform> <design> <q_s>
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$HERE"
export EXPERIMENTS_HOME="$HERE"
python3.11 attacks/blind/run_postroute_blind.py \
  --platform "${1:?platform}" --design "${2:?design}" --qs "${3:?q_s}"
