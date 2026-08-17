# Watermark Certificate

The watermark commands write their claims as plaintext CSV. A claim file says
which objects were marked and what value each was driven to, which is what a
verifier needs, but on its own it proves nothing about *when* the owner chose
them. Anyone holding a marked layout could write a claim file after the fact.

The certificate closes that gap. It seals the accepted placement and CTS claims
under a key only the owner holds, reduces the sealed blob to a short commitment,
and has that commitment countersigned by an independent timestamping authority
before the layout is released. Three properties follow:

-   Confidentiality: the claims are readable only with the master seed, so
    publishing a certificate does not disclose which objects are marked
-   Integrity: the design identifier and the nonce are authenticated, so a
    certificate cannot be re-pointed at another design
-   Priority: the timestamp fixes the commitment in time, so an infringer
    cannot construct a competing claim afterwards

The sealing is `Γ = (Γ_P, Γ_C)`, the accepted claims, under paper Eqs. 17-20:

```
K_Γ = HMAC-SHA256(K, LP(ID(D₀)) ‖ LP(ν) ‖ LP("cert"))                  (Eq. 17)
C_Γ = AES-256-GCM-Enc_{K_Γ}(Γ;  aad = LP(ID(D₀))‖LP(ν),  nonce = ν)    (Eq. 18)
c   = SHA256(K ‖ ID(D₀) ‖ ν ‖ SHA256(C_Γ))                             (Eq. 19)
R   = (ID(D₀), ν, SHA256(C_Γ), c)   →  RFC 3161 timestamp
```

`LP(x)` prefixes `x` with its big-endian uint32 length, the framing
[`wm_prf.hmac_digest`](../wm_prf.py) already uses. Eq. 19's four fields are
fixed-length at 32, 32, 12 and 32 bytes, so its plain concatenation is
unambiguous and is written exactly as the paper states.

`K` is the 32-byte master seed from [`../gen_key/`](../gen_key/), that is
`SHA-256(Ed25519_sig(sk, M))`. Eq. 2 feeds the same value into the per-stage
KDF, so the certificate key and the stage keys are siblings under one secret,
separated by domain.

`ν` and `ID(D₀)` are fixed at certification time, after embedding, and are
deliberately not part of the signed binding message `M`. Adding a field to `M`
would change the signature, hence the master seed, hence every stage seed, which
would invalidate every watermark already embedded. Their integrity comes from
the AEAD associated data, from `c`, and from the timestamp.

## Commands

```{note}
- Parameters in square brackets `[--param param]` are optional.
- Parameters without square brackets `--param2 param2` are required.
```

All five subcommands are reached through [`cert.sh`](cert.sh), which selects a
Python interpreter and forwards the arguments.

### Certify

The `certify` command reads the embed CSVs, seals the accepted claims, and
writes the commitment record. Run it after embedding, before the layout is
released.

It also records the watermark configuration. The effective parameters are
derived per design from the reference run's timing class, and nothing else on
disk keeps the result. In particular the routing fraction `f` was only echoed
into a log, which left Eq. 20's step 4 -- derive `K_R` and rebuild `WM_R` --
not actually executable from the key alone. Sealing the configuration into the
certificate is what makes it executable, so `certify` must run inside the
driver, after the adaptive parameters have been applied.

Certification runs automatically in the all-stage driver. The single-stage
runners take `PDMARKS_CERTIFY=1` to opt in.

```bash
cert.sh certify
    [--results-dir dir]
    [--out-dir dir]
    [--stages stages]
    [--master-seed-hex src]
    [--nonce-hex hex]
    [--tsr file]
    [--no-tsq]
    [--force]
```

#### Options

| Switch Name | Description |
| ----- | ----- |
| `--results-dir` | Where to read the embed CSVs. Defaults to `WM_RESULTS`. |
| `--out-dir` | Where to write the certificate. Defaults to `--results-dir`. |
| `--stages` | Comma-separated subset of `placement,cts,routing`. Defaults to all three. |
| `--master-seed-hex` | The key: hex, a `*.hex` file, or a `bundle.json`. |
| `--nonce-hex` | Use this nonce instead of drawing one. |
| `--tsr` | Record a timestamp token already obtained for this commitment. |
| `--no-tsq` | Skip the timestamp request. The commitment is then unattested. |
| `--force` | Overwrite an existing certificate. |

`DESIGN`, `PLATFORM`, `WM_FLOW_VARIANT` and `WM_RESULTS` are read from the
environment, so inside a driver the command usually needs no arguments at all.

#### Outputs

