# Watermark Certificate

The accepted placement and CTS claims are serialized, sealed under AES-256-GCM,
bound to a design and a watermark instance by a SHA-256 key commitment, and
timestamped before the layout is released.

```
K_Γ = HMAC-SHA256(K, LP(ID(D₀)) ‖ LP(ν) ‖ LP("cert"))                  (Eq. 17)
C_Γ = AES-256-GCM-Enc_{K_Γ}(Γ;  aad = LP(ID(D₀))‖LP(ν),  nonce = ν)    (Eq. 18)
c   = SHA256(K ‖ ID(D₀) ‖ ν ‖ SHA256(C_Γ))                             (Eq. 19)
R   = (ID(D₀), ν, SHA256(C_Γ), c)   →  RFC 3161 timestamp
```

`LP(x)` prefixes `x` with its big-endian uint32 length, the framing that
[`wm_prf.hmac_digest`](../wm_prf.py) already uses. The four fields of Eq. 19 are
fixed-length, 32, 32, 12 and 32 bytes, so its plain concatenation is unambiguous
and is written exactly as the paper states.

## What `K` is

`K` is the 32-byte master seed from [`../gen_key/`](../gen_key/), that is
`SHA-256(Ed25519_sig(sk, M))`. It is the same value Eq. 2 feeds into the
per-stage KDF. The certificate key and the stage keys are siblings under one
secret, separated by domain.

`ν` and `ID(D₀)` are fixed at certification time, after embedding, and are not
part of the signed binding message `M`. Adding a field to `M` would change the
signature, hence `master_seed`, hence every stage seed, which would invalidate
every watermark already embedded. Their integrity comes from the AEAD associated
data, from `c`, and from the timestamp.

## Files

| File | Description |
| ----- | ----- |
| [`certify.py`](certify.py) | Build Γ, seal it, commit, and emit the `.tsq`. |
| [`verify_ownership.py`](verify_ownership.py) | The full Eq. 20 sequence on a suspect layout. |
| [`cert.sh`](cert.sh) | Wrapper for `certify`, `verify`, `show`, `stamp-request` and `stamp-verify`. |
| [`tests/`](tests/) | Known-answer and equivalence tests. Standard library only. |

The library lives one level up so it is importable from inside
`openroad -python`: [`../wm_cert.py`](../wm_cert.py) for encoding, sealing,
opening, commitment and DER; [`../wm_aesgcm.py`](../wm_aesgcm.py) for the
vendored AES-GCM; and [`../wm_claims.py`](../wm_claims.py) for the claim loading
the four verifiers use.

## Commands

### Certify

The `certify` command reads the embed CSVs, seals the accepted claims, and
writes the commitment record. Certification runs automatically in the all-stage
driver.

```bash
DESIGN=jpeg PLATFORM=nangate45 WM_FLOW_VARIANT=base \
  bash experiments/drivers/run_all_stage.sh        # PDMARKS_CERTIFY=0 to skip
```

The single-stage runners take `PDMARKS_CERTIFY=1` to opt in. To run it by hand:

```bash
./cert.sh certify --results-dir <results> --stages placement,cts,routing
./cert.sh show    --cert <results>/wm_cert.bin --master-seed-hex <seed>
```

Outputs land in `--out-dir`, which defaults to `--results-dir`.

| File | Description |
| ----- | ----- |
| `wm_cert.bin` | 60-byte header followed by `C_Γ`. |
| `wm_commit.json` | ID(D₀), ν, SHA256(C_Γ), `c` and SHA256(R). Public. |
| `wm_commit.tsq` | DER RFC 3161 request over SHA256(R). |
| `wm_id_d0.json` | The ID(D₀) preimage, for audit. Public. |
| `wm_cert_manifest.json` | What was sealed, and from which CSVs. |

The plaintext embed CSVs are read and left in place. The analysis harness, the
attack campaigns and the wrong-key sweep all still read them directly.

`C_Γ` is exactly bytes 60 to EOF, so its digest is reproducible with no parser.

```bash
tail -c +61 wm_cert.bin | sha256sum        # == cert_sha256 in wm_commit.json
```

### Verify ownership

The `verify` command runs the admissibility checks and then the per-stage
evidence checks against a suspect layout.

```bash
./cert.sh verify --cert <results>/wm_cert.bin \
                 --commit <results>/wm_commit.json \
                 --tsr <results>/wm_commit.tsr \
                 --tsa-cafile <results>/freetsa_cacert.pem \
                 --master-seed-hex <seed-or-bundle> \
                 --suspect-odb <suspect>/5_route.odb \
                 --out-json verdict.json
```

The cheap checks come first, so a wrong key costs nothing.

0. `SHA256(C_Γ)` matches a commitment record, otherwise `cert_hash_mismatch`.
1. Eq. 19, otherwise `commitment_mismatch`.
2. Eqs. 17 and 18, authenticate and decrypt, otherwise `aead_auth_failed`.
3. Evaluate Γ_P and Γ_C against the suspect layout.
4. Derive `K̂_R`, rebuild `WM_R` with `f` from the certificate, run the statistic.
5. Accept if at least two available stages pass, per Eq. 16.

| Exit | Description |
| ----- | ----- |
| 0 | Admissible and accepted. |
| 2 | Admissible, not accepted. |
| 3 | Inadmissible. Eq. 19 or Eq. 18 failed. |
| 1 | Operational error. |

Exit code `3` is a different outcome from "the watermark is absent" and should
not be conflated with it.

