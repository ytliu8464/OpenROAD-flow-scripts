# Placement Watermark

The placement stage embeds ownership evidence in the relative x-order of
same-row cell pairs, on a post-detailed-placement ODB. It is pure
OpenROAD-Python and needs no OpenROAD source changes. The key comes from
[`../gen_key/`](../gen_key/) as `seed_placement.hex`.

```{note}
The OpenROAD `wmk` module implements the same stage in C++ as
`place_watermark`. This directory is the flow's implementation and is what
`make WATERMARK=1` runs; the two are separate code paths for the same scheme.
```

## What the watermark is

**Pairs.** For two instances `A` and `B` in the same row within `D_pair` of each
other:

-   The observed bit is `0` if `x(A) < x(B)`, otherwise `1`. Ties resolve to `1`.
-   The target bit is `HMAC(seed, "bit", tile_id, sorted(name_A, name_B))[0] & 1`.

The embedder swaps the two cells when the observed bit differs from the target.
Both cells sit in the same row and exchange positions, so the perturbation is
local and area-neutral.

**Triples** are optional, enabled by `WM_USE_GROUPS=1`. Three cells in one
bucket admit six left-to-right orders, and the target index is
`HMAC(..., "perm", ...) % 6`. Only triples whose six permutations differ in
local HPWL by at most `WM_HPWL_EPS_GROUP_DBU` are eligible, so the reordering is
near-free.

Ownership evidence is the extraction rate `r_P` and the Bernoulli coincidence
probability `P_c = sum_{i<=x} C(X,i) 0.5^X` over `X` committed claims with `x`
mismatches.

## How candidates are chosen

Selection is deliberately conservative, because the watermark must not cost PPA.

1.  **Bucket** cells by tile, row, master width and criticality bin, then sort
    by x.
2.  **Enumerate** only `(i, i+1) … (i, i+K)` per bucket, where `K` is
    `WM_PAIR_NEIGHBOR_K` and defaults to 2, instead of a full O(n²) window. Set
    it to `0` to restore the full window.
3.  **Filter** cheapest first, so early rejects save later work: distance,
    neighbor slack, fanout difference, dense-tile gate, then HPWL delta.
4.  **Stop early** per tile once `WM_PAIRS_PER_TILE * WM_TILE_OVERSAMPLE`
    candidates are accepted in that tile.
5.  **Select** greedily in keyed order. `HMAC(seed, …)` totally orders the
    candidates; the embedder picks non-overlapping ones until the per-tile quota
    or the touch cap `WM_TILE_TOUCH_FRAC_MAX` is reached.
6.  **Fall back** when fewer than `WM_MIN_PAIRS_TOTAL` pairs survive. A second
    pass widens `K`, coarsens the criticality bins and relaxes the HPWL bound,
    then reselects from the merged pool. The strict pass is preserved.
7.  **Guard** with one post-embed STA pass over all swapped cells. Any swap that
    drops slack below `WM_SLACK_THRESHOLD_NS - WM_GUARD_DEGRADE_NS`, or by more
    than `WM_GUARD_DEGRADE_NS`, is reverted and marked `reverted_post_guard`.
8.  **Legalize** once at the end with incremental detailed placement, bounded by
    `WM_MAX_DISP_X` and `WM_MAX_DISP_Y`.

An HPWL cache built once over the kept cells makes `swap_delta_hpwl` pure Python
arithmetic, with no OpenDB walk per candidate. Nets with more than
`WM_HPWL_NET_FANOUT_MAX` pins are skipped as uninformative for local swap
quality. The cache is freed before detailed placement.

## Commands

```{note}
Parameters are environment variables. Every one is also available as a
command-line flag on the underlying Python script.
```

[`place_wm.sh`](place_wm.sh) dispatches four operations. The argparse defaults
in [`watermark_embed.py`](watermark_embed.py) are the single source of truth;
the wrapper sets none of them.

### Embed

The `embed` command selects the pairs, applies the swaps, legalizes, and writes
the watermarked ODB together with the claim CSV. Run it after detailed
placement.

