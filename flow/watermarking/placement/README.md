# Placement watermarking 

External Python tools that embed and verify **row-parity** constraints on a **post-detailed-placement** OpenDB (`.odb`), without modifying OpenROAD.

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
| `run.sh` | `singularity exec ... env ... openroad -python -exit ...` |

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
