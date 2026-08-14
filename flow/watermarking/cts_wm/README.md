# CTS Watermark

Embeds ownership evidence in the sequential fanout parity of selected leaf clock
buffers (LCBs) on a post-TritonCTS ODB. The key comes from
[`../gen_key/`](../gen_key/) as `seed_cts.hex`.

## What the watermark is

For a keyed pair of neighbouring LCBs `(L_A, L_B)`, the seed fixes

```
seq_fanout(target_lcb) % 2  ==  target_bit
```

The seed determines both `target_bit` and which of the two LCBs is the target.
Parity is adjusted by moving one boundary flip-flop from the target LCB to its
peer. No buffers are added or removed and the clock tree keeps its shape. A
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
during a trial the move is reverted and recorded as `repair_changed`.

Pairs are filled in priority order pure, then quasi_leaf, then pure_quasi, until
`WM_CTS_NUM_PAIRS` successful embeds or the queue is exhausted. One pair yields
one successful embed, and both LCBs leave the pool afterwards.

## Embed algorithm

1. Read the post-CTS ODB and classify every clock buffer.
2. Build proximity pairs with centroid distance at most
   `WM_CTS_SIBLING_DIST_UM`, separately per channel pool.
3. Filter on Liberty headroom: `max_fanout` slack, slew margin, and for
   quasi-leaf pairs the capacitance margin.
4. Select pairs and target bits with domain-separated RNGs (`cts_pure`,
   `cts_quasi`, `cts_pure_quasi`), honouring `WM_CTS_CHANNEL_BUDGET`.
5. For each pair, record it and continue when the parity is already correct.
   Otherwise move the closest boundary flip-flop from target to peer.
6. Run incremental STA after each trial, covering slew, capacitance, skew growth
   and, for quasi-leaf pairs, setup and hold degradation. Reject and revert
   anything unsafe.
7. Mark accepted structures do-not-touch, then write the watermarked ODB and the
   ground-truth CSV.

## Files

| File | Description |
| ----- | ----- |
| `cts_watermark_common.py` | Classification, pairing, and Liberty and timing helpers. |
| `cts_watermark_embed.py` | Channel embed, filters, legality checks, CSV and ODB. |
| `cts_watermark_verify.py` | Verify one ODB against the embed CSV or a certificate. |
| `cts_watermark_verify_stages.py` | Verify across a `label:odb` stage list. |
| `cts_wm.sh` | Wrapper for `embed`, `verify`, `verify_stages` and `all`. |
| `run_cts_wm.sh` | End-to-end example: key bundle, embed, verify. |
| `run_ppa.sh` | Continue the ORFS back-end (GRT and DRT) from the marked ODB. |
| `run_verify_stages.sh` | Verify at post-CTS, GRT, DRT and final. |

The keyed primitives live in [`../wm_prf.py`](../wm_prf.py), shared with the
placement and routing stages.

## Commands

The `cts_wm.sh` script dispatches the four operations. `embed` writes the
watermarked ODB and the ground-truth CSV. `verify` checks one ODB.
`verify_stages` checks a list of stage ODBs. `all` runs `embed` then `verify`.

```bash
./cts_wm.sh embed | verify | verify_stages | all
```

## Example scripts

```bash
export DESIGN=jpeg PLATFORM=nangate45 WM_FLOW_VARIANT=base

./run_cts_wm.sh          # embed and self-verify
./run_ppa.sh             # optional: GRT and DRT from the watermarked CTS ODB
./run_verify_stages.sh   # optional: confirm survival at each later stage
```

Driving the embedder directly:

```bash
WM_SEED_HEX=../gen_key/out/jpeg/seed_cts.hex \
WM_CTS_INPUT=.../4_cts.odb \
WM_CTS_OUTPUT_ODB=.../4_cts_wm.odb \
WM_CTS_OUTPUT_CSV=.../wm_cts_pairs_embed.csv \
  ./cts_wm.sh embed
```

## Parameters

The argparse defaults in `cts_watermark_embed.py` are the single source of
truth. The wrapper scripts set none of them.

### Required

| Variable | Description |
| ----- | ----- |
| `WM_CTS_INPUT` | Post-CTS `.odb`. |
| `WM_CTS_OUTPUT_ODB` | Watermarked `.odb` to write. |
| `WM_CTS_OUTPUT_CSV` | Ground-truth CSV, the verification commitment. |
| `WM_SEED_HEX` | `seed_cts.hex`. |

