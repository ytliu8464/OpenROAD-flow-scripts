# Spacing-only NDR routing watermark

Secondary IP watermark: assign a **Non-Default Rule (NDR)** with slightly larger **metal2 / metal3 spacing** to a deterministic subset of **signal nets** on a **post-CTS** OpenDB design, then run normal **global + detailed routing**. Primary watermark remains the **placement** row-parity flow under `../placement/`.

## Usage

```bash
./run_route_wm.sh  # Generate 4_cts_rt_wm.odf with NDR
./run_ppa.sh # contiune grt and drt
```


## What it does

- **Selection**: `WM_MESSAGE` + `WM_KEY` → HMAC-SHA256 → seed → `random.Random.sample` over eligible nets (same idea as placement watermarking).
- **Eligible nets**: not `SPECIAL`, `getSigType() == "SIGNAL"`, at least two `ITerm`s, sorted by name for reproducibility.
- **NDR**: One block-scoped rule (default name `wm_spacing_ndr`). Every **routing** layer gets `dbTechLayerRule` with default **width**; **metal2** and **metal3** get extra **spacing** (default **0.09 µm** vs NanGate45 default track spacing **0.07 µm** in the spacing table sense used by the flow).
- **Embed**: `net.setNonDefaultRule(ndr)` for each chosen net.
- **Verify**: Re-derive the same net list; check `net.getNonDefaultRule()` name and M2/M3 spacing in DBU; optional secondary flag if `dbWire` contains a **RULE** opcode.

## Strength (honest)

- **Useful as a second lock** with placement watermarking: an attacker must defeat both.
- **Moderate / limited alone**: NDR association is visible in ODB (`getNonDefaultRule`). A determined attacker can clear NDRs and re-route affected nets (~`WM_RT_NUM_NETS`) at **moderate** cost; spacing is harder to “fake” without routing than via swapping, but removal is still feasible.
- **Pc with metadata**: Under a null model where nets almost never receive this exact NDR by accident, use a tiny per-net `p_null` (default `1e-12` in verify) for `binomial_pc`; tune via `WM_RT_P_NULL` if you calibrate empirically.

## Requirements

- Run inside the project **Singularity** image (same as `../placement/place_wm.sh`).
- **OpenROAD** Python driver: `openroad -python -exit script.py` with `PYTHONPATH` including this directory.

## Scripts

| File | Role |
|------|------|
| `route_watermark_common.py` | Net collection, PRNG selection, NDR helper, `binomial_pc`, wire RULE scan |
| `route_watermark_embed.py` | Read post-CTS ODB, create NDR, assign nets, write ODB + optional CSV |
| `route_watermark_verify.py` | Read routed ODB, re-derive nets, verify NDR + spacing, optional CSV |
| `route_wm.sh` | `embed` / `verify` / `all` via Singularity |
| `run_route_wm.sh` | Sets default paths for AES `watermarking-test1-ppa` flow |

## Typical flow

1. Complete **CTS** (e.g. `4_cts.odb` from ORFS results).
2. **Embed** (before GRT):

   ```bash
   cd flow/watermarking/routing
   export WM_RT_INPUT=.../4_cts.odb
   export WM_RT_OUTPUT_ODB=.../4_cts_rt_wm.odb
   export WM_RT_OUTPUT_CSV=.../wm_route_nets_embed.csv
   export WM_MESSAGE='...' WM_KEY='...' WM_RT_NUM_NETS=100
   ./route_wm.sh embed
   ```

3. Run **GRT + DRT** in ORFS using `WM_RT_OUTPUT_ODB` as the design checkpoint for routing (integrate with your Makefile / `run.sh` as you do for other experiments).

4. **Verify** on the final routed database:

   ```bash
   export WM_RT_VERIFY_INPUT=.../6_final.odb
   export WM_RT_VERIFY_CSV=.../wm_route_nets_verify.csv   # optional
   ./route_wm.sh verify
   ```

Quick defaults (adjust `FLOW_RES` if needed):

```bash
./run_route_wm.sh embed
```

## Environment summary

| Variable | Meaning |
|----------|---------|
| `WM_RT_INPUT` | Post-CTS input ODB (embed) |
| `WM_RT_OUTPUT_ODB` | ODB with NDR assigned |
| `WM_RT_OUTPUT_CSV` | Optional embed net list CSV |
| `WM_RT_VERIFY_INPUT` | ODB to verify |
| `WM_RT_VERIFY_CSV` | Optional per-net verification CSV |
| `WM_MESSAGE` / `WM_KEY` | Watermark secret inputs |
| `WM_RT_NUM_NETS` | Number of nets (default 100) |
| `WM_RT_SPACING_UM` | M2/M3 NDR spacing in µm (default 0.09) |
| `WM_RT_NDR_NAME` | Rule name (default `wm_spacing_ndr`) |
| `WM_RT_TARGET_LAYERS` | Comma-separated layers (default `metal2,metal3`) |
| `WM_RT_P_NULL` | Per-net accidental-match probability for Pc (default 1e-12) |

## Exit codes

- `route_watermark_verify.py`: **0** if every watermark net passes; **2** if any failure (so CI can fail).

## Limitations

- Verification relies primarily on **NDR metadata** still attached to nets after route (OpenROAD does not strip it by default). If metadata is stripped, use geometry / RULE opcodes / external checks; measuring true neighbor clearance is not exposed as a simple Python API.
- **Re-embedding** on the same ODB without removing the old `wm_spacing_ndr` fails by design; always start from a clean `4_cts.odb`.

## References

- Kahng et al., *Robust IP Watermarking Methodologies for Physical Design* (placement + routing ideas).
- OpenROAD Tcl: `create_ndr`, `assign_ndr` (`tools/OpenROAD/src/odb/src/swig/tcl/odb.tcl`, `OpenRoad.tcl`).
