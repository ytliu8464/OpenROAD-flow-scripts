# Key Generation

The key generation tool derives the per-stage watermark seeds from an Ed25519
signature. Each watermarking stage consumes exactly one `seed_<stage>.hex`.

The master seed comes from a signature over a binding message rather than from a
random number, so the watermark binds to an owner and a design. Showing
`(M, sig, pk)` proves the seed could only have been produced by the holder of
`sk`, without revealing the seed itself.

## Commands

```{note}
- Parameters in square brackets `[--param param]` are optional.
- Parameters without square brackets `--param2 param2` are required.
```

### Generate a Keypair

The `keygen` command generates `sk.pem` (mode `0600`) and `pk.pem`, and appends
`{owner_id, timestamp_utc, pk_fingerprint_sha256, pk_path}` to a local
`registry.json`. Run it once per owner.

```bash
./gen_key.sh keygen
    --owner-id owner_id
    [--out-dir dir]
    [--registry path]
    [--force]
```

#### Options

| Switch Name | Description |
| ----- | ----- |
| `--owner-id` | Owner identity recorded in the registry. |
| `--out-dir` | Directory for `sk.pem` and `pk.pem`. Defaults to `keys`. |
| `--registry` | Path to the local registry. Defaults to `registry.json`. |
| `--force` | Overwrite an existing keypair. |

### Sign and Derive

The `sign` command builds the binding message, signs it, and derives the master
seed and the three stage seeds. Run it once per design.

```bash
./gen_key.sh sign
    --design-id design_id
    --owner-id owner_id
    --pk pk.pem
    --sk sk.pem
    [--date YYYY-MM-DD]
    [--force]
    [--nonce hex]
    [--out-dir dir]
    [--tool-commit hash]
    [--tool-name name]
    [--tool-root dir]
```

#### Options

| Switch Name | Description |
| ----- | ----- |
| `--design-id` | Design identity recorded in the binding message. |
| `--owner-id` | Owner identity recorded in the binding message. |
| `--pk`, `--sk` | Key paths. |
| `--date` | Date recorded in `M`. Defaults to today in UTC. |
| `--force` | Overwrite an existing bundle. `sign` refuses to overwrite without it. |
| `--nonce` | Hex nonce. Defaults to 16 random bytes. |
| `--out-dir` | Output directory. Defaults to `out/<design_id>`. |
| `--tool-commit` | Commit recorded in `M`. Defaults to the git HEAD of `--tool-root`, otherwise `unknown`. |
| `--tool-name` | Tool recorded in `M`. Defaults to `OpenROAD`. |
| `--tool-root` | Checkout to read HEAD from. Defaults to `$OPENROAD_ROOT`. |

The binding message is:

```json
{
  "owner_id":  "...",
  "design_id": "...",
  "date":      "YYYY-MM-DD",
  "tool":      {"name": "OpenROAD", "commit": "<hash>"},
  "nonce":     "<hex>"
}
```

It is canonicalized with sorted keys and compact separators, a pragmatic
equivalent of RFC 8785. The derivation is:

```
master_seed    = SHA256(sig)                       # 32 B
seed_placement = SHA256(master_seed || "placement")
seed_cts       = SHA256(master_seed || "cts")
seed_routing   = SHA256(master_seed || "routing")
```

The command writes `M.json`, `sig.bin`, `pk.pem`, `bundle.json`,
`master_seed.hex` (mode `0600`) and the three `seed_*.hex` files to
`out/<design_id>/`.

### Verify a Bundle

The `verify` command re-canonicalizes `M.json`, verifies `sig.bin` against
`pk.pem`, recomputes the master seed and the three stage seeds, and compares
them to what is on disk. It is how an auditor checks a bundle without the
private key.

```bash
./gen_key.sh verify
    --bundle-dir dir
```

#### Options

| Switch Name | Description |
| ----- | ----- |
| `--bundle-dir` | Directory holding `M.json`, `sig.bin`, `pk.pem` and the seed files. |

## The master seed and the certificate

`master_seed` is also the key `K` used by [`../certificate/`](../certificate/).
The per-stage seeds and the certificate key
`K_Γ = HMAC-SHA256(K, ID(D₀), ν, "cert")` are siblings derived from it by domain
separation. Point the certificate tools at `master_seed.hex`, or at
`bundle.json`, which carries the same value as `master_seed_hex` for bundles
generated before `master_seed.hex` existed.

`ν` and `ID(D₀)` are not part of `M`. They are fixed at certification time,
after embedding. Adding either to `M` would change the signature, hence
`master_seed`, hence every stage seed, which would invalidate every watermark
already embedded for that design. Their integrity comes instead from the AEAD
associated data, from the commitment `c`, and from the RFC 3161 timestamp.

## Consuming the seeds

Each stage runner points `WM_SEED_HEX` at the matching file.

```bash
export WM_SEED_HEX=out/jpeg/seed_placement.hex   # placement_wm/
export WM_SEED_HEX=out/jpeg/seed_cts.hex         # cts_wm/
export WM_SEED_HEX=out/jpeg/seed_routing.hex     # routing_wm/
```

[`wm_prf.load_seed_hex`](../wm_prf.py) reads the file into a raw 32-byte key and
rejects anything that is not exactly 32 bytes. A truncated seed would silently
produce a different and unverifiable watermark. The runner in each stage
directory generates the bundle on demand when it is missing, so `gen_key.sh` is
rarely called by hand.

## Installation

```bash
pip install -r ../requirements.txt      # needs cryptography>=42
```

PyNaCl is supported as a fallback backend when `cryptography` is unavailable.
`GEN_KEY_PYTHON` overrides the interpreter that `gen_key.sh` selects.

## Example

```bash
# 1. One-time: owner keypair and local registry entry.
./gen_key.sh keygen --owner-id alice --out-dir keys

# 2. Per design: sign and derive the three stage seeds.
./gen_key.sh sign \
    --sk keys/sk.pem --pk keys/pk.pem \
    --owner-id alice --design-id jpeg \
    --out-dir out/jpeg

# 3. Audit an existing bundle.
./gen_key.sh verify --bundle-dir out/jpeg
```

## Limitations

-   `registry.json` is a local stub. A production deployment publishes
    `(pk, owner_identity, timestamp)` to a tamper-evident registry so a verifier
    can anchor `pk_fingerprint_sha256` to an identity.
-   Ed25519 signatures are deterministic. Verification needs only what is stored
    in `M`, not the original nonce separately.
-   `sk.pem` is written `0600`. Do not commit it, and back it up offline.
-   `keys/`, `out/` and `registry.json` are gitignored. Once a design is taped
    out, freeze its seed files read-only. Regenerating them changes the
    watermark.

## References

1.  A. B. Kahng and Y. Liu. Kerckhoffs-Compliant Watermarking for Physical
    Design IP Protection: From Placement to Routing. arXiv preprint
    arXiv:2608.05055. [(arXiv)](https://arxiv.org/pdf/2608.05055)

## License

BSD 3-Clause License. See the [LICENSE](../../../LICENSE_BUILD_RUN_SCRIPTS)
file.
