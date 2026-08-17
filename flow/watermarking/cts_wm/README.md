# CTS Watermark

The CTS stage embeds ownership evidence in the sequential fanout parity of
selected leaf clock buffers (LCBs), on a post-TritonCTS ODB. The key comes from
[`../gen_key/`](../gen_key/) as `seed_cts.hex`.

```{note}
The OpenROAD `wmk` module implements the same stage in C++ as `cts_watermark`.
This directory is the flow's implementation and is what `make WATERMARK=1` runs;
the two are separate code paths for the same scheme.
```

## What the watermark is

For a keyed pair of neighbouring LCBs `(L_A, L_B)`, the seed fixes:

```
seq_fanout(target_lcb) % 2  ==  target_bit
```

The seed determines both `target_bit` and which of the two LCBs is the target.
Parity is adjusted by moving one boundary flip-flop from the target LCB to its
peer. No buffers are added or removed, and the clock tree keeps its shape. A
boundary flip-flop is one whose distance gap to the peer LCB is within
`WM_CTS_DELTA_SITES` site pitches, so the move is short.

Successful pairs are marked `setDoNotTouch` and `FIRM`, along with quasi-leaf
repair cells, so downstream routing does not undo the watermark.

Ownership evidence is the extraction rate `r_C` and the coincidence probability
`P_c = sum_{i<=x} C(X,i) 0.5^X`. Each pair is a fair coin under a wrong key.

## Channels

Every clock buffer is classified by what its output net drives.

| Sink kind | Definition |
| ----- | ----- |
| `seq` | A sequential clock pin, that is a flip-flop. |
| `repair` | A timing-repair cell, recognised by a basename hint (`rebuffer`, `wire`, `hold`, `max_cap`, `max_slew`, `fanout`, `load_slew`, `clkload`, `clk_load`) and not named like a CTS buffer. |
| `other` | Anything else. |

That classification yields two candidate channels.

| Channel | Definition |
| ----- | ----- |
| `pure` | `seq > 0`, `repair == 0`, `other == 0` |
| `quasi_leaf` | `seq > 1`, `1 <= repair <= R_max`, `other == 0` |
| neither | Not a candidate. |

Candidate pairs may be pure-pure, quasi-quasi, or the cross-channel
`pure_quasi`. All three use the same `seq_fanout % 2` bit. Quasi-leaf pairs
additionally enforce a capacitance margin, a tighter slew margin, setup and hold
bounds, and a repair-signature check. If the set of repair instances changes
during a trial, the move is reverted and recorded as `repair_changed`.

Pairs are filled in priority order pure, then quasi_leaf, then pure_quasi, until
`WM_CTS_NUM_PAIRS` successful embeds or the queue is exhausted. One pair yields
one successful embed, and both LCBs leave the pool afterwards.

## Embed algorithm

1.  Read the post-CTS ODB and classify every clock buffer.
2.  Build proximity pairs with centroid distance at most
    `WM_CTS_SIBLING_DIST_UM`, separately per channel pool.
3.  Filter on Liberty headroom: `max_fanout` slack, slew margin, and for
    quasi-leaf pairs the capacitance margin.
4.  Select pairs and target bits with domain-separated RNGs (`cts_pure`,
    `cts_quasi`, `cts_pure_quasi`), honouring `WM_CTS_CHANNEL_BUDGET`.
5.  For each pair, record it and continue when the parity is already correct.
    Otherwise move the closest boundary flip-flop from target to peer.
6.  Run incremental STA after each trial, covering slew, capacitance, skew
    growth and, for quasi-leaf pairs, setup and hold degradation. Reject and
    revert anything unsafe.
7.  Mark accepted structures do-not-touch, then write the watermarked ODB and
    the claim CSV.

## Commands

```{note}
Parameters are environment variables. Every one is also available as a
command-line flag on the underlying Python script.
```

[`cts_wm.sh`](cts_wm.sh) dispatches four operations. The argparse defaults in
[`cts_watermark_embed.py`](cts_watermark_embed.py) are the single source of
truth; the wrapper sets none of them.

### Embed

The `embed` command selects the LCB pairs, moves the boundary flip-flops, and
writes the watermarked ODB together with the claim CSV. Run it after clock tree
synthesis.

```bash
./cts_wm.sh embed
```

#### Required

| Variable | Description |
| ----- | ----- |
| `WM_CTS_INPUT` | Post-CTS `.odb`. |
| `WM_CTS_OUTPUT_ODB` | Watermarked `.odb` to write. |
| `WM_CTS_OUTPUT_CSV` | Claim CSV, the verification commitment. |
| `WM_SEED_HEX` | `seed_cts.hex`. |

#### Selection

| Variable | Description | Default |
| ----- | ----- | ----- |
| `WM_CTS_NUM_PAIRS` | Target number of successful embeds. | 32 |
| `WM_CTS_SIBLING_DIST_UM` | Maximum centroid distance for a pair, in microns. | 20 |
| `WM_CTS_DELTA_SITES` | Boundary flip-flop threshold, in site pitches. | 2 |
| `WM_CTS_MAX_ATTEMPTS` | Boundary flip-flop trials per pair. | 3 |
| `WM_CTS_CHANNEL_BUDGET` | `auto`, `pure_only`, `quasi_only` or `N:M`. | auto |
| `WM_CTS_R_MAX` | Quasi-leaf maximum `repair_fanout`. | 2 |

#### Safety margins, pure channel

| Variable | Description | Default |
| ----- | ----- | ----- |
| `WM_CTS_FANOUT_MARGIN` | Minimum slack against Liberty `max_fanout`. | 2 |
| `WM_CTS_SLEW_HEADROOM_FRAC` | Minimum output slew margin. | 0.20 |
| `WM_CTS_SKEW_SLACK_PS` | Maximum growth of worst \|clock skew\| against the baseline. | 20 |