| File | Description |
| ----- | ----- |
| `wm_cert.bin` | A 60-byte header followed by `C_Γ`. |
| `wm_commit.json` | ID(D₀), ν, SHA256(C_Γ), `c` and SHA256(R). Public. |
| `wm_commit.tsq` | DER RFC 3161 request over SHA256(R). |
| `wm_id_d0.json` | The ID(D₀) preimage, for audit. Public. |
| `wm_cert_manifest.json` | What was sealed, and which CSVs it came from. |

The plaintext embed CSVs are read and left in place. The analysis harness, the
attack campaigns and the wrong-key sweep all still read them directly.

`C_Γ` is exactly bytes 60 to EOF, so its digest is reproducible without a
parser:

```bash
tail -c +61 wm_cert.bin | sha256sum        # == cert_sha256 in wm_commit.json
```

### Show Certificate

The `show` command opens a certificate and prints what it contains. It is the
quickest check that a key matches a certificate.

```bash
cert.sh show
    --cert file
    --master-seed-hex src
```

#### Options

| Switch Name | Description |
| ----- | ----- |
| `--cert` | Path to `wm_cert.bin`. |
| `--master-seed-hex` | The key: hex, a `*.hex` file, or a `bundle.json`. |

### Request Timestamp

The `stamp-request` command regenerates the DER request from `wm_commit.json`
alone, so the timestamped preimage is reproducible from the public record.

```bash
cert.sh stamp-request
    --commit file
    [--out file]
```

#### Options

| Switch Name | Description |
| ----- | ----- |
| `--commit` | Path to `wm_commit.json`. |
| `--out` | Where to write the `.tsq`. Defaults to beside the commitment. |

A `.tsq` with no `.tsr` proves nothing. Until an authority has signed it,
`wm_commit.json` is an unattested local claim, and there is no evidence the key
was chosen before the layout was released. No script here makes a network call,
so obtaining the token is the one manual step:

```bash
curl -s -H 'Content-Type: application/timestamp-query' \
     --data-binary @<results>/wm_commit.tsq \
     https://freetsa.org/tsr > <results>/wm_commit.tsr

# one-time: the authority's trust anchors
curl -s https://freetsa.org/files/cacert.pem > <results>/freetsa_cacert.pem
```

Record the token against the certificate with `certify --tsr <path>`, or pass
it to `verify` with `--tsr`.

### Verify Timestamp

The `stamp-verify` command checks a token against its request and the
authority's trust anchors.

```bash
cert.sh stamp-verify
    --tsr file
    [--tsq file]
    [--tsa-cafile file]
```

#### Options

| Switch Name | Description |
| ----- | ----- |
| `--tsr` | The token to check. |
| `--tsq` | The request it should answer. |
| `--tsa-cafile` | The authority's trust anchors. |

### Verify Ownership

The `verify` command runs the admissibility checks and then the per-stage
evidence checks against a suspect layout. It is the full Eq. 20 sequence.

```bash
cert.sh verify
    --cert file
    --master-seed-hex src
    [--commit file]
    [--tsr file]
    [--tsa-cafile file]
    [--suspect-odb odb]
    [--routing-f f]
    [--skip-placement]
    [--skip-cts]
    [--skip-routing]
    [--recheck-id]
    [--out-json file]
```

#### Options

| Switch Name | Description |
| ----- | ----- |
| `--cert` | Path to `wm_cert.bin`. |
| `--master-seed-hex` | The claimed key: hex, a `*.hex` file, or a `bundle.json`. |
| `--commit` | A commitment record. Repeatable, paired positionally with `--tsr`. |
| `--tsr` | The timestamp token for the preceding `--commit`. |
| `--tsa-cafile` | The authority's trust anchors. |
| `--suspect-odb` | The layout to check. |
| `--routing-f` | Override the routing fraction sealed in the certificate. |
| `--skip-placement`, `--skip-cts`, `--skip-routing` | Leave a stage out. |
| `--recheck-id` | Recompute ID(D₀) as an owner-side integrity check. |
| `--out-json` | Write the verdict here. |

The cheap checks come first, so a wrong key costs nothing:

0.  `SHA256(C_Γ)` matches a commitment record, otherwise `cert_hash_mismatch`
1.  Eq. 19, otherwise `commitment_mismatch`
2.  Eqs. 17 and 18, authenticate and decrypt, otherwise `aead_auth_failed`
3.  Evaluate `Γ_P` and `Γ_C` against the suspect layout
4.  Derive `K̂_R`, rebuild `WM_R` with `f` from the certificate, run the statistic
5.  Accept if at least two available stages pass, per Eq. 16

#### Exit codes

| Exit | Description |
| ----- | ----- |
| 0 | Admissible and accepted. |
| 2 | Admissible, not accepted. |
| 3 | Inadmissible. Eq. 19 or Eq. 18 failed. |
| 1 | Operational error. |

