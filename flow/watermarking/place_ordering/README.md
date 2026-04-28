# Pairwise / small-group ordering placement watermark

External Python tools that embed and verify **relative x-order** constraints on a
**post-detailed-placement** OpenDB (`.odb`), without modifying OpenROAD. The
secret key material comes from [`../gen_key/`](../gen_key/)
(`seed_placement.hex`).

## Watermark definition

### Pairs

For two instances `A`, `B` on the same row within distance `D_pair`:

- Encoded bit: `0` if `x(A) < x(B)`, else `1` if `x(B) < x(A)` (tie broken by tool as `bit=1`).
- Target bit: `HMAC(seed, "bit", tile_id, sort(name_A,name_B))[0] & 1`.

### Triples (optional)

For three instances in the same bucket (same tile/row/width/criticality bin)
with small span along x, six permutations of left-to-right order are possible.
A target permutation index `0..5` is drawn from `HMAC(..., "perm", ...)%6`.
Only triples whose six local-HPWL permutations have spread `≤ WM_HPWL_EPS_GROUP_DBU`
are candidates (“nearly free” permutations).

## Strategy (efficiency)

- **Bucketed enumeration:** cells are grouped by `(tile, row, master width,
  criticality bin)` and sorted by `x`. Pairs use **bounded K-neighbor**
  enumeration (`WM_PAIR_NEIGHBOR_K`, default `2`): only `(i, i+1)` and
  `(i, i+2)` per bucket, instead of the full window. Set to `0` for the
  legacy O(n²) sliding window.
- **Cheap-filter cascade:** rejected stages save the next stage's work. Order:
  `bucket -> distance -> neighbor-slack -> fanout-diff -> dense-tile gate ->
  HPWL-cache delta` (HPWL is last and most expensive).
- **Per-tile early stop:** after `WM_PAIRS_PER_TILE * WM_TILE_OVERSAMPLE`
  candidates accept in a tile, remaining buckets in that tile are skipped.
- **Capacity fallback:** if strict selection produces fewer than
  `WM_MIN_PAIRS_TOTAL` pairs, a second pass widens K-neighbor enumeration,
  uses coarser criticality bins, and modestly relaxes pair HPWL eps. This
  preserves the low-risk strict pass while avoiding too few watermark bits on
  timing-stressed designs.
- **Deduped HPWL cache:** positions, incident nets, and pin centers are
  cached once over kept cells. `swap_delta_hpwl` becomes pure Python
  arithmetic with no OpenDB walks per candidate. Nets above
  `WM_HPWL_NET_FANOUT_MAX` (default 64, typically global/control) are
  ignored as not informative for local swap quality. Cache freed before DPL.
- **One STA pass** for slack filtering + criticality bins; optional `WM_LIB_FILES` + `WM_SDC`.
- **Neighbor-slack guard:** a cell is rejected if any of its net-mate cells
  has slack below `WM_SLACK_THRESHOLD_NS + WM_NEIGHBOR_SLACK_MARGIN_NS`.
- **Tile quotas + touch cap:** `WM_PAIRS_PER_TILE`, `WM_GROUPS_PER_TILE`;
  keys `HMAC(seed,…)` assign a total order on candidates for greedy
  **non-overlapping** selection. `WM_TILE_TOUCH_FRAC_MAX` caps the fraction
  of cells perturbed in any single tile.
- **Post-embed STA guard:** after applying the whole batch, a single STA
  pass identifies swaps that drop slack below
  `WM_SLACK_THRESHOLD_NS - WM_GUARD_DEGRADE_NS` (or by more than
  `WM_GUARD_DEGRADE_NS`). Bad swaps are reverted in one rollback batch and
  marked `reverted_post_guard` in the CSV. An optional final STA check
  (`WM_POST_GUARD_FINAL_CHECK`) confirms the result.
- **Incremental DPL** once at the end with bounded `WM_MAX_DISP_X/Y`
  (default 5 µm) so legalization perturbs cells only locally.

## Files

| File | Role |
|------|------|
| `watermark_common.py` | PRF, tile grid, slack, macro/obstruction index, HPWL delta, enumeration, balance tracker |
| `watermark_embed.py` | embed: select, swap/permute, DPL, ODB/DEF/CSV |
| `watermark_verify.py` | verify suspect ODB against CSV |
| `watermark_verify_stages.py` | verify across `label:odb` stage list + optional PPA CSV |
| `place_wm.sh` | Singularity wrapper: `embed` / `verify` / `verify_stages` / `all` |
| `run_place_wm.sh` | Example: gen_key + embed + verify |
| `run_ppa.sh` | ORFS `wm_cts_and_route` with `DP_ODB` = watermarked placement |
| `run_verify_stages.sh` | Default stage paths for PPA outputs |

