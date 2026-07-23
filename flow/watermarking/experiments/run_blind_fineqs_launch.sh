#!/usr/bin/env bash
# Launch the fine-q_s blind all-stage sweep with bounded parallelism.
# Each command -> its own log under /tmp/blind_logs/; progress to master log.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
mkdir -p /tmp/blind_logs
MASTER=/tmp/blind_fineqs_master.log
: > "$MASTER"
grep -v '^#' run_blind_fineqs.cmds | grep -v '^[[:space:]]*$' | nl -ba -w1 -s'|' \
  | xargs -P 3 -d '\n' -I {} bash -c '
      line="{}"; n="${line%%|*}"; cmd="${line#*|}"
      echo "[start $(date +%H:%M:%S)] #$n $cmd" >> "'"$MASTER"'"
      eval "$cmd" > "/tmp/blind_logs/cmd_${n}.log" 2>&1
      rc=$?
      echo "[done  rc=$rc $(date +%H:%M:%S)] #$n $cmd" >> "'"$MASTER"'"
    '
echo "[all done $(date +%H:%M:%S)]" >> "$MASTER"
