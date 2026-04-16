# Placement watermarking 

External Python tools that embed and verify **row-parity** constraints on a **post-detailed-placement** OpenDB (`.odb`), without modifying OpenROAD.

## Usage
```bash
./run_place_wm.sh  # apply watermarking to the placement solution
# ./run_place_wm.sh 
./run_ppa.sh # start with the watermarked placement and run cts grt drt to get post-route PPA results
./run_verify_stages.sh # verify the watermarks across stages
```

## Row index convention

Physical `dbRow` bottoms are sorted by ascending Y. Index `0` is the **bottom-most** row. Constraint parity `0` means the instance must sit on an **even** row index; `1` means **odd**.

## Prerequisites

- OpenROAD built with Python enabled (`openroad -python`).
- Run **inside Singularity** on this cluster (host `openroad` may miss shared libs / GLIBC):

```bash
alias or_singularity='singularity shell -B /home -B /tmp --bind /tmp/.X11-unix -e /home/tool/singularity/images/ispd26.sif'
```

Default flow uses the same `.sif` path as `run.sh` (`SINGULARITY_SIF` overrides).

## Scripts

| File | Role |
|------|------|
| `watermark_common.py` | Row indexing, HMAC seeding, binomial Pc, argv cleanup |
| `watermark_embed.py` | Select cells, assign parities, swap / nudge rows, incremental `detailedPlacement`, write `.odb`/`.def` |
| `watermark_verify.py` | Reload design, re-derive constraints, count satisfied / Pc |
| `watermark_verify_stages.py` | Read embed CSV as ground truth; verify those cells across multiple `.odb` checkpoints (post-CTS, post-GRT, post-DRT, etc.) |
| `run_verify_stages.sh` | Defaults for AES + PPA results; runs `watermark_verify_stages.py` in Singularity |
| `place_wm.sh` | `embed`, `verify`, `verify_stages`, or `all` inside Singularity |

## Quick start (AES example)

From the host (not inside Singularity), set paths and run:

```bash
export AES_RES=/home/fetzfs_projects/MISC-ytliu/watermarking/OR0415/OpenROAD-flow-scripts/flow/results/nangate45/aes/watermarking-test1
export WM_INPUT="${AES_RES}/3_place.odb"
export WM_OUTPUT_ODB="${AES_RES}/3_place_watermarked.odb"
export WM_OUTPUT_DEF="${AES_RES}/3_place_watermarked.def"
export WM_MESSAGE='Placed-with-watermark-test'
export WM_KEY='change-me-secret'
export WM_NUM_CELLS=100

cd /home/fetzfs_projects/MISC-ytliu/watermarking/OR0415/OpenROAD-flow-scripts/flow/watermarking
chmod +x run.sh
./run.sh embed

export WM_VERIFY_INPUT="${AES_RES}/3_place_watermarked.odb"
./run.sh verify
```

`./run.sh all` runs embed then verify, and **always** sets `WM_VERIFY_INPUT` to `WM_OUTPUT_ODB` so a stale `WM_VERIFY_INPUT` from an earlier session cannot point at the wrong file.

`watermark_verify.py` exits `0` if all constraints are satisfied after legalization, `2` otherwise (still prints Pc).

## Multi-stage verification (post-CTS / GRT / DRT)

CTS and routing can move or resize logic; **do not** re-run `watermark_selection` on later ODBs (new clock buffers change the sorted cell list). Use the **embed CSV** (`wm_cells_embed.csv`) as the list of watermarked instances and required parities.

From `flow/watermarking/placement`:

```bash
chmod +x run_verify_stages.sh place_wm.sh
# Defaults: WM_CELL_LIST=.../wm_cells_embed.csv and stages under
#   results/nangate45/aes/watermarking-test1-ppa/{4_cts,5_1_grt,5_route,6_final}.odb
./run_verify_stages.sh
```

Or via `place_wm.sh` (same env vars):

```bash
export WM_CELL_LIST="${AES_RES}/wm_cells_embed.csv"
export WM_VERIFY_STAGES="post_cts:/path/4_cts.odb,post_grt:/path/5_1_grt.odb,post_drt:/path/5_route.odb"
export WM_STAGE_REPORT="${AES_RES}/wm_stage_report.csv"   # optional
./place_wm.sh verify_stages
```

`WM_VERIFY_STAGES` is comma-separated `label:absolute_path.odb`. With `--output-csv` / `WM_STAGE_REPORT`, one row per watermark cell and columns `{stage}_row_idx`, `{stage}_satisfied` per checkpoint. Exit `0` only if every stage completes and every cell satisfies parity at every stage; otherwise `2`.

## Direct `openroad` invocation

Inside Singularity:

```bash
cd .../flow/watermarking
openroad -python -exit ./watermark_embed.py -- \
  --input "${WM_INPUT}" --output-odb "${WM_OUTPUT_ODB}" \
  --message "${WM_MESSAGE}" --key "${WM_KEY}" --num-cells 100
```

(If your OpenROAD build does not support `--`, rely on `run.sh` or env vars only.)

## Tunables

- **`WM_NUM_CELLS`**: More constraints → stronger signature (lower Pc) but more placement perturbation.
- **`WM_MAX_DISP_X` / `WM_MAX_DISP_Y`**: Micron limits passed to incremental detailed placement (site-snapped internally).

## Notes

- Start from **post-DP** placement (`3_place.odb` / `3_5_place_dp.odb`), not global placement overlap.
- Legalization may break a small fraction of parity constraints; the embed script prints post-DPL satisfaction counts and Pc.