## Quick start

```bash
cd .../watermarking/place_ordering
chmod +x *.sh
./run_place_wm.sh        # produces 3_place_order_wm.odb + wm_place_order_embed.csv
./run_ppa.sh             # optional: flow continues from watermarked placement
./run_verify_stages.sh   # optional: check ordering at CTS/GRT/route/final ODBs
```

## Environment (embed)

| Var | Meaning | Default |
|-----|---------|---------|
| `WM_INPUT` | Post-DP `.odb` | required |
| `WM_OUTPUT_ODB` | Watermarked `.odb` | required |
| `WM_OUTPUT_DEF` | Optional DEF | unset |
| `WM_OUTPUT_CELL_LIST` | Embed CSV | unset |
| `WM_SEED_HEX` | `seed_placement.hex` | required |
| `WM_GRID_NX`, `WM_GRID_NY` | Tile grid | 8 × 8 |
| `WM_PAIR_DIST_UM` | Max horizontal separation | 1 |
| `WM_PAIRS_PER_TILE` | Pair selections per tile | 4 |
| `WM_GROUPS_PER_TILE` | Triple selections per tile | 2 |
| `WM_USE_GROUPS` | `1` = enable triples | 0 |
| `WM_HPWL_EPS_PAIR_DBU` | Max \|ΔHPWL\| for pair swap | 100 |
| `WM_HPWL_EPS_GROUP_DBU` | Max triple permutation spread | 100 |
| `WM_FANOUT_MAX` | Skip cells whose own fanout exceeds this | 16 |
| `WM_FANOUT_DIFF_MAX` | Skip pairs whose fanout differs by more (cheap pre-filter) | 4 |
| `WM_SLACK_THRESHOLD_NS` | Worst-pin slack lower bound | 0.20 |
| `WM_NEIGHBOR_SLACK_MARGIN_NS` | Extra slack required of net-neighbors | 0.10 |
| `WM_CRIT_BIN_NS` | Slack quantization for buckets | 0.05 |
| `WM_CRIT_BIN_RELAXED_NS` | Coarser criticality bin for capacity fallback | 0.20 |
| `WM_TILE_DENSITY_MAX` | Skip tiles with area density above this | 1.2 |
| `WM_TILE_DISP_CAP_UM` | Cap on cumulative \|dx\| bookkeeping per tile | 200 |
| `WM_TILE_TOUCH_FRAC_MAX` | Max fraction of cells perturbed per tile | 0.05 |
| `WM_BLOCKAGE_MARGIN_SITES` | Macro/obstruction keep-out (sites) | 4 |
| `WM_PAIR_NEIGHBOR_K` | Bounded K-neighbor enum (`0`=full window) | 2 |
| `WM_PAIR_NEIGHBOR_K_RELAXED` | Fallback K-neighbor enum | 8 |
| `WM_TILE_OVERSAMPLE` | Per-tile early-stop multiplier | 4 |
| `WM_HPWL_CACHE` | `1` = use deduped HPWL cache | 1 |
| `WM_HPWL_NET_FANOUT_MAX` | Skip very-high-fanout nets in HPWL cost | 64 |
| `WM_MIN_PAIRS_TOTAL` | Trigger capacity fallback below this selected-pair count | 64 |
| `WM_HPWL_EPS_PAIR_RELAXED_DBU` | Fallback max \|ΔHPWL\| for pair swap | 200 |
| `WM_POST_GUARD` | `1` = run STA after batch and revert bad swaps | 1 |
| `WM_POST_GUARD_FINAL_CHECK` | `1` = final STA after revert (diagnostics) | 1 |
| `WM_GUARD_DEGRADE_NS` | Slack-drop tolerance before reverting | 0.02 |
| `WM_LIB_FILES`, `WM_SDC` | STA inputs | auto / optional |
| `WM_MAX_DISP_X`, `WM_MAX_DISP_Y` | Incremental DPL (µm) | 5 |

## Verification

- **`watermark_verify.py`** reads **CSV ground truth**. Only rows with
  `skipped_reason` in `{ "", "already_satisfied" }` are checked. Skipped or
  reverted embed attempts (`balance_cap`, `hpwl_precheck`,
  `reverted_post_guard`, …) are ignored — the watermark commitment is the
  set of constraints the embed actually applied and kept after the post-guard.
- Re-derive from seed alone is **not** implemented (would require duplicating
  the full embed selection on the same netlist); always keep the embed CSV for
  sign-off.

## Security note

Order-based bits are invariant to **global translation** of the whole block
(both instances move together). Tampering that **reorders** chosen cells
relative to each other is detectable.

## Exit codes

- `watermark_verify.py`: `0` all checked constraints pass, `2` otherwise.