**Extraction-rate denominators.** `r_s` divides by `|Γ_s|`, not by the number of
claims the verifier managed to look at. The paper counts a certified object that
is missing or cannot be located unambiguously as a mismatch, and
`watermark_verify.verify_from_csv` skips a nameless row without counting it, so
dividing by the checked count would drop such claims from both the numerator and
the denominator.

**Verification does not recompute ID(D₀).** It enters only the KDF, the AAD and
the commitment, all read from the certificate header, so an owner who no longer
has `D₀` can still verify. `--recheck-id` is an optional owner-side integrity
check.

**Thresholds.** `τ_P = τ_C = 0.75` and `α_R = 1e-4`, from
[`../experiments/lib/thresholds.py`](../experiments/lib/thresholds.py).
`experiments/wrong_key/run_wrong_key.py` uses `α_R = 0.05` for its
null-distribution sweep. That is a different quantity and the two should not be
unified.

## Obtaining a timestamp

The `certify` command emits a request. A `.tsq` with no `.tsr` proves nothing:
until a timestamping authority has signed it, `wm_commit.json` is an unattested
local claim and there is no evidence the key was chosen before the layout was
released. Obtain the token once per certificate.

```bash
curl -s -H 'Content-Type: application/timestamp-query' \
     --data-binary @<results>/wm_commit.tsq \
     https://freetsa.org/tsr > <results>/wm_commit.tsr

# one-time: the TSA's trust anchors
curl -s https://freetsa.org/files/cacert.pem > <results>/freetsa_cacert.pem
curl -s https://freetsa.org/files/tsa.crt    > <results>/freetsa_tsa.crt

./cert.sh stamp-verify --tsr <results>/wm_commit.tsr \
                       --tsq <results>/wm_commit.tsq \
                       --tsa-cafile <results>/freetsa_cacert.pem
```

Record the token against the certificate with `certify --tsr <path>`, or pass it
to `verify_ownership` with `--tsr`.

No script in this repository makes a network call. This is the one manual step.
The `stamp-request` command regenerates the `.tsq` from `wm_commit.json` alone,
so the preimage is reproducible from the public record.

### Earliest valid record

The `--commit` option of `verify_ownership` is repeatable, paired positionally
with `--tsr`. Records are grouped by owner and ID(D₀). The earliest record whose
token verifies, and whose `cert_sha256` names the certificate under test, wins.
Every rejected record is listed in the verdict with its `genTime` and a reason.

Two verified records for the same owner and ID(D₀) carrying different
commitments are a conflicting-ownership signal rather than something to resolve
silently. The verdict then reports `conflict: true` and lists them all.

## Verifying against the certificate instead of the CSV

The four claim-verifiers take the certificate through `WM_CERT_FILE`. Every
variable must be `WM_`-prefixed: `place_wm.sh` and `cts_wm.sh` forward the
environment into the OpenROAD child with `compgen -v | grep -E '^WM_'`, and
anything else is dropped.

| Variable | Description |
| ----- | ----- |
| `WM_CERT_FILE` | Path to `wm_cert.bin`. Its presence is the only switch. |
| `WM_CERT_MASTER_SEED_HEX` | The claimed key: hex, a `*.hex` file, or a `bundle.json`. |
| `WM_CERT_COMMIT_JSON` | `wm_commit.json`. Enables the Eq. 19 check. |
| `WM_CERT_REQUIRE` | Defaults to `1`. Never falls back to the plaintext CSV. |
| `WM_CERT_STAGE` | `placement` or `cts`. Optional cross-check. |
| `WM_CERT_FORCE_PURE_AES` | Test only. Ignore `cryptography`. |

With `WM_CERT_FILE` unset, every verifier behaves as before, byte for byte.
`tests/test_claims.py` asserts this directly, and `wm_cert` is not imported on
that path.

## Crypto backends

The certificate uses the AESGCM implementation from `cryptography` when it
imports, and the vendored [`../wm_aesgcm.py`](../wm_aesgcm.py) otherwise. The
fallback is required rather than optional: the claim-verifiers run inside
`openroad -python`, reached through `singularity exec -e` with `PYTHONPATH`
reset, where `cryptography` is not importable and the Python 3.12 standard
library has no AEAD. Both backends are pinned to the same NIST CAVP vectors and
cross-checked against each other when both are present.

## Tests

```bash
bash tests/run_tests.sh              # everything
bash tests/run_tests.sh test_cert    # one module
```

The suite uses only the standard library, so it needs no OpenROAD, no numpy and
no `cryptography`. Tests that depend on an optional package skip themselves.
Coverage includes:

- FIPS-197 and NIST GCM known-answer vectors for the vendored AES, and the
  table-driven GHASH checked against a bit-serial reference.
- A byte-exact comparison of the DER request against
  `openssl ts -query -no_nonce`.
- A frozen wire-format known-answer test pinning `cert_sha256`, `c`, `SHA256(R)`
  and the `.tsq` bytes, so a refactor cannot change the format silently.
- Length-prefix disambiguation: `("ab","c")` and `("a","bc")` must serialize
  differently.
- Backward compatibility: certificate-sourced rows against `csv.DictReader`
  rows, and the same verification result from both.

## Limitations

- The vendored AES-GCM path runs at roughly 0.19 MB/s on CPython 3.9, so a 17 KB
  certificate takes about 90 ms to seal and 90 ms to open. It is not intended for
  megabyte payloads.
- The vendored path is not constant-time. This is acceptable because the
  certificate is opened on the owner's machine with a key the owner already
  holds, but `cryptography` is preferred wherever it is available.
- Obtaining the RFC 3161 token is manual, because no script here makes network
  calls.

## License

BSD 3-Clause License. See the [LICENSE](../../../LICENSE_BUILD_RUN_SCRIPTS) file.
