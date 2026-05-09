# CTS fanout-parity watermark

Post-TritonCTS watermark: for selected **sibling LCB pairs** `(L_A, L_B)` the
secret seed dictates the **fanout parity (odd/even)** of one of the two LCBs.
Parity is flipped, when needed, by reassigning **boundary flip-flops**
(FFs whose distance to `L_B` is only ~a couple of site pitches more than to
`L_A`) from one LCB to the other. Incremental timing guards each reassignment
and rolls back on skew/slew failure. Satisfied LCBs are marked
`do_not_touch` so downstream GRT/DRT cannot undo the watermark.

All pseudo-randomness is derived from the 32B `seed_cts.hex` produced by
[`watermarking/gen_key/`](../gen_key/README.md). There are no `WM_KEY` /
`WM_MESSAGE` inputs (same contract as `place_site_parity/`).

## Algorithm

1. **Run TritonCTS with default settings** to obtain the baseline clock tree
   (`4_cts.odb`).
2. **Enumerate LCBs**: instances whose master name matches a clock-buffer
   hint (`CLKBUF`, `CLKINV`, `CLKGATE`, `CTSBUF`) AND whose output net's
   sinks are all sequential clock pins (no downstream buffer).
3. **Sibling pairs**: group LCBs by their **immediate parent** (the driver
   of the LCB's clock input net). Emit all unordered pairs within a group
   whose centroids are within `WM_CTS_SIBLING_DIST_UM` microns.
4. **Headroom filters**: drop pairs where either LCB has
   `fanout + WM_CTS_FANOUT_MARGIN >= max_fanout(Liberty)` or output slew
   above `(1 - WM_CTS_SLEW_HEADROOM_FRAC) * max_transition(Liberty)`.
5. **Boundary FFs**: for each pair `(L_A, L_B)`, an FF currently driven by
   `L_A` is *boundary* iff `d_B - d_A <= WM_CTS_DELTA_SITES * site_pitch`.
6. **Seed-driven selection**:
   - Master RNG seeded by `HMAC_SHA256(seed, "cts_pairs")[:8]`.
   - `rng.sample` picks `WM_CTS_NUM_PAIRS` pairs from sorted candidates.
   - Each chosen pair gets its bits from a pair-scoped
     `HMAC_SHA256(seed, "pair", parent, L_A, L_B)`:
     `target_bit = d[0] & 1`, `target_lcb_is_A = (d[0] >> 1) & 1`.
7. **Parity flip**: if `fanout(target_lcb) % 2 != target_bit`, reassign the
   closest-boundary FF from source to target by rewiring its clock iterm.
8. **Incremental timing**: after each reassignment run
   `estimate_parasitics -placement` and check that (a) max pin slew on each
   LCB output is still <= Liberty `max_transition`, and (b) worst clock
   skew has not grown by more than `WM_CTS_SKEW_SLACK_PS` picoseconds.
   On failure, revert the reassignment and try the next boundary FF (up to
   `WM_CTS_MAX_ATTEMPTS = 3`). If all attempts fail, skip the pair and
   record `skipped_reason` in the embed CSV.
9. **Mark fixed**: successfully watermarked LCBs receive
   `setDoNotTouch(True)` and `setPlacementStatus("FIRM")`.

## Files

| File | Role |
|------|------|
| `cts_watermark_common.py` | Seed / HMAC helpers, LCB enumeration, sibling pairing, timing / Liberty wrappers, binomial Pc |
| `cts_watermark_embed.py`  | Filter candidates, PRNG-select pairs, flip parity via iterm rewiring, incremental timing + revert-and-retry, mark fixed, write ODB + CSV |
| `cts_watermark_verify.py` | CSV-driven fanout parity check on one ODB |
| `cts_watermark_verify_stages.py` | Same check across post-CTS / post-GRT / post-DRT / post-final ODBs |
| `cts_wm.sh` | Singularity wrapper (`embed` / `verify` / `verify_stages` / `all`) |
| `run_cts_wm.sh` | AES / NanGate45 defaults; bootstraps `gen_key/` keygen + sign |
| `run_verify_stages.sh` | Stage verification defaults |
| `run_ppa.sh` | Continue GRT + DRT from `4_cts_wm.odb` via the ORFS Makefile |

## Usage

```bash
cd .../flow/watermarking/cts
chmod +x run_cts_wm.sh run_verify_stages.sh run_ppa.sh cts_wm.sh

# 1) Embed + self-verify (also ensures gen_key/ produced seed_cts.hex).
./run_cts_wm.sh all

# 2) Run GRT + DRT on the watermarked CTS ODB.
./run_ppa.sh

# 3) Verify watermark survival across stages.
./run_verify_stages.sh
```

## Direct invocation

Inside Singularity:

```bash
export WM_SEED_HEX=.../gen_key/out/aes/seed_cts.hex
export WM_CTS_INPUT=.../4_cts.odb
export WM_CTS_OUTPUT_ODB=.../4_cts_wm.odb
export WM_CTS_OUTPUT_CSV=.../wm_cts_pairs_embed.csv
./cts_wm.sh embed

export WM_CTS_VERIFY_INPUT=.../4_cts_wm.odb
export WM_CELL_LIST=.../wm_cts_pairs_embed.csv
./cts_wm.sh verify
```

## Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `WM_SEED_HEX` | `gen_key/out/<design>/seed_cts.hex` | 32B HMAC seed (from `gen_key/`) |
| `WM_CTS_INPUT` | `${FLOW_RES}/4_cts.odb` | Post-CTS ODB |
| `WM_CTS_OUTPUT_ODB` | `${FLOW_RES}/4_cts_wm.odb` | Watermarked ODB |
| `WM_CTS_OUTPUT_CSV` | `${FLOW_RES}/wm_cts_pairs_embed.csv` | Ground-truth pair list |
| `WM_CTS_NUM_PAIRS` | 32 | Number of LCB pairs to watermark |
| `WM_CTS_SIBLING_DIST_UM` | 20 | Sibling geographic cap (microns) |
| `WM_CTS_DELTA_SITES` | 2 | Boundary-FF delta in site pitches |
| `WM_CTS_FANOUT_MARGIN` | 2 | Minimum fanout slack below max_fanout |
| `WM_CTS_SLEW_HEADROOM_FRAC` | 0.20 | Minimum output slew headroom |
| `WM_CTS_SKEW_SLACK_PS` | 20 | Extra worst skew allowed by reassignment |
| `WM_CTS_MAX_FANOUT` | 32 | Fallback when Liberty lookup fails |
| `WM_CTS_MAX_TRANSITION_NS` | 0.4 | Fallback Liberty max_transition |
| `WM_CTS_MAX_ATTEMPTS` | 3 | Boundary-FF attempts per pair |
| `WM_CELL_LIST` | embed CSV | Ground truth for verify / verify_stages |
| `WM_CTS_VERIFY_INPUT` | *(embed output)* | ODB to verify |
| `WM_CTS_VERIFY_CSV` | *(unset)* | Optional per-pair verify report |
| `WM_VERIFY_STAGES` | see `run_verify_stages.sh` | `label:odb,...` list |
| `WM_STAGE_REPORT` | *(unset)* | Optional per-pair x stage CSV |

## Exit codes

- `cts_watermark_verify.py` and `cts_watermark_verify_stages.py` exit **0**
  when every pair's observed fanout parity matches `target_bit`, **2**
  otherwise.

## Strength / notes

- **Null probability** is `p = 1/2` per pair, so `Pc = sum_{i=0..x} C(N,i) 0.5^N`.
  With `N = 32` watermarked pairs and zero failures, `Pc <= 2^-32`.
- The watermark lives in a *structural* property (which LCB drives which FF)
  that persists across GRT/DRT -- those stages don't rewire clock sinks --
  but CTS re-runs or clock-tree rebuild would destroy it, so we mark
  watermark LCBs `do_not_touch`.
- Because CTS renames/creates buffers each run, the seed **cannot** be used
  to re-derive the watermarked LCB identities on a downstream ODB; the
  embed CSV (`wm_cts_pairs_embed.csv`) is the ground truth for verify.
- TritonCTS-inserted clock-gate / clock-inverter cells that drive FFs
  directly are also recognized as LCBs via the master-name hints.