### Selection

| Variable | Description | Default |
| ----- | ----- | ----- |
| `WM_CTS_NUM_PAIRS` | Target number of successful embeds. | 32 |
| `WM_CTS_SIBLING_DIST_UM` | Maximum centroid distance for a pair, in microns. | 20 |
| `WM_CTS_DELTA_SITES` | Boundary flip-flop threshold, in site pitches. | 2 |
| `WM_CTS_MAX_ATTEMPTS` | Boundary flip-flop trials per pair. | 3 |
| `WM_CTS_CHANNEL_BUDGET` | `auto`, `pure_only`, `quasi_only` or `N:M`. | auto |
| `WM_CTS_R_MAX` | Quasi-leaf maximum `repair_fanout`. | 2 |

### Safety margins, pure channel

| Variable | Description | Default |
| ----- | ----- | ----- |
| `WM_CTS_FANOUT_MARGIN` | Minimum slack against Liberty `max_fanout`. | 2 |
| `WM_CTS_SLEW_HEADROOM_FRAC` | Minimum output slew margin. | 0.20 |
| `WM_CTS_SKEW_SLACK_PS` | Maximum growth of worst \|clock skew\| against the baseline. | 20 |

### Safety margins, quasi-leaf channel

| Variable | Description | Default |
| ----- | ----- | ----- |
| `WM_CTS_QL_SLEW_HEADROOM_FRAC` | Slew margin. | `max(0.30, pure + 0.10)` |
| `WM_CTS_QL_CAP_HEADROOM_FRAC` | Capacitance margin. | 0.20 |
| `WM_CTS_QL_SETUP_SLACK_PS` | Setup WNS degradation limit. | 50 |
| `WM_CTS_QL_HOLD_SLACK_PS` | Hold WNS degradation limit. | 30 |
| `WM_CTS_QL_SKEW_SLACK_PS` | Skew slack against the baseline. | `WM_CTS_SKEW_SLACK_PS` |
| `WM_CTS_AVOID_HOLD_REPAIR` | Set to `1` to skip quasi-leaf pairs with a `hold` repair hint. | 1 |

### Liberty fallbacks

These apply only when the value cannot be read from the library.

| Variable | Description | Default |
| ----- | ----- | ----- |
| `WM_CTS_MAX_FANOUT` | Fallback `max_fanout`. | 32 |
| `WM_CTS_MAX_TRANSITION_NS` | Fallback `max_transition`, in ns. | 0.4 |
| `WM_CTS_MAX_CAP_FF` | Fallback `max_capacitance`, in fF. | 50 |
| `WM_LIB_FILES`, `WM_SDC`, `WM_SETRC` | STA inputs. | auto-discovered |

### Verify

| Variable | Description |
| ----- | ----- |
| `WM_CTS_VERIFY_INPUT` | `.odb` to check. |
| `WM_CELL_LIST` | Embed CSV, the ground truth. |
| `WM_CTS_VERIFY_CSV` | Optional per-pair report. |
| `WM_VERIFY_STAGES` | `verify_stages` only: `label:odb,label:odb,…`. |
| `WM_STAGE_REPORT` | Optional per-pair and per-stage CSV. |

## Verification

The embed CSV carries `channel`, `target_lcb`, `target_bit`, `final_bit`, the
seq and repair fanout counts, and `skipped_reason`. Verification recomputes
`seq_fanout(target_lcb) % 2` on the ODB under test and compares it to
`target_bit`. Quasi-leaf pairs additionally fail when the repair signature has
been tampered with.

A CTS re-run renames buffers, so the embed CSV rather than the ODB is the
durable ground truth on later stages. Keep it for sign-off, or seal it into a
certificate with [`../certificate/`](../certificate/) and set `WM_CERT_FILE` to
verify against that instead.

Exit codes: `0` when every pair matches, `2` otherwise.

## Limitations

GRT and DRT generally preserve clock sink wiring, and the do-not-touch marks on
watermarked LCBs and their repair cells reduce disruption further. Use
`run_verify_stages.sh` to confirm this on a given design.

## License

BSD 3-Clause License. See the [LICENSE](../../../LICENSE_BUILD_RUN_SCRIPTS) file.
