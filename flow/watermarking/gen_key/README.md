# Watermarking key / seed generation

Ed25519 signature-based derivation of per-stage watermarking seeds. Shared
by the placement / CTS / routing watermarking flows (each stage consumes
exactly one `seed_<stage>.hex`).

## Pipeline

1. **One-time owner setup** (`keygen`):
   - `Ed25519PrivateKey.generate()` -> `sk.pem` (mode 0600) + `pk.pem`.
   - Append `{owner_id, timestamp_utc, pk_fingerprint_sha256, pk_path}` to
     a local `registry.json` (stub for a real trusted registry).
2. **Per-design signing** (`sign`):
   - Build binding message
     ```json
     M = {
       "owner_id": "...",
       "design_id": "...",
       "date": "YYYY-MM-DD",
       "tool": {"name": "OpenROAD", "commit": "<hash>"},
       "nonce": "<hex>"
     }
     ```
   - Canonicalize with sorted keys + compact separators (pragmatic JCS).
   - `sig = Ed25519(sk).sign(canonical_M)` (64 bytes).
   - Derive:
     ```
     master_seed    = SHA256(sig)                     # 32B
     seed_placement = SHA256(master_seed || "placement")
     seed_cts       = SHA256(master_seed || "cts")
     seed_routing   = SHA256(master_seed || "routing")
     ```
   - Write to `out/<design_id>/`: `M.json`, `sig.bin`, `pk.pem`,
     `bundle.json`, `seed_{placement,cts,routing}.hex`.
3. **Verify** (`verify`):
   - Re-canonicalize `M.json`, verify `sig.bin` against `pk.pem`, recompute
     `master_seed` and per-stage seeds, compare against on-disk hex files
     and `bundle.json`.

## Prerequisites

```
pip install -r requirements.txt   # cryptography>=42
```

PyNaCl is supported as a fallback backend if `cryptography` is unavailable.

## Usage

```bash
cd .../watermarking/gen_key
chmod +x gen_key.sh

# 1) One-time: generate owner keypair + local registry entry.
./gen_key.sh keygen --owner-id alice --out-dir keys

# 2) Per-design: sign and derive seeds.
./gen_key.sh sign \
    --sk keys/sk.pem --pk keys/pk.pem \
    --owner-id alice --design-id aes \
    --tool-name OpenROAD \
    --out-dir out/aes

# 3) Audit.
./gen_key.sh verify --bundle-dir out/aes
```

## Consuming the seeds in downstream stages

Each watermarking stage's `run_*.sh` exports `WM_SEED_HEX` pointing at the
corresponding hex file:

```bash
# Placement (site-parity) watermark:
export WM_SEED_HEX=.../gen_key/out/aes/seed_placement.hex
./place_site_parity/run_place_wm.sh

# CTS watermark (future):
export WM_SEED_HEX=.../gen_key/out/aes/seed_cts.hex

# Routing watermark:
export WM_SEED_HEX=.../gen_key/out/aes/seed_routing.hex
```

The stage's `watermark_common.py` loads the hex file into a raw 32-byte
`bytes` object and uses it as the HMAC key.

## Threat model / notes

- The "registry" here is a local JSON stub. A real deployment publishes
  `(pk, owner_identity, timestamp)` to a tamper-evident registry so a
  verifier can anchor `pk_fingerprint_sha256` to an owner identity.
- Signatures are deterministic (Ed25519); verification does not need the
  original nonce beyond what is stored in `M`.
- `sk.pem` is written with mode `0600`. Never commit it; back it up
  offline.
- Seed files are short hex strings; keep them read-only once frozen for a
  given tapeout.