```bash
./place_wm.sh embed
```

#### Required

| Variable | Description |
| ----- | ----- |
| `WM_INPUT` | Post-detailed-placement `.odb`. |
| `WM_OUTPUT_ODB` | Watermarked `.odb` to write. |
| `WM_SEED_HEX` | `seed_placement.hex`. |

#### Outputs

| Variable | Description | Default |
| ----- | ----- | ----- |
| `WM_OUTPUT_CELL_LIST` | Claim CSV, the verification commitment. | unset |
| `WM_OUTPUT_DEF` | Optional DEF. | unset |

#### Selection

| Variable | Description | Default |
| ----- | ----- | ----- |
| `WM_GRID_NX`, `WM_GRID_NY` | Tile grid. | 8 × 8 |
| `WM_PAIR_DIST_UM` | Maximum horizontal separation for a pair. | 1 |
| `WM_PAIRS_PER_TILE` | Pair quota per tile. | 4 |
| `WM_GROUPS_PER_TILE` | Triple quota per tile. | 2 |
| `WM_USE_GROUPS` | Set to `1` to enable triples. | 0 |
| `WM_PAIR_NEIGHBOR_K` | Bounded K-neighbor enumeration. `0` selects the full window. | 2 |
| `WM_TILE_OVERSAMPLE` | Per-tile early-stop multiplier. | 4 |
| `WM_TILE_TOUCH_FRAC_MAX` | Maximum fraction of a tile's cells perturbed. | 0.05 |
| `WM_TILE_TOUCH_FLOOR_PAIRS` | Minimum pairs a tile may always take. | 4 |
| `WM_TILE_DENSITY_MAX` | Skip tiles denser than this. | 1.2 |
| `WM_TILE_DISP_CAP_UM` | Cumulative \|dx\| budget per tile. | 200 |
| `WM_BLOCKAGE_MARGIN_SITES` | Macro and obstruction keep-out. | 4 |

#### Quality gates

| Variable | Description | Default |
| ----- | ----- | ----- |
| `WM_HPWL_EPS_PAIR_DBU` | Maximum \|ΔHPWL\| for a pair swap. | 100 |
| `WM_HPWL_EPS_GROUP_DBU` | Maximum permutation spread for a triple. | 100 |
| `WM_FANOUT_MAX` | Skip cells whose own fanout exceeds this. | 16 |
| `WM_FANOUT_DIFF_MAX` | Skip pairs whose fanouts differ by more than this. | 4 |
| `WM_SLACK_THRESHOLD_NS` | Worst-pin slack lower bound. | 0.20 |
| `WM_NEIGHBOR_SLACK_MARGIN_NS` | Extra slack required of net-mates. | 0.10 |
| `WM_CRIT_BIN_NS` | Slack quantization for bucketing. | 0.05 |
| `WM_HPWL_CACHE` | Set to `1` to use the deduplicated HPWL cache. | 1 |
| `WM_HPWL_NET_FANOUT_MAX` | Ignore nets above this pin count. | 64 |

#### Capacity fallback

| Variable | Description | Default |
| ----- | ----- | ----- |
| `WM_MIN_PAIRS_TOTAL` | Trigger the fallback below this pair count. | 64 |
| `WM_PAIR_NEIGHBOR_K_RELAXED` | Fallback K. | 8 |
| `WM_HPWL_EPS_PAIR_RELAXED_DBU` | Fallback HPWL bound. | 200 |
| `WM_CRIT_BIN_RELAXED_NS` | Fallback criticality bin. | 0.20 |

#### Post-guard and legalization

| Variable | Description | Default |
| ----- | ----- | ----- |
| `WM_POST_GUARD` | Set to `1` to run STA after the batch and revert bad swaps. | 1 |
| `WM_POST_GUARD_FINAL_CHECK` | Set to `1` to re-run STA after reverting. | 1 |
| `WM_GUARD_DEGRADE_NS` | Slack-drop tolerance before reverting. | 0.02 |
| `WM_MAX_DISP_X`, `WM_MAX_DISP_Y` | Incremental detailed-placement bound, in microns. | 5 |
| `WM_LIB_FILES`, `WM_SDC` | STA inputs. | auto-discovered |

