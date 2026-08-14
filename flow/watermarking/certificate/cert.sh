#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Watermark certificate and ownership verification (paper Section IV.D).
#
#   cert.sh certify       seal the accepted claims into wm_cert.bin + commitment
#   cert.sh verify        run the full Eq. 20 ownership check on a suspect layout
#   cert.sh show          print a certificate's metadata (needs the key)
#   cert.sh stamp-request re-emit the RFC 3161 request from a commitment record
#   cert.sh stamp-verify  check a TSA token against a request
#
# Everything is plain Python: the certificate library is stdlib-only so it also
# loads inside `openroad -python`, where the claim-verifiers run.  `verify`
# needs numpy for the routing statistic and therefore prefers experiments/sbpy;
# it still works without it, reporting routing as unavailable and deciding on
# placement and CTS alone.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../wm_env.sh"

usage() {
  cat <<'EOF'
Usage:
  cert.sh certify        [--results-dir DIR] [--stages placement,cts,routing] ...
  cert.sh verify         --cert FILE --master-seed-hex SRC [--suspect-odb ODB] ...
  cert.sh show           --cert FILE --master-seed-hex SRC
  cert.sh stamp-request  --commit wm_commit.json [--out FILE]
  cert.sh stamp-verify   --tsr FILE [--tsq FILE] [--tsa-cafile FILE]

certify reads DESIGN / PLATFORM / WM_FLOW_VARIANT / WM_RESULTS from the
environment, so inside a driver it usually needs no arguments at all.

Obtaining a timestamp token (once per certificate, needs network access):

  curl -s -H 'Content-Type: application/timestamp-query' \
       --data-binary @<results>/wm_commit.tsq \
       https://freetsa.org/tsr > <results>/wm_commit.tsr

The commitment is not binding until that token exists -- see README.md.
EOF
}

# Prefer an interpreter with numpy for `verify` (the routing statistic needs
# it); plain python3 is fine for everything else.  If no such interpreter
# exists we still run: verify_ownership reports routing as unavailable and
# decides on placement and CTS alone, which is the documented degradation.
# Note sbpy is only usable if it can *actually* resolve a stacked interpreter
# -- it exits non-zero when it cannot, so probe it rather than trusting -x.
pick_python() {
  local want_numpy="${1}"
  local py
  py="$(wm_python)"
  if [[ "${want_numpy}" != "1" ]]; then
    echo "${py}"
    return
  fi
  if "${py}" -c 'import numpy' >/dev/null 2>&1; then
    echo "${py}"
    return
  fi
  if [[ -x "${EXPERIMENTS_HOME}/sbpy" ]] \
     && "${EXPERIMENTS_HOME}/sbpy" -c 'import numpy' >/dev/null 2>&1; then
    echo "${EXPERIMENTS_HOME}/sbpy"
    return
  fi
  echo "[cert.sh] no interpreter with numpy; the routing stage will be" >&2
  echo "          reported as unavailable and the decision will rest on" >&2
  echo "          placement and CTS." >&2
  echo "${py}"
}

cmd="${1:-}"
[[ $# -gt 0 ]] && shift || true

case "${cmd}" in
  certify)
    exec "$(pick_python 0)" "${SCRIPT_DIR}/certify.py" "$@"
    ;;
  verify)
    exec "$(pick_python 1)" "${SCRIPT_DIR}/verify_ownership.py" "$@"
    ;;
  show)
    exec "$(pick_python 0)" - "$@" <<'PY'
import argparse, json, os, sys
sys.path.insert(0, os.environ["WM_HOME"])
import wm_cert as wc

p = argparse.ArgumentParser(prog="cert.sh show")
p.add_argument("--cert", required=True)
p.add_argument("--master-seed-hex", required=True)
a = p.parse_args()

with open(a.cert, "rb") as f:
    blob = f.read()
cert = wc.open_certificate(wc.load_master_seed(a.master_seed_hex), blob)
print(json.dumps({
    "cert_sha256": wc.cert_sha256(blob).hex(),
    "id_d0_sha256": cert.header.id_d0.hex(),
    "nu_hex": cert.header.nu.hex(),
    "aead_backend": cert.backend,
    "n_placement_claims": len(cert.gamma.placement),
    "n_cts_claims": len(cert.gamma.cts),
    "meta": cert.gamma.meta,
}, indent=2, sort_keys=True))
PY
    ;;
  stamp-request)
    exec "$(pick_python 0)" - "$@" <<'PY'
import argparse, json, os, sys
sys.path.insert(0, os.environ["WM_HOME"])
import wm_cert as wc

p = argparse.ArgumentParser(prog="cert.sh stamp-request")
p.add_argument("--commit", required=True)
p.add_argument("--out", default=None)
p.add_argument("--tsa-policy", default=None)
a = p.parse_args()

with open(a.commit) as f:
    rec = json.load(f)
digest = bytes.fromhex(rec["record_sha256"])
req = wc.build_timestamp_request(digest, policy_oid=a.tsa_policy)
out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.commit)),
                            "wm_commit.tsq")
with open(out, "wb") as f:
    f.write(req)
print(f"[stamp-request] SHA256(R) = {rec['record_sha256']}")
print(f"[stamp-request] wrote {out} ({len(req)} bytes)")
print("[stamp-request] submit it, e.g.:")
print("  curl -s -H 'Content-Type: application/timestamp-query' \\")
print(f"       --data-binary @{out} https://freetsa.org/tsr > "
      f"{os.path.splitext(out)[0]}.tsr")
PY
    ;;
  stamp-verify)
    exec "$(pick_python 0)" - "$@" <<'PY'
import argparse, json, os, sys
sys.path.insert(0, os.environ["WM_HOME"])
import wm_cert as wc

p = argparse.ArgumentParser(prog="cert.sh stamp-verify")
p.add_argument("--tsr", required=True)
p.add_argument("--tsq", default=None)
p.add_argument("--tsa-cafile", default=None)
p.add_argument("--tsa-untrusted", default=None)
a = p.parse_args()

result = wc.record_timestamp_response(a.tsr, a.tsq, cafile=a.tsa_cafile,
                                      untrusted=a.tsa_untrusted)
print(json.dumps(result, indent=2, sort_keys=True))
sys.exit(0 if result["verified"] else 2)
PY
    ;;
  -h|--help|help|"")
    usage
    ;;
  *)
    echo "Unknown command: ${cmd}" >&2
    usage
    exit 1
    ;;
esac