#### Safety margins, quasi-leaf channel

| Variable | Description | Default |
| ----- | ----- | ----- |
| `WM_CTS_QL_SLEW_HEADROOM_FRAC` | Slew margin. | `max(0.30, pure + 0.10)` |
| `WM_CTS_QL_CAP_HEADROOM_FRAC` | Capacitance margin. | 0.20 |
| `WM_CTS_QL_SETUP_SLACK_PS` | Setup WNS degradation limit. | 50 |
| `WM_CTS_QL_HOLD_SLACK_PS` | Hold WNS degradation limit. | 30 |
| `WM_CTS_QL_SKEW_SLACK_PS` | Skew slack against the baseline. | `WM_CTS_SKEW_SLACK_PS` |
| `WM_CTS_AVOID_HOLD_REPAIR` | Set to `1` to skip quasi-leaf pairs with a `hold` repair hint. | 1 |

#### Liberty fallbacks

These apply only when the value cannot be read from the library.

| Variable | Description | Default |
| ----- | ----- | ----- |
| `WM_CTS_MAX_FANOUT` | Fallback `max_fanout`. | 32 |
| `WM_CTS_MAX_TRANSITION_NS` | Fallback `max_transition`, in ns. | 0.4 |
| `WM_CTS_MAX_CAP_FF` | Fallback `max_capacitance`, in fF. | 50 |
| `WM_LIB_FILES`, `WM_SDC`, `WM_SETRC` | STA inputs. | auto-discovered |

### Verify

The `verify` command checks one ODB against the claim CSV, or against a
certificate. It exits `0` when every pair matches and `2` otherwise.

```bash
./cts_wm.sh verify
```

#### Options

| Variable | Description |
| ----- | ----- |
| `WM_CTS_VERIFY_INPUT` | `.odb` to check. |
| `WM_CELL_LIST` | Claim CSV, the ground truth. |
| `WM_CTS_VERIFY_CSV` | Optional per-pair report. |

### Verify Stages

The `verify_stages` command checks the same claims against a list of stage ODBs,
which is how survival through routing and finishing is measured.

```bash
./cts_wm.sh verify_stages
```

#### Options

| Variable | Description |
| ----- | ----- |
| `WM_VERIFY_STAGES` | `label:odb,label:odb,…`. |
| `WM_STAGE_REPORT` | Optional per-pair and per-stage CSV. |

### All

The `all` command runs `embed` and then `verify`.

```bash
./cts_wm.sh all
```

## Usage

The stage runs as part of the watermarked flow:

```bash
make DESIGN_CONFIG=./designs/nangate45/jpeg/config.mk WATERMARK=1
```

`flow/watermark.mk` inserts it between `4_1_cts` and `4_cts`, writing
`4_2_cts_wm.odb`. To drive the embedder directly:

```bash
WM_SEED_HEX=../gen_key/out/jpeg/seed_cts.hex \
WM_CTS_INPUT=.../4_1_cts.odb \
WM_CTS_OUTPUT_ODB=.../4_2_cts_wm.odb \
WM_CTS_OUTPUT_CSV=.../wm_cts_pairs_embed.csv \
  ./cts_wm.sh embed
```

## Files

| File | Description |
| ----- | ----- |
| [`cts_wm.sh`](cts_wm.sh) | Wrapper for `embed`, `verify`, `verify_stages` and `all`. |
| [`cts_watermark_common.py`](cts_watermark_common.py) | Classification, pairing, and Liberty and timing helpers. |
| [`cts_watermark_embed.py`](cts_watermark_embed.py) | Channel embed, filters, legality checks, CSV and ODB. |
| [`cts_watermark_verify.py`](cts_watermark_verify.py) | Verify one ODB against the claim CSV or a certificate. |
| [`cts_watermark_verify_stages.py`](cts_watermark_verify_stages.py) | Verify across a `label:odb` stage list. |

The keyed primitives live in [`../wm_prf.py`](../wm_prf.py), shared with the
placement and routing stages.

## Verification

The claim CSV carries `channel`, `target_lcb`, `target_bit`, `final_bit`, the
seq and repair fanout counts, and `skipped_reason`. Verification recomputes
`seq_fanout(target_lcb) % 2` on the ODB under test and compares it to
`target_bit`. Quasi-leaf pairs additionally fail when the repair signature has
been tampered with.

A CTS re-run renames buffers, so the claim CSV rather than the ODB is the
durable ground truth on later stages. Keep it for sign-off, or seal it into a
certificate with [`../certificate/`](../certificate/) and set `WM_CERT_FILE` to
verify against that instead.

## Limitations

-   The claim CSV is required for verification. Unlike the routing stage, this
    one is not key-recoverable.
-   GRT and DRT generally preserve clock sink wiring, and the do-not-touch marks
    on watermarked LCBs and their repair cells reduce disruption further. Use
    `verify_stages` to confirm this on a given design rather than assuming it.
-   Capacity is a property of the design. A shallow clock tree may offer too few
    leaf buffers to pair.
-   Timing must be set up. Without liberty and constraints the safety margins
    cannot be evaluated, and the stage has no basis on which to reject a move.

## References

1.  A. B. Kahng and Y. Liu. Kerckhoffs-Compliant Watermarking for Physical
    Design IP Protection: From Placement to Routing. arXiv preprint
    arXiv:2608.05055. [(arXiv)](https://arxiv.org/pdf/2608.05055)

## License

BSD 3-Clause License. See the [LICENSE](../../../LICENSE_BUILD_RUN_SCRIPTS)
file.