Exit 3 is a different outcome from "the watermark is absent" and should not be
conflated with it.

The extraction rate `r_s` divides by `|Γ_s|`, not by the number of claims the
verifier managed to look at. A certified object that is missing, or that cannot
be located unambiguously, counts as a mismatch; dividing by the checked count
would instead drop such claims from both the numerator and the denominator, and
a layout that had deleted the marked cells would verify perfectly.

Verification does not recompute ID(D₀). It enters only the KDF, the associated
data and the commitment, all read from the certificate header, so an owner who
no longer has `D₀` can still verify.

Thresholds are `τ_P = τ_C = 0.75` and `α_R = 1e-4`, from
[`../experiments/lib/thresholds.py`](../experiments/lib/thresholds.py).
`experiments/wrong_key/run_wrong_key.py` uses `α_R = 0.05` for its
null-distribution sweep; that is a different quantity and the two should not be
unified.

When `--commit` is given more than once, records are grouped by owner and
ID(D₀), and the earliest record whose token verifies and whose `cert_sha256`
names the certificate under test wins. Every rejected record is listed in the
verdict with its `genTime` and a reason. Two verified records for the same owner
and ID(D₀) carrying different commitments are a conflicting-ownership signal
rather than something to resolve silently: the verdict then reports
`conflict: true` and lists them all.

## Verifying from the certificate instead of the claim files

The four claim-verifiers take a certificate through `WM_CERT_FILE`. Every
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

## Files

| File | Description |
| ----- | ----- |
| [`cert.sh`](cert.sh) | Wrapper for all five subcommands. |
| [`certify.py`](certify.py) | Build `Γ`, seal it, commit, and emit the `.tsq`. |
| [`verify_ownership.py`](verify_ownership.py) | The full Eq. 20 sequence on a suspect layout. |
| [`tests/`](tests/) | Known-answer and equivalence tests. Standard library only. |

The library lives one level up so it is importable from inside
`openroad -python`: [`../wm_cert.py`](../wm_cert.py) for encoding, sealing,
opening, commitment and DER; [`../wm_aesgcm.py`](../wm_aesgcm.py) for the
vendored AES-GCM; and [`../wm_claims.py`](../wm_claims.py) for the claim loading
the verifiers share.

## Crypto backends

The certificate uses the AESGCM implementation from `cryptography` when it
imports, and the vendored [`../wm_aesgcm.py`](../wm_aesgcm.py) otherwise. The
fallback is required rather than optional: the claim-verifiers run inside
`openroad -python`, reached through `singularity exec -e` with `PYTHONPATH`
reset, where `cryptography` is not importable and the Python 3.12 standard
library has no AEAD. Both backends are pinned to the same NIST CAVP vectors and
cross-checked against each other when both are present.

## Regression tests

```bash
bash tests/run_tests.sh              # everything
bash tests/run_tests.sh test_cert    # one module
```

The suite uses only the standard library, so it needs no OpenROAD, no numpy and
no `cryptography`. Tests that depend on an optional package skip themselves.
Coverage includes FIPS-197 and NIST GCM known-answer vectors for the vendored
AES and a table-driven GHASH checked against a bit-serial reference; a
byte-exact comparison of the DER request against `openssl ts -query -no_nonce`;
a frozen wire-format test pinning `cert_sha256`, `c`, `SHA256(R)` and the `.tsq`
bytes, so a refactor cannot change the format silently; length-prefix
disambiguation, where `("ab","c")` and `("a","bc")` must serialize differently;
and certificate-sourced rows compared against `csv.DictReader` rows for the same
verification result.

## Limitations

-   Obtaining the RFC 3161 token is manual, because no script here makes a
    network call. A certificate whose token was never fetched carries no
    priority claim.
-   The vendored AES-GCM path runs at roughly 0.19 MB/s on CPython 3.9, so a
    17 KB certificate takes about 90 ms to seal and 90 ms to open. It is not
    intended for megabyte payloads.
-   The vendored path is not constant-time. This is acceptable because the
    certificate is opened on the owner's machine with a key the owner already
    holds, but `cryptography` is preferred wherever it is available.
-   The certificate seals the placement and CTS claims only. Routing has no
    per-object claims to seal; it is re-derived from the key, so what the
    certificate contributes there is the sealed routing fraction.

## References

1.  A. B. Kahng and Y. Liu. Kerckhoffs-Compliant Watermarking for Physical
    Design IP Protection: From Placement to Routing. arXiv preprint
    arXiv:2608.05055. [(arXiv)](https://arxiv.org/pdf/2608.05055)

## License

BSD 3-Clause License. See the [LICENSE](../../../LICENSE_BUILD_RUN_SCRIPTS)
file.
