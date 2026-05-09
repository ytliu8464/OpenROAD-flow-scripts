# Site-Parity placement watermarking (mod-5 along X)

External Python tools that embed and verify **site-parity** constraints on
a **post-detailed-placement** OpenDB (`.odb`), without modifying OpenROAD.
The secret material comes from the signature-based
[`../gen_key/`](../gen_key/) pipeline.

## Watermark definition

For each watermarked instance in row `r` with site width `sw`:

- `site_col(inst) = round((inst.xMin - row.xMin) / sw)`
- `residue(inst)  = site_col(inst) mod 5`

For each chosen cell a target residue in `{0,1,2,3,4}` is derived from
`HMAC-SHA256(seed_placement, tile_id || inst_name) mod 5`, where
`seed_placement` is the raw 32-byte output of `gen_key/`.

The watermark is satisfied when `residue(inst) == target_residue` after
incremental detailed placement. Mod-5 defeats "shift-whole-chip-by-one-
site" attacks because a single global shift by `s` sites maps every
residue uniformly, so it cannot hit five intended targets simultaneously.

## Pipeline

1. Load post-DP ODB.
2. Collect movable, single-row-height core cells.
3. Drop timing-critical cells (worst pin slack < `WM_SLACK_THRESHOLD_NS`,
   using `estimate_parasitics -placement` + OpenROAD `Timing` API; falls
   back to a heuristic that skips sequentials if STA fails).
4. Build `WM_GRID_NX x WM_GRID_NY` tile grid over the core bbox.
5. For each tile, sample `WM_K_PERCENT %` of cells with a per-tile RNG
   seeded by `HMAC(seed_placement, tile_id)`.
6. For each chosen cell: try **row-local swap** with a same-master cell
   whose current residue matches the target; otherwise **nudge** X by
   the signed mod-5 delta (in `{-2, -1, 0, 1, 2}` sites).
7. Run incremental `detailedPlacement(max_disp_x, max_disp_y)`.
8. Emit watermarked ODB / DEF and embed CSV. Report satisfied count and
   `Pc <= sum_{i=0..fail} C(n,i) p^{n-i} (1-p)^i` with `p = 1/5`.

## Files

| File | Role |
|------|------|
| `watermark_common.py` | HMAC / residue helpers, tile grid, STA slack filter, binomial Pc |
| `watermark_embed.py` | Select, enforce, incremental DPL, write ODB/DEF/CSV |
| `watermark_verify.py` | Verify a watermarked ODB (prefers embed CSV as ground truth) |
| `watermark_verify_stages.py` | Verify cells across post-CTS/GRT/DRT ODBs |
| `place_wm.sh` | Singularity wrapper (embed / verify / verify_stages / all) |
| `run_place_wm.sh` | End-to-end AES example: ensures seed exists, then embed + verify |
| `run_ppa.sh` | Drive CTS/GRT/DRT from the watermarked ODB |
| `run_verify_stages.sh` | Defaults for `verify_stages` pointing at PPA results |

## Prerequisites

- OpenROAD built with Python (`openroad -python`).
- Must run inside Singularity (`ispd26.sif`) — see `place_wm.sh`.
- `../gen_key/` must have a keypair (`keys/sk.pem`, `keys/pk.pem`) and a
  signed bundle for your design (`out/<design_id>/seed_placement.hex`).
  `run_place_wm.sh` performs both lazily if they are missing.

## Quick start (AES)

```bash
cd /home/fetzfs_projects/MISC-ytliu/watermarking/OR0415/OpenROAD-flow-scripts/flow/watermarking/place_site_parity
chmod +x *.sh
./run_place_wm.sh          # sign + embed + verify
./run_ppa.sh               # CTS/GRT/DRT starting from the watermarked .odb
./run_verify_stages.sh     # verify across post-CTS/GRT/DRT/final ODBs
```

## Environment variables

### Embed

| Var | Meaning | Default |
|-----|---------|---------|
| `WM_INPUT` | post-DP `.odb` | required |
| `WM_OUTPUT_ODB` | output watermarked `.odb` | required |
| `WM_OUTPUT_DEF` | optional output DEF | unset |
| `WM_OUTPUT_CELL_LIST` | optional embed CSV path | unset |
| `WM_SEED_HEX` | path to `seed_placement.hex` from `gen_key/` | required |
| `WM_GRID_NX`, `WM_GRID_NY` | tile grid dims | `8`, `8` |
| `WM_K_PERCENT` | percent of tile cells to watermark | `5` |
| `WM_SLACK_THRESHOLD_NS` | keep cells with worst slack >= this | `0.1` |
| `WM_SDC` | optional SDC path (read before STA) | unset |
| `WM_MAX_DISP_X`, `WM_MAX_DISP_Y` | incremental DPL budget (um) | `50`, `50` |
| `WM_MESSAGE` | human-readable tag (not used in selection) | unset |

### Verify

`WM_VERIFY_INPUT` plus either `WM_CELL_LIST` (preferred ground truth) or
`WM_SEED_HEX` + grid/k%/slack (to re-derive the pool). Optional
`WM_VERIFY_CELL_LIST` for an output CSV.

### Verify stages

`WM_CELL_LIST`, `WM_VERIFY_STAGES='label:odb,...'`, optional
`WM_STAGE_REPORT`, `WM_DBU_PER_MICRON`.

## Notes

- Start from **post-DP** placement (`3_place.odb` / `3_5_place_dp.odb`).
- Legalization can break a small fraction of residues on congested rows;
  the embed script prints pre-DPL violations, swaps, direct moves, and
  post-DPL satisfaction + `Pc`.
- Single-row-height cells only (multi-row macros are skipped).
- The embed CSV is the **only** reliable ground truth for downstream
  verification — CTS/GRT can add, resize, or move cells, which would
  change the tile-selection pool.
- `verify` exits `0` iff every expected cell is present and all residues
  match, `2` otherwise.
