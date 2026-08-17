# Routing Watermark

The routing stage embeds ownership evidence in the routing-direction statistics
of a keyed subset of signal nets. It is not a per-object bit like the placement
and CTS stages. It is a population-level bias produced by the detailed router,
and it is verified with a statistical test rather than an exact match.

The key comes from [`../gen_key/`](../gen_key/) as `seed_routing.hex`.

This directory holds the flow hooks only. The algorithms are C++ in the
OpenROAD `wmk` module; see `src/wmk/README.md` in the OpenROAD tree.

## Requirements

The stage needs an OpenROAD build carrying the `wmk` module, which provides:

| Command | Description |
| ----- | ----- |
| `set_routing_watermark` | Tag the keyed subset of nets. |
| `set_routing_watermark_strength` | Set the wrong-way cost multiplier in DRT. |
| `report_routing_watermark` | Report the post-route statistic. |
| `clear_routing_watermark` | Drop every tag. |
| `verify_watermark` | Decide ownership from claims, or for routing from the key. |

```{warning}
These commands are not yet in upstream OpenROAD. They are proposed in
<https://github.com/ytliu8464/OpenROAD/tree/wmk-module>, which is what the
`tools/OpenROAD` submodule is expected to point at. Placement and CTS
watermarking do not need them and run on a stock OpenROAD build.
```

Confirm the commands are present before running this stage:

```bash
echo 'puts [info commands set_routing_watermark]' | "$OPENROAD_EXE" -no_init
# prints "set_routing_watermark" on a PDMarks build, an empty line on a stock one
```

## What the watermark is

**Selection.** Net `n` is watermarked if and only if

```
HMAC-SHA256(seed_routing, b"net\0" + n)[0:4]  /  2^32   <   f
```

read as a little-endian uint32, where `f` is `WATERMARK_FRACTION`. The C++ side
applies this rule, and `experiments/lib/keyless_verify.routing_wm_set` mirrors
it in Python.

**Carrier.** During detailed routing, tagged nets pay an increased penalty for
wiring against a layer's preferred direction, controlled by
`set_routing_watermark_strength`. Selected nets therefore use *less* wrong-way
routing than the rest of the design. The observable per-net statistic is the
wrong-way wirelength fraction:

```
q_R(n) = l_ww(n) / l_tot(n)
```

It is computed on canonicalized geometry, with overlapping collinear wire
intervals merged and vias excluded, so it does not depend on how the router
happened to split route records.

**Evidence.** The statistic is the difference in mean `q_R` between the
watermarked set and the rest of the eligible set:

```
T_R = mean_{n in WM_R} q_R(n)  -  mean_{n in E_R \ WM_R} q_R(n)
```

A more negative `T_R` means stronger evidence. Its significance comes from a
net-level randomization test. Draw `B` uniform k-subsets of `E_R`, where
`k = |WM_R|`, from a stream seeded by the public design identifier, and report:

```
p_R = (1 + #{ b : T_R^(b) <= T_R }) / (B + 1)
```

The null depends only on `k` and the fixed `q_R` vector, so wrong-key trials at
the same `k` reuse one null table. The smallest reportable `p_R` is `1/(B+1)`.
When the watermarked nets carry no wrong-way wirelength at all, the exact
combinatorial tail is reported instead of that floor.

The sign of `T_R` is not evidence on its own. On a design carrying no watermark
it is a coin flip, so half of all wrong keys would pass a sign test.

## Usage

The stage runs as part of the watermarked flow. Nothing here is invoked by hand.

```bash
make DESIGN_CONFIG=./designs/nangate45/jpeg/config.mk WATERMARK=1
```

`flow/watermark.mk` wires the two hooks below and generates the key bundle on
demand.

## Parameters

| Variable | Description | Default |
| ----- | ----- | ----- |
| `WATERMARK` | Enable the watermarked flow. | `0` |
| `WATERMARK_FRACTION` | `f`, the fraction of eligible signal nets selected. | `0.02` |
| `WATERMARK_STRENGTH` | `lambda_wm`, the wrong-way cost multiplier. | `100.0` |
| `WATERMARK_P` | Quantile cutoff for `report_routing_watermark`. | `0.4` |
| `WM_OWNER_ID` | Identity bound into the key bundle. | `pdmarks-owner` |

`PRE_DETAIL_ROUTE_TCL` is mandatory, not optional. ORFS runs each stage as a
separate process, and the net tags persist in the database while the strength
does not: it lives in the router's in-memory configuration and dies with the
global-route process. Wiring only `PRE_GLOBAL_ROUTE_TCL` routes every run at the
compiled-in default, which makes a strength sweep produce identical results. The
hook is idempotent, so sourcing it at both points is safe.

## Files

| File | Description |
| ----- | ----- |
| [`pre_route_watermark.tcl`](pre_route_watermark.tcl) | Tag the keyed nets, set the strength, and record the tagged list. Sourced before global route and again before detailed route. |
| [`post_route_watermark.tcl`](post_route_watermark.tcl) | Report the statistic, then clear the tags. |
| [`verify.tcl`](verify.tcl) | Check the placement and CTS claims on a finished design. Driven by the `watermark_verify` target. |

The keyed primitives live in [`../wm_prf.py`](../wm_prf.py), shared with the
placement and CTS stages.

## Why the tags are cleared

The `watermark` property names the marked nets in plaintext. Leaving it in the
database would hand that set to anyone the design is shipped to, which is enough
to reroute exactly those nets and strip the evidence without holding the key.
`post_route_watermark.tcl` therefore clears the tags once it has measured them,
before the stage writes `5_2_route.odb`.

Verification is unaffected. The routing stage is key-recoverable: it derives the
marked set from the key and never reads the tags back out of the design.

## Verification

Ownership verification is a native command and needs no flow:

```bash
make DESIGN_CONFIG=./designs/nangate45/jpeg/config.mk WATERMARK=1 watermark_verify
```

For the routing stage on a suspect layout, `verify_watermark` recovers the
marked set from the key alone:

```tcl
read_db suspect.odb
verify_watermark -routing_key_hex $seed_routing -routing_fraction 0.02
```

`watermark_nets.txt`, written beside the stage results, is a convenience record.
A verifier holding the key does not need it.

### Where `f` comes from

Key-only reconstruction needs `f`, so it must be recorded. It is recorded in two
places: `wm_route_params.json` in the results directory, and the watermark
certificate, which seals `f` and `lambda_wm` into its metadata. See
[`../certificate/`](../certificate/). `verify_ownership.py` reads `f` from the
certificate, so ownership verification needs no side channel.

## Limitations

-   ASAP7's strict-direction router produces no wrong-way wirelength, so `q_R` is
    identically zero and `T_R` and `p_R` are structurally undefined. The routing
    channel is skipped on ASAP7.
-   Watermark tags are not serialized to distributed workers, so the routing bias
    is not applied in distributed detailed routing.
-   `watermark_nets.txt` records the marked set in plaintext beside the stage
    results. Clearing the tags from the database does not remove it, so treat the
    results directory as private.

## References

1.  A. B. Kahng and Y. Liu. Kerckhoffs-Compliant Watermarking for Physical
    Design IP Protection: From Placement to Routing. arXiv preprint
    arXiv:2608.05055. [(arXiv)](https://arxiv.org/pdf/2608.05055)

## License

BSD 3-Clause License. See the [LICENSE](../../../LICENSE_BUILD_RUN_SCRIPTS)
file.
