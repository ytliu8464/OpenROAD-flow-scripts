# CTS fanout-parity watermark (v2)

Post-TritonCTS watermark: for selected **LCB pairs** `(L_A, L_B)` the secret seed
fixes **sequential fanout parity** ``seq_fanout(target_lcb) % 2``. Parity is
adjusted by reassigning **boundary flip-flops** between the two LCBs.
Incremental timing guards each move; successful pairs mark LCBs (and quasi-leaf
repair cells) **do-not-touch** so downstream routing does not undo the watermark.

Two **channels** plus mixed pair support (priority: pure, quasi-leaf, then
pure-quasi):

| Channel       | Meaning |
|---------------|---------|
| `pure`        | Leaf LCB: all clock sinks are FFs (`repair_fanout == 0`, `other_fanout == 0`). |
| `quasi_leaf`  | `seq_fanout > 1`, `1 <= repair_fanout <= R_max`, `other_fanout == 0`; repair naming heuristics + stricter slew/cap/slack checks; repair identities frozen. |
| `pure_quasi` pair | Cross-channel pair with one pure LCB and one quasi-leaf LCB. It uses the same `seq_fanout(target_lcb) % 2` bit and the stricter quasi-leaf safety checks. |

Candidate **pairs** may be pure-pure, quasi-quasi, or pure-quasi. Pairs are
geographic neighbors (centroid distance). The seed selects pairs and per-pair
target bit / which side is target.

All pseudo-randomness uses the 32B `seed_cts.hex` from
[`watermarking/gen_key/`](../gen_key/README.md). No `WM_KEY` / `WM_MESSAGE`.

## High-level flow

1. Load post-CTS clock tree (`4_cts.odb`).
2. Classify clock-buffer instances into pure LCBs, quasi-leaf LCBs, or non-candidates.
3. Build candidate **pure**, **quasi_leaf**, and **pure_quasi** pairs (proximity).
4. Boundary FFs: FFs on the target LCB whose distance gap to the peer LCB is within `delta` site pitches.
5. Seed / HMAC selects watermark pairs and target bits.
6. Embed with priority: fill **pure** first, then **quasi_leaf**, then
   **pure_quasi**, until `WM_CTS_NUM_PAIRS` successes or attempts exhausted
   (**one pair, one successful embed** — both LCBs are removed from the stack
   after success).
7. Legality + incremental **slew, capacitance, skew**, and (quasi) **setup/hold** slack checks after each FF move.
8. Reject unsafe moves (revert reassignment).
9. Mark accepted structures protected (`setDoNotTouch`, `FIRM`, plus repair cells on quasi-leaf).
10. Continue routing / PPA (`run_ppa.sh`).
11. Extract / verify (`run_verify_stages.sh`) using the embed CSV as ground truth.

## Classification

Per LCB output net, each sink is labeled:

- **seq**: sequential clock pin (FF).
- **repair**: timing-repair load/buffer whose instance basename contains a
  repair hint (`rebuffer`, `wire`, `hold`, `max_cap`, `max_slew`, `fanout`,
  `load_slew`, `clkload`, `clk_load`), and **not** named like a CTS LCB
  (`clkbuf`, …). `clkload` is recognized even when the Liberty master is not a
  simple buffer.
- **other**: everything else (violates leaf / quasi-leaf structure).

**Pure LCB:** `seq > 0`, `repair == 0`, `other == 0`.

**Quasi-leaf LCB:** `seq > 1`, `1 <= repair <= R_max` (default `WM_CTS_R_MAX=2`),
`other == 0`.

**Non-candidate:** else.

Quasi-leaf embedding does not add repair buffers; after each trial, **repair
instance identities** must match the pre-move signature or the move is reverted
(`repair_changed`).

## Algorithm (embed)

1. **Run TritonCTS** → baseline `4_cts.odb`.
2. **Classify** LCBs (`classify_lcbs`).
3. **Proximity pairs** separately for pure, quasi_leaf, and pure-quasi pools.
4. **Filters:** fanout headroom (Liberty `max_fanout`), slew margin; quasi_leaf
   adds capacitance margin vs `max_capacitance`, optional skip of repair nets
   with `hold` in the instance name (`WM_CTS_AVOID_HOLD_REPAIR`).
5. **Selection:** domain-separated RNGs (`cts_pure`, `cts_quasi`, `cts_pure_quasi`); `WM_CTS_CHANNEL_BUDGET`
   (`auto` | `pure_only` | `quasi_only` | `N:M` ratio caps).
6. **Iterate** the merged queue until **successful embed count** reaches
   `WM_CTS_NUM_PAIRS` or no more attempts. Skip if either LCB was already used
   (`lcb_already_used`).
7. **Parity:** if `seq_fanout(target) % 2 != target_bit`, move the closest
   boundary FF from target → other; **seq parity** defines the bit (same for pure
   and quasi; for pure, total fanout equals seq).
8. **Incremental STA** after each trial: slew (and quasi: enforced margin vs
   `max_transition`), optional cap vs `max_capacitance`, skew growth bound,
   quasi setup/hold slack degradation bounds; quasi repair-signature equality.
9. **CSV** ground truth + watermarked ODB.

## Files