### Verify

The `verify` command checks one ODB against the claim CSV, or against a
certificate. It exits `0` when every checked constraint holds and `2` otherwise.

```bash
./place_wm.sh verify
```

#### Options

| Variable | Description |
| ----- | ----- |
| `WM_VERIFY_INPUT` | Watermarked or suspect `.odb`. |
| `WM_CELL_LIST` | Claim CSV, the ground truth. |
| `WM_VERIFY_CELL_LIST` | Optional summary CSV to write. |

### Verify Stages

The `verify_stages` command checks the same claims against a list of stage ODBs,
which is how survival through CTS, routing and finishing is measured.

```bash
./place_wm.sh verify_stages
```

#### Options

| Variable | Description |
| ----- | ----- |
| `WM_VERIFY_STAGES` | `label:odb,label:odb,…`. |
| `WM_STAGE_REPORT` | Optional per-stage CSV. |

### All

The `all` command runs `embed` and then `verify`.

```bash
./place_wm.sh all
```

## Usage

The stage runs as part of the watermarked flow:

```bash
make DESIGN_CONFIG=./designs/nangate45/jpeg/config.mk WATERMARK=1
```

`flow/watermark.mk` inserts it between `3_5_place_dp` and `3_place`, writing
`3_6_place_wm.odb`. To drive the embedder directly:

```bash
WM_SEED_HEX=../gen_key/out/jpeg/seed_placement.hex \
WM_INPUT=.../3_5_place_dp.odb \
WM_OUTPUT_ODB=.../3_6_place_wm.odb \
WM_OUTPUT_CELL_LIST=.../wm_place_order_embed.csv \
  ./place_wm.sh embed
```

## Files

| File | Description |
| ----- | ----- |
| [`place_wm.sh`](place_wm.sh) | Wrapper for `embed`, `verify`, `verify_stages` and `all`. |
| [`watermark_common.py`](watermark_common.py) | Tile grid, slack filtering, macro index, HPWL cache and enumeration. |
| [`watermark_embed.py`](watermark_embed.py) | Select, swap or permute, legalize, and write the ODB, DEF and CSV. |
| [`watermark_verify.py`](watermark_verify.py) | Verify one ODB against the claim CSV or a certificate. |
| [`watermark_verify_stages.py`](watermark_verify_stages.py) | Verify across a `label:odb` stage list. |

The keyed primitives live in [`../wm_prf.py`](../wm_prf.py), shared with the CTS
and routing stages.

## Verification

`watermark_verify.py` checks the ODB against the claim CSV rather than against
the seed alone. Only rows whose `skipped_reason` is empty or `already_satisfied`
are checked. Rows the embedder skipped or the post-guard reverted, such as
`balance_cap`, `hpwl_precheck` and `reverted_post_guard`, are excluded. The
commitment is the set of constraints the embed applied and kept.

Re-deriving the selection from the seed alone is deliberately not implemented,
because it would mean rerunning the full candidate enumeration against the same
netlist. Keep the claim CSV for sign-off, or seal it into a certificate with
[`../certificate/`](../certificate/) and set `WM_CERT_FILE` to verify against
that instead.

## Limitations

-   Order-based bits are invariant under global translation of the block,
    because both cells of a pair move together and the bit is unchanged.
    Tampering that reorders watermarked cells relative to each other is what the
    verifier detects.
-   The claim CSV is required for verification. Unlike the routing stage, this
    one is not key-recoverable.
-   Capacity is a property of the design. A sparse or timing-tight design may
    yield few pairs or none.

## References

1.  A. B. Kahng and Y. Liu. Kerckhoffs-Compliant Watermarking for Physical
    Design IP Protection: From Placement to Routing. arXiv preprint
    arXiv:2608.05055. [(arXiv)](https://arxiv.org/pdf/2608.05055)

## License

BSD 3-Clause License. See the [LICENSE](../../../LICENSE_BUILD_RUN_SCRIPTS)
file.
