# Watermarking

ORFS can embed keyed ownership evidence into a design while it is being
implemented, and later check whether a suspect layout carries that evidence.
The scheme is PDMarks, described in *"Kerckhoffs-Compliant Watermarking for
Physical Design IP Protection: From Placement to Routing"*.

Security rests on a secret key alone. Every watermarked object and every target
value is derived from a 32-byte master key by domain-separated HMAC-SHA256, so
the algorithms and these scripts can be public.

## Quick start

```bash
make DESIGN_CONFIG=./designs/nangate45/jpeg/config.mk WATERMARK=1
make DESIGN_CONFIG=./designs/nangate45/jpeg/config.mk WATERMARK=1 watermark_verify
```

The first command runs an ordinary flow with watermarks embedded at three
stages. The second checks them against the finished design.

## What gets embedded

| Stage | Carrier | Where |
| ----- | ----- | ----- |
| Placement | keyed x-order of same-row cell pairs | `3_5_place_dp.odb` &rarr; `3_6_place_wm.odb` |
| CTS | leaf-clock-buffer fanout parity | `4_1_cts.odb` &rarr; `4_2_cts_wm.odb` |
| Routing | keyed wrong-way routing bias | applied by the detailed router |

Placement and CTS insert an extra substep, so the blessed `3_place.odb` and
`4_cts.odb` come from the watermarked result. Routing needs no new step: a
keyed subset of nets is tagged with a `watermark` property, and the detailed
router charges those nets an inflated cost for wiring against a layer's
preferred direction.

## Commands

| Target | Description |
| ----- | ----- |
| `WATERMARK=1` | Run the flow with watermarks embedded. |
| `watermark_verify` | Check the placement and CTS watermarks against `6_final.odb`. |
| `watermark_certify` | Seal the accepted claims into an encrypted, timestamped certificate. |
| `watermark_keygen` | Generate the owner keypair and per-design stage seeds without running the flow. |

Ownership is decided by the extraction rate, the fraction of committed claims
that still hold, against a threshold (`WM_TAU`, default 0.75). An exact match is
not expected: routing and filling legitimately disturb a few marked objects. A
design whose watermark survives intact typically scores well above the
threshold, while an unmarked design scores near chance, around 0.5 for a binary
carrier.

Verification is a native OpenROAD command, so it needs no Python and works on
any design outside the flow:

```tcl
read_db suspect.odb
verify_watermark -placement_claims wm_place_order_embed.csv \
                 -cts_claims wm_cts_pairs_embed.csv -tau 0.75
```

The same is available under `openroad -python`:

```python
design.getWatermark().verifyPlacement("wm_place_order_embed.csv")
```

## Variables

| Variable | Description | Default |
| ----- | ----- | ----- |
| `WATERMARK` | Enable watermarking. | `0` |
| `WATERMARK_FRACTION` | Fraction of signal nets selected for the routing watermark. | `0.02` |
| `WATERMARK_STRENGTH` | Wrong-way cost multiplier for watermarked nets. `1` tags nets without biasing them, which is the control case. | `100.0` |
| `WATERMARK_P` | Quantile cutoff used when classifying a net as watermarked. | `0.4` |
| `WM_OWNER_ID` | Owner identity bound into the key bundle. | `pdmarks-owner` |

## Keys

Keys are generated on demand into `flow/watermarking/gen_key/`:

```
keys/sk.pem                       owner private key
out/<design>/seed_placement.hex   per-stage seeds derived from the signature
out/<design>/seed_cts.hex
out/<design>/seed_routing.hex
```

`sk.pem` is the secret. Anyone holding it can forge the watermark, and losing it
makes existing watermarks unverifiable, so keep it out of the results tree and
out of version control.

Key generation needs Ed25519 from the `cryptography` package. If the flow's
interpreter does not have it, point `GEN_KEY_PYTHON` at one that does.

## Evidence

Embedding writes its accepted claims next to the stage results:

```
wm_place_order_embed.csv    committed placement pairs and their target bits
wm_cts_pairs_embed.csv      committed clock-buffer pairs and their target parities
watermark_nets.txt    the tagged net list
```

These files are the verification commitment. `make clean_all` deletes them along
with the rest of the results, so archive them, or run `watermark_certify` to seal
them into `wm_cert.bin` plus a `wm_commit.json` that can be timestamped.

The routing stage is *key-recoverable*: a verifier holding the key and the
fraction can rebuild the tagged net set without `watermark_nets.txt`. Placement
and CTS are not; their CSVs (or a certificate) are required.

## Capacity

How much evidence a design can carry depends on how many eligible objects it
has. Small designs carry very little. `gcd` on NanGate45 yields a handful of
placement pairs and two clock-buffer pairs, and at default settings it yields
none at all, because a 0.2 ns timing gate against a 0.46 ns clock rejects
almost every cell. That is a property of the design, not a failure. Use a design
of realistic size before drawing conclusions about strength.

## Requirements

The routing stage needs an OpenROAD build providing the `wmk` module
(`set_routing_watermark` and related commands). Placement and CTS run on a
stock build. Check with:

```bash
openroad -no_init -exit <<< 'puts [info commands set_routing_watermark]'
```

ASAP7 produces no wrong-way wirelength, so the routing statistic is undefined
there; use NanGate45 for the routing stage.