| File | Role |
|------|------|
| `cts_watermark_common.py` | Seed/HMAC, sink breakdown, classification, pairing, Liberty/timing/cap helpers |
| `cts_watermark_embed.py` | Two-channel embed, filters, legalize checks, CSV + ODB |
| `cts_watermark_verify.py` | CSV vs ODB: `seq_fanout % 2`, quasi repair tamper |
| `cts_watermark_verify_stages.py` | Same check across stage ODBs |
| `cts_wm.sh` | Singularity wrapper (`embed` / `verify` / `verify_stages` / `all`) |
| `run_cts_wm.sh` | AES / NanGate45 defaults; keygen + `cts_wm.sh` |
| `run_verify_stages.sh` | Stage verification defaults |
| `run_ppa.sh` | GRT + DRT from `4_cts_wm.odb` |

## Usage

```bash
cd .../flow/watermarking/cts_v2
chmod +x run_cts_wm.sh run_verify_stages.sh run_ppa.sh cts_wm.sh

# 1) Embed + self-verify (also ensures gen_key/ produced seed_cts.hex).
./run_cts_wm.sh all

# 2) Run GRT + DRT on the watermarked CTS ODB.
./run_ppa.sh

# 3) Verify watermark survival across stages.
./run_verify_stages.sh
```

### Direct invocation (Singularity)

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
| `WM_SEED_HEX` | `gen_key/out/<design>/seed_cts.hex` | 32B HMAC seed |
| `WM_CTS_INPUT` | `${FLOW_RES}/4_cts.odb` | Post-CTS ODB |
| `WM_CTS_OUTPUT_ODB` | `${FLOW_RES}/4_cts_wm.odb` | Watermarked ODB |
| `WM_CTS_OUTPUT_CSV` | `${FLOW_RES}/wm_cts_pairs_embed.csv` | Ground-truth CSV |
| `WM_CTS_NUM_PAIRS` | 32 | Target **successful** embed count |
| `WM_CTS_SIBLING_DIST_UM` | 20 | Max centroid distance (µm) for a pair |
| `WM_CTS_DELTA_SITES` | 2 | Boundary-FF threshold (site pitches) |
| `WM_CTS_FANOUT_MARGIN` | 2 | Min slack vs Liberty `max_fanout` |
| `WM_CTS_SLEW_HEADROOM_FRAC` | 0.20 | Min output slew margin (**pure** channel) |
| `WM_CTS_SKEW_SLACK_PS` | 20 | Max growth of worst \|clock skew\| vs baseline (pure) |
| `WM_CTS_MAX_FANOUT` | 32 | Fallback `max_fanout` |
| `WM_CTS_MAX_TRANSITION_NS` | 0.4 | Fallback `max_transition` (ns) |
| `WM_CTS_MAX_CAP_FF` | 50 | Fallback `max_capacitance` (fF) |
| `WM_CTS_MAX_ATTEMPTS` | 3 | Boundary-FF trials per pair |
| `WM_CTS_R_MAX` | 2 | Quasi-leaf max `repair_fanout` |
| `WM_CTS_QL_SLEW_HEADROOM_FRAC` | max(0.30, pure+0.10) | Quasi-leaf slew margin |
| `WM_CTS_QL_CAP_HEADROOM_FRAC` | 0.20 | Quasi-leaf capacitance margin |
| `WM_CTS_QL_SETUP_SLACK_PS` | 50 | Quasi setup WNS degradation limit |
| `WM_CTS_QL_HOLD_SLACK_PS` | 30 | Quasi hold WNS degradation limit |
| `WM_CTS_QL_SKEW_SLACK_PS` | `WM_CTS_SKEW_SLACK_PS` | Quasi skew slack vs baseline |
| `WM_CTS_AVOID_HOLD_REPAIR` | 1 | Skip quasi_leaf with `hold` repair hint |
| `WM_CTS_CHANNEL_BUDGET` | auto | `auto` \| `pure_only` \| `quasi_only` \| `N:M` |
| `WM_CELL_LIST` | embed CSV | Ground truth for verify |
| `WM_CTS_VERIFY_INPUT` | embed output | ODB to verify |
| `WM_CTS_VERIFY_CSV` | *(unset)* | Optional per-pair verify report |
| `WM_VERIFY_STAGES` | see `run_verify_stages.sh` | `label:odb,...` |
| `WM_STAGE_REPORT` | *(unset)* | Optional per-pair × stage CSV |

## Embed CSV columns (ground truth)

Includes `channel`, `target_lcb`, `target_bit`, `final_bit`, seq/repair fanout
counts, caps, `skipped_reason`, etc. Verification uses **`seq_fanout(target_lcb) % 2`**
vs `target_bit`. Legacy CSVs without `channel` default to **pure**; parity still
matches when all sinks were sequential.

## Exit codes

`cts_watermark_verify.py` and `cts_watermark_verify_stages.py` exit **0** when
every pair matches (`observed_bit == target_bit` and no quasi repair tampering),
**2** otherwise.

## Strength / notes

- **Null probability** per pair is \(p = 1/2\) on parity; with \(N\) independent
  constraints, \(P_c = \sum_{i=0}^{x} \binom{N}{i} 0.5^N\).
- Downstream GRT/DRT generally preserve clock sink wiring; **do-not-touch** on
  watermarked LCBs (and quasi repair cells) reduces disruption.
- CTS re-runs change buffer names; the **embed CSV** remains the verification
  ground truth on later ODBs.
