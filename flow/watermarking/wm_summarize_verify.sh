#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026, The OpenROAD Authors
#
# Summarize the placement and CTS stage verifiers into a single ownership
# verdict.
#
# Each stage verifier exits 2 as soon as one claim fails, which is the right
# answer to "did every claim survive" but the wrong one for ownership: a few
# marked objects are legitimately disturbed by routing and filling.  Ownership
# is decided by the extraction rate r_s against a threshold tau (0.75 in the
# PDMarks paper), so this script reads the counts back out and applies it.
#
# Usage: wm_summarize_verify.sh <placement-log> <cts-log> <tau>

set -u

placement_log="${1:?placement log required}"
cts_log="${2:?cts log required}"
tau="${3:-0.75}"

# "pairs checked=102 ok=101"
read_placement() {
    [[ -f "${placement_log}" ]] || return 1
    sed -n 's/.*pairs checked=\([0-9]*\)  *ok=\([0-9]*\).*/\1 \2/p' \
        "${placement_log}" | tail -1
}

# "pairs=32 satisfied=32 failed_parity=0 ..."
read_cts() {
    [[ -f "${cts_log}" ]] || return 1
    sed -n 's/.*pairs=\([0-9]*\) satisfied=\([0-9]*\).*/\1 \2/p' \
        "${cts_log}" | tail -1
}

status=0
printf '\n[INFO WMK-0103] Ownership summary (tau=%s)\n' "${tau}"
printf '  %-10s %-8s %-8s %-8s %s\n' stage claims held rate verdict

report_stage() {
    local name="$1" counts="$2"
    local total held rate verdict
    if [[ -z "${counts}" ]]; then
        printf '  %-10s %-8s %-8s %-8s %s\n' "${name}" - - - "no claims"
        return
    fi
    total="${counts% *}"
    held="${counts#* }"
    if [[ "${total}" -eq 0 ]]; then
        printf '  %-10s %-8s %-8s %-8s %s\n' "${name}" 0 0 - "no claims"
        return
    fi
    rate=$(awk -v h="${held}" -v t="${total}" 'BEGIN{printf "%.4f", h/t}')
    if awk -v r="${rate}" -v x="${tau}" 'BEGIN{exit !(r >= x)}'; then
        verdict=PASS
    else
        verdict=FAIL
        status=2
    fi
    printf '  %-10s %-8s %-8s %-8s %s\n' \
        "${name}" "${total}" "${held}" "${rate}" "${verdict}"
}

report_stage placement "$(read_placement)"
report_stage cts "$(read_cts)"

if [[ "${status}" -eq 0 ]]; then
    printf '[INFO WMK-0105] Ownership evidence holds.\n\n'
else
    printf '[ERROR WMK-0106] At least one stage fell below tau.\n\n'
fi

# The routing channel is a statistical test rather than a claim count; it is
# reported by report_routing_watermark during detailed route, and the full
# Eq. 20 verdict comes from certificate/verify_ownership.py.
exit "${status}"
