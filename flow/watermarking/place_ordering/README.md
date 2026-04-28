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
  criticality bin)` and sorted by `x`. Pairs use a **sliding window** along x
  (not all-pairs `O(n²)`). Each cell touches only a handful of neighbors while
  `Δx ≤ D_pair`.
- **Local HPWL only:** swap / perm cost is the sum over **nets incident to
  moved cells** of `(hpwl_after − hpwl_before)` in DBU.
- **One STA pass** for slack filtering + criticality bins; optional `WM_LIB_FILES` + `WM_SDC`.
- **Tile quotas:** `WM_PAIRS_PER_TILE`, `WM_GROUPS_PER_TILE`; keys `HMAC(seed,…)`
  assign a total order on candidates for greedy **non-overlapping** selection.
- **Incremental DPL** once at the end with bounded `WM_MAX_DISP_X/Y`.

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
| `WM_PAIR_DIST_UM` | Max horizontal separation | 5 |
| `WM_PAIRS_PER_TILE` | Pair selections per tile | 4 |
| `WM_GROUPS_PER_TILE` | Triple selections per tile | 2 |
| `WM_USE_GROUPS` | `1` = enable triples | 1 |
| `WM_HPWL_EPS_PAIR_DBU` | Max \|ΔHPWL\| for pair swap | 500 |
| `WM_HPWL_EPS_GROUP_DBU` | Max triple permutation spread | 500 |
| `WM_FANOUT_MAX` | Skip high-fanout cells | 16 |
| `WM_SLACK_THRESHOLD_NS` | Worst-pin slack lower bound | 0.05 |
| `WM_CRIT_BIN_NS` | Slack quantization for buckets | 0.05 |
| `WM_TILE_DENSITY_MAX` | Skip tiles with area density above this | 1.2 |
| `WM_TILE_DISP_CAP_UM` | Cap on cumulative \|dx\| bookkeeping per tile | 200 |
| `WM_BLOCKAGE_MARGIN_SITES` | Macro/obstruction keep-out (sites) | 4 |
| `WM_LIB_FILES`, `WM_SDC` | STA inputs | auto / optional |
| `WM_MAX_DISP_X`, `WM_MAX_DISP_Y` | Incremental DPL (µm) | 50 |

## Verification

- **`watermark_verify.py`** reads **CSV ground truth**. Only rows with
  `skipped_reason` in `{ "", "already_satisfied" }` are checked (skipped embed
  attempts such as `balance_cap` are ignored).
- Re-derive from seed alone is **not** implemented (would require duplicating
  the full embed selection on the same netlist); always keep the embed CSV for
  sign-off.

## Security note

Order-based bits are invariant to **global translation** of the whole block
(both instances move together). Tampering that **reorders** chosen cells
relative to each other is detectable.

## Exit codes

- `watermark_verify.py`: `0` all checked constraints pass, `2` otherwise.
