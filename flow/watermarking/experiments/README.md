# PDMarks Experiments & Attack Evaluation — Runbook

This directory contains the full experiment harness for the PDMarks paper.
It **never modifies `main.tex`**; every table cell is rendered into
`results/.../tab_*.tex` so you can paste it by hand.

---

## Table of Contents

1. [Directory layout](#1-directory-layout)
2. [Prerequisites](#2-prerequisites)
3. [Phase 1 — Embedding, PPA, Capacity, Survival](#3-phase-1--embedding-ppa-capacity-survival)
4. [Phase 2 — Security Analysis](#4-phase-2--security-analysis)
5. [Phase 3 — Attack Evaluation](#5-phase-3--attack-evaluation)
6. [Full pipeline (one shot)](#6-full-pipeline-one-shot)
7. [Runtime overrides](#7-runtime-overrides)
8. [Output reference](#8-output-reference)
9. [Cryptographic invariants](#9-cryptographic-invariants)
10. [Baseline methods](#10-baseline-methods-tables-v--vi)

---

## 1. Directory layout

```
experiments/
├── bench_matrix.py               # Active 10-cell matrix (Bench dataclass w/ design_nickname)
├── sbpy                          # Shim: picks Singularity Python w/ sklearn/matplotlib
│
├── lib/
│   ├── orfs.py                   # Path resolution, 6_report.json / *.log readers
│   ├── pc.py                     # P_c  (Eqs. pc_stage / pc_total)
│   ├── route_stat.py             # Z_R / p_R (Eqs. routing_stat)
│   ├── keys.py                   # SHA-256 seed derivation; HMAC-SHA256 net selection
│   ├── keyless_verify.py         # Placement / CTS / routing extraction-rate functions
│   ├── eligibility.py            # Public-rule reconstruction of eligible P/C/R sets
│   │                               (delegates to embedders' own enumeration helpers)
│   └── thresholds.py             # τ_P / τ_C / τ_all / α_R + ownership_pass() helper
│
├── drivers/
│   ├── _common.sh                # Shared env setup, ensure_keys()
│   ├── run_ref.sh                # Unmodified reference ORFS flow
│   ├── run_p_only.sh             # Placement watermark + PPA continuation
│   ├── run_c_only.sh             # CTS watermark + PPA continuation
│   ├── run_r_only.sh             # Routing wrong-way bias + PPA continuation
│   └── run_all_stage.sh          # Chained P → C → R + PPA
│
├── baselines/                    # each <name>/ has embed.py, verify.py, run.sh
│   ├── _common.py                # Capacity lookup (K = PDMarks P-only), HMAC select
│   ├── kahng/                    # Kahng et al. DAC'98 / TCAD'01 (row-parity)
│   ├── cell_scattering/          # Cai et al. ISIC'07
│   ├── buffer_insertion/         # Sun et al. ISQED'06
│   ├── icmarks/                  # Zhang et al. TCAD'25 (post-DP re-impl)
│   └── automarks/                # Zhang et al. MLCAD'24 (post-DP re-impl)
│
├── sensitivity/
│   ├── run_sensitivity.sh        # Sweep knobs on SweRV NG45 + SweRV ASAP7
│   └── aggregate_sensitivity.py
│
├── wrong_key/
│   ├── run_wrong_key.py          # 1000 wrong-key Pc null distribution
│   └── plot_wrong_key.py         # Fig. wrong_key_analysis.png
│
├── attacks/
│   ├── blind/                    # paper §7.1
│   │   ├── attack_placement.py   # Reconstruct eligible 2-/3-tuple pool;
│   │   │                           swap pairs / permute triples; legalize
│   │   │                           with WM_MAX_DISP_X/Y via dpl.detailedPlacement
│   │   ├── attack_cts.py         # Reconstruct eligible LCB-pair pool; move one
│   │   │                           sequential sink between L_A and L_B; skip
│   │   │                           dont_touch / LCB sinks / repair buffers
│   │   ├── attack_routing.py     # Pick q_s of eligible signal nets; write
│   │   │                           WM_NETS_ATTACK file (consumed by re-route step)
│   │   └── run_blind_attack.py   # Driver: stages = {placement, cts, routing, all_stage};
│   │                              q_s = {10/20/50/80/100%};  computes r_all + ownership
│   │                              decision; skips routing on ASAP7 by default
│   ├── targeted/                 # paper §7.2
│   │   ├── features.py           # ~10 placement + ~11 CTS + ~9 routing features
│   │   └── run_targeted_attack.py# RandomForest per stage; AUC + precision@recall +
│   │                              recall@top-K; routing top-K triggers real re-route
│   └── ppa/                      # ΔPPA harness for paper §7 PPA columns
│       ├── run_attack_ppa.py     # Re-runs ORFS back-end on each atk_p_*.odb / atk_c_*.odb
│       │                           (routing attacks self-complete via run_attack_route.sh)
│       └── aggregate_attack_ppa.py  # → results/phase3/blind_ppa.csv (ΔPPA vs ref + vs wm)
│
├── tools/
│   ├── dump_route_counts.py      # OpenROAD-Python: per-net (wrong_way, total) CSV
│   └── dump_route_counts.sh      # Wrapper for dump_route_counts.py
│
├── phase1_capacity.py            # Parse embed logs → raw/capacity_*.json
├── phase1_ppa.py                 # Δ-PPA + P_c per variant → raw/ppa_*.json
├── phase1_survival.py            # Verify watermark at 4 post-stage checkpoints
├── run_phase1_embeds.sh          # Fast embed-only pass (no full PPA round-trip)
├── run_baselines_all.sh          # All 5 baselines × 8 paper designs (40 ORFS flows)
├── run_phase1_baselines.sh       # Legacy: cellscatter+bufins over 7-bench matrix
├── aggregate.py                  # Roll up raw JSON → per-phase CSV
└── render_tex.py                 # CSV → copy-pasteable LaTeX row fragments
```

---

## 2. Prerequisites

### 2a. Environment

All scripts assume you are on the lab server with:
- Singularity image at `/home/tool/singularity/images/ispd26.sif` (contains OpenROAD, Python 3.12, sklearn, matplotlib)
- OpenROAD binary at `OR0415/OpenROAD/build/bin/openroad`
- Project root at `/home/fetzfs_projects/MISC-ytliu/watermarking`

Override any of these via environment variables:

```bash
export PROJ_DIR=/my/other/location
export OPENROAD_EXE=/path/to/openroad
export SINGULARITY_SIF=/path/to/ispd26.sif
```

### 2b. Reference flow results

The 10 active benches must have a completed reference ORFS run (`6_report.json` on disk).
These are already present. To add a new design later:

```bash
DESIGN=my_design PLATFORM=nangate45 WM_FLOW_VARIANT=base \
  bash drivers/run_ref.sh
```

### 2b-1. `DESIGN` vs `DESIGN_NICKNAME`

ORFS supports a `DESIGN_NICKNAME` override in each design's `config.mk` that
controls **on-disk path names**, while `DESIGN_NAME` still drives the config /
gen_key identity.  The bench matrix and every driver / module script in this
harness handle the distinction automatically:

| Kind of path | Uses | Example for `bp_multi_top` |
|---|---|---|
| `designs/<plat>/<DESIGN>/config.mk` | `DESIGN` (full name) | `designs/nangate45/bp_multi_top/config.mk` |
| `gen_key/out/<DESIGN>/seed_*.hex` | `DESIGN` (full name) | `gen_key/out/bp_multi_top/seed_*.hex` |
| `flow/results/<plat>/<NICKNAME>/<variant>/` | `DESIGN_NICKNAME` | `flow/results/nangate45/bp_multi/...` |
| `flow/logs/<plat>/<NICKNAME>/<variant>/` | `DESIGN_NICKNAME` | `flow/logs/nangate45/bp_multi/...` |
| `OR_inputs/{place_wm,cts_wm,route_wm}/<plat>/<NICKNAME>/` | `DESIGN_NICKNAME` | `OR_inputs/.../nangate45/bp_multi/` |
| `experiments/results/<plat>/<NICKNAME>/<variant>/` | `DESIGN_NICKNAME` | `experiments/results/nangate45/bp_multi/...` |
| wm_log filenames (`<design>_run_*.log`) | `DESIGN` (full name) | `bp_multi_top_run_place_wm_*.log` |
| Output CSV / JSON `"design"` columns | `DESIGN` (full name) — logical identity |  |

You always set `DESIGN=<full_name>` on the command line.  `drivers/_common.sh`
then runs `make print-DESIGN_NICKNAME DESIGN_CONFIG=...` to auto-discover the
nickname (falls back to `DESIGN` when there's no override) and exports
`DESIGN_NICKNAME` for every child script.  In Python, every `Bench` has both
`b.design` (full) and `b.design_nickname` fields; the harness uses the right
one per call-site.

The only bench in the matrix that currently has a split is
**`bp_multi_top` (NG45)** with nickname `bp_multi` — all others have
`design_nickname == design`.

### 2c. Cryptographic keys (once per design)

Generate the owner keypair and per-design seed bundle. The drivers call
`ensure_keys` automatically, but you can do it manually:

```bash
cd OR0415/OpenROAD-flow-scripts/flow/watermarking/gen_key
./gen_key.sh keygen --owner-id yiting --out-dir keys
./gen_key.sh sign --sk keys/sk.pem --pk keys/pk.pem \
    --owner-id yiting --design-id aes --out-dir out/aes --force
```

Outputs `out/<design>/seed_placement.hex`, `seed_cts.hex`, `seed_routing.hex`.

---

## 3. Phase 1 — Embedding, PPA, Capacity, Survival

All commands are run from the `experiments/` directory.

### Step 1-1. Collect reference PPA (tab:exp_setup)

```bash
python3.11 aggregate.py --what ref
# writes results/phase1/summary_ref.csv
python3.11 render_tex.py
# writes results/phase1/tab_exp_setup.tex
```

### Step 1-2. Run watermarked ORFS flows

Each driver embeds the watermark and runs the full ORFS flow to completion.
Results land in module-specific subdirectories under `flow/watermarking/`.

**Single design, one variant at a time:**

```bash
DESIGN=aes PLATFORM=nangate45 WM_FLOW_VARIANT=watermarking-test1 \
  bash drivers/run_p_only.sh       # placement only

DESIGN=aes PLATFORM=nangate45 WM_FLOW_VARIANT=watermarking-test1 \
  bash drivers/run_c_only.sh       # CTS only

DESIGN=aes PLATFORM=nangate45 WM_FLOW_VARIANT=watermarking-test1 \
  bash drivers/run_r_only.sh       # routing only

DESIGN=aes PLATFORM=nangate45 WM_FLOW_VARIANT=watermarking-test1 \
  bash drivers/run_all_stage.sh    # P → C → R chained (all-stage)
```

**All 10 designs in a loop:**

```bash
for bench in \
    "nangate45 aes            watermarking-test1" \
    "nangate45 jpeg           watermarking-test1" \
    "nangate45 swerv_wrapper  base" \
    "nangate45 ariane136      base_tcp3p5" \
    "nangate45 bp_multi_top   base_tcp3p2" \
    "asap7     aes            base" \
    "asap7     jpeg           base_tcp540" \
    "asap7     swerv_wrapper  base_tcp1455" \
    "asap7     ariane         base_fixed" \
    "asap7     cva6           base_tcp950"; do
  read -r plat dsgn var <<< "$bench"
  DESIGN=$dsgn PLATFORM=$plat WM_FLOW_VARIANT=$var bash drivers/run_all_stage.sh
done
```

**Artifact locations after a run:**

| Artifact | Location |
|---|---|
| Placement/CTS embed/verify CSVs, watermarked ODB/DEF files (`3_place_order_wm.odb`, `3_place_order_wm.def`, `4_cts_wm.odb`) | `flow/watermarking/experiments/results/{plat}/{design}/{FLOW_VARIANT}/` |
| PPA stage logs, `6_report.json` | `flow/watermarking/experiments/logs/{plat}/{design}/{FLOW_VARIANT}/` |
| Post-GRT/DRT ODBs (`5_1_grt.odb`, `5_route.odb`, `6_final.odb`) | `flow/watermarking/experiments/results/{plat}/{design}/{FLOW_VARIANT}/` |
| Routing artifacts (`watermark_nets.txt`, `route_counts.csv`) | `flow/watermarking/experiments/results/{plat}/{design}/{FLOW_VARIANT}/` |

**Two distinct variant names are in play:**

| Variable | What it names | Set by |
|---|---|---|
| `WM_FLOW_VARIANT` | The **reference** ORFS run whose ODBs are the starting point | `bench_matrix.py` (e.g. `watermarking-test1`, `base`, `base_tcp540`) |
| `FLOW_VARIANT` | The **watermarked PPA** run written by each module's `run_ppa.sh` | Each module script (see table below) |

**Default `FLOW_VARIANT` names per driver / module:**

| Driver | Module written to | Default `FLOW_VARIANT` | Pattern |
|---|---|---|---|
| `run_p_only.sh` | `place_ordering` | `pdmarks-p-only` | fixed |
| `run_c_only.sh` | `cts_v2` | `pdmarks-c-only` | fixed |
| `run_r_only.sh` | `routing_wrong_way` | `pdmarks-r-only` | fixed |
| `run_all_stage.sh` | all three | `pdmarks-all-stage` | fixed |
| `place_ordering/run_ppa.sh` (direct) | `place_ordering` | `{WM_FLOW_VARIANT}-ppa-v2` | derived |
| `cts_v2/run_ppa.sh` (direct) | `cts_v2` | `{WM_FLOW_VARIANT}-ppa-run2` | derived |
| `routing_wrong_way/run.sh` (direct) | `routing_wrong_way` | `route-wm-wrong-way` | fixed |

> The experiment analysis scripts (`phase1_ppa.py`, `phase1_survival.py`, etc.) use
> `find_latest_wm_variant(module, plat, design)` to **auto-discover** the most recently
> completed `FLOW_VARIANT` directory (highest-mtime `6_report.json`). You can pin a
> specific one with `WM_VARIANT_<MODULE>` — see [Runtime overrides](#7-runtime-overrides).

**Override `FLOW_VARIANT` at the command line:**
```bash
# Use a custom variant name instead of the default
DESIGN=aes PLATFORM=nangate45 WM_FLOW_VARIANT=watermarking-test1 \
  FLOW_VARIANT=my-p-only-run bash drivers/run_p_only.sh
```

**Adaptive watermark parameters:**

The experiment drivers source `drivers/adaptive_params.sh` by default.  Before
running a watermark stage, the driver reads the reference
`flow/logs/{plat}/{design}/{WM_FLOW_VARIANT}/6_report.json` plus
`clock_period.txt`, classifies the reference by normalized setup violation
`abs(WNS) / TCP`:

| Class | Condition | Intended behavior |
|---|---|---|
| `near_closed` | negative WNS within `2% TCP` | Stronger watermarking; small timing violation is treated as usable margin |
| `moderate_neg` | negative WNS within `8% TCP` | Moderate watermarking with relaxed candidate search |
| `stressed` | negative WNS within `20% TCP` | Smaller watermark, but still nonzero |
| `fragile` | negative WNS beyond `20% TCP` | Conservative but not disabled |
| `relaxed` | nonnegative WNS above `3% TCP` | Strongest watermarking |

The adaptive layer sets only variables that are not already exported.  Manual
command-line settings always win.

| Driver | Adaptive stages |
|---|---|
| `run_p_only.sh` | placement only |
| `run_c_only.sh` | CTS only |
| `run_r_only.sh` | routing only |
| `run_all_stage.sh` | placement + CTS + routing |

Key adaptive knobs:

| Stage | Variables adjusted |
|---|---|
| Placement | `WM_MIN_PAIRS_TOTAL`, `WM_PAIR_DIST_UM`, `WM_HPWL_EPS_PAIR_DBU`, `WM_SLACK_THRESHOLD_NS`, `WM_TILE_TOUCH_FRAC_MAX`, `WM_MAX_DISP_X/Y`, `WM_GUARD_DEGRADE_NS`, etc. |
| CTS | `WM_CTS_NUM_PAIRS`, `WM_CTS_SIBLING_DIST_UM`, `WM_CTS_DELTA_SITES`, `WM_CTS_R_MAX`, `WM_CTS_SKEW_SLACK_PS`, `WM_CTS_CHANNEL_BUDGET`, etc. |
| Routing | `WATERMARK_FRACTION`, `WATERMARK_STRENGTH`, `WATERMARK_P` |

Distance-related knobs are platform-aware.  ASAP7 uses smaller micron windows
and displacements than Nangate45; Nangate45 uses larger physical distances to
cover a comparable number of sites/tracks.

For example, a mildly negative `nangate45/aes` reference may choose:

```text
adaptive place: class=moderate_neg target_pairs=40 pair_dist=3.2um ...
adaptive cts:   class=moderate_neg pairs=20 dist=55um ...
adaptive route: class=moderate_neg fraction≈0.010 strength=70
```

Disable adaptive defaults when you want fixed hand-tuned parameters:

```bash
PDMARKS_ADAPTIVE_PARAMS=0 \
DESIGN=aes PLATFORM=nangate45 WM_FLOW_VARIANT=watermarking-test1 \
  bash drivers/run_all_stage.sh
```

Override any individual knob while keeping the rest adaptive:

```bash
WM_CTS_NUM_PAIRS=12 WATERMARK_STRENGTH=50 \
DESIGN=aes PLATFORM=nangate45 WM_FLOW_VARIANT=watermarking-test1 \
  bash drivers/run_all_stage.sh
```

### Step 1-3. Embed-only pass (capacity only, no full PPA run)

If you only need the embed CSVs and capacity counts — without running the
full ORFS flow — this is much faster (seconds per design):

```bash
bash run_phase1_embeds.sh
```

By default, embed-only placement/CTS CSVs and watermarked ODB/DEF files are
written to:

```text
flow/watermarking/experiments/results/{plat}/{design}/pdmarks-embed-only/
```

Use a custom output variant with either variable:

```bash
PHASE1_EMBED_FLOW_VARIANT=my-embed-only bash run_phase1_embeds.sh
# or
FLOW_VARIANT=my-embed-only bash run_phase1_embeds.sh
# or 
SKIP_PLACE=1 bash run_phase1_embeds.sh
# SKIP_CTS=1 bash run_phase1_embeds.sh
```

### Step 1-4. Run baseline methods (Tables V & VI)

**All 5 baselines × 8 paper designs (recommended).** `run_baselines_all.sh`
chains every prior-work baseline — Kahng row-parity, Cell-scattering,
Buffer-insertion, ICMarks, AutoMarks — over the exact 8 designs in
`tab:ppa_ng45`/`tab:ppa_asap7` (NG45: JPEG, SweRV, Ariane, BP; ASAP7: JPEG,
SweRV, CVA6, Ariane). Each method auto-re-execs inside Singularity, embeds its
watermark on the reference ODB, runs `make wm_cts_and_route` (CTS + route +
finish), and verifies at DRT. This is 40 full ORFS flows, so run it detached:

```bash
nohup bash run_baselines_all.sh > logs/baselines_all.log 2>&1 &
# subsets:
BASELINES="kahng icmarks" bash run_baselines_all.sh   # pick methods
bash run_baselines_all.sh --only cell_scattering      # single method
SKIP_DONE=1 bash run_baselines_all.sh                 # skip finished runs
```

Outputs land under `experiments/{results,logs}/<plat>/<nickname>/baseline-<method>/`
(`baseline-{kahng,cellscatter,bufins,icmarks,automarks}`), exactly where
`phase1_ppa.py` looks. BP is handled by passing `DESIGN=bp_multi_top` (config)
with `DESIGN_NICKNAME=bp_multi` (results) — every `baselines/*/run.sh` honors
`DESIGN_NICKNAME` for all filesystem paths (see §2b-1).

**Legacy driver.** `run_phase1_baselines.sh` runs only Cell-scattering and
Buffer-insertion over the original 7-bench matrix and is kept for backward
compatibility:

```bash
bash run_phase1_baselines.sh           # cellscatter + bufins, 7 designs
bash run_phase1_baselines.sh --skip-bufins      # cell-scattering only
bash run_phase1_baselines.sh --skip-cellscatter # buffer-insertion only
SKIP_DONE=1 bash run_phase1_baselines.sh        # skip already-finished runs
```

Override the watermark capacity K (matched to PDMarks P-only by default):
```bash
BASELINE_K=64 bash run_baselines_all.sh
```

After the runs finish, refresh the PPA tables:
```bash
python3.11 phase1_ppa.py && python3.11 aggregate.py --what ppa && python3.11 render_tex.py
# baseline rows appear in results/phase1/tab_ppa_{nangate45,asap7}.tex
```

### Step 1-5. Compute Δ-PPA and P_c (tab:ppa_ng45, tab:ppa_asap7)

```bash
python3.11 phase1_ppa.py
# reads: flow/watermarking/{module}/logs/{plat}/{design}/{latest}/6_report.json
#        flow/watermarking/experiments/results/{plat}/{design}/{FLOW_VARIANT}/wm_*.csv
# writes: results/phase1/raw/ppa_*.json
python3.11 aggregate.py --what ppa
python3.11 render_tex.py
```

### Step 1-6. Compute watermark capacity (tab:capacity)

```bash
python3.11 phase1_capacity.py
# reads: watermarking/{module}/wm_log/{design}_run_*.log
#        routing_wrong_way/results/{plat}/{design}/{latest}/watermark_nets.txt
# writes: results/phase1/raw/capacity_*.json
```

### Step 1-7. Compute survival rates (tab:survival)

Verifies the watermark at four checkpoints (per all-stage flow):

| Checkpoint | ODB read | Dir |
|---|---|---|
| `post_place` | `3_place_order_wm.odb` | `embed_dir` |
| `post_cts`   | `4_cts_wm.odb`         | `embed_dir` |
| `post_grt`   | `5_1_grt.odb`          | `ppa_dir` (auto-discovered) |
| `post_drt`   | `5_route.odb`          | `ppa_dir` (auto-discovered) |

The script discovers `ppa_dir` by scanning `experiments/results/<plat>/<nick>/`
for any directory whose name starts with `pdmarks-all-stage` and contains
`5_route.odb`.  Most current runs consolidate everything into a single
`pdmarks-all-stage/` dir, so `embed_dir == ppa_dir`; older split layouts
(`pdmarks-all-stage` + `pdmarks-all-stage-routed`) are still handled
transparently.  Override with `PDMARKS_SURVIVAL_EMBED_VARIANT` /
`PDMARKS_SURVIVAL_ROUTED_VARIANT` if you need to pin a specific variant.

```bash
python3.11 phase1_survival.py
# writes: results/phase1/raw/survival_*.json
```

### Step 1-8. Aggregate and render

```bash
python3.11 aggregate.py --what ppa
python3.11 aggregate.py --what capacity
python3.11 aggregate.py --what survival
python3.11 render_tex.py
```

Or aggregate everything at once:
```bash
python3.11 aggregate.py    # all phases
python3.11 render_tex.py
```

**Phase 1 output files:**

| Paper table | LaTeX fragment |
|---|---|
| `tab:exp_setup` | `results/phase1/tab_exp_setup.tex` |
| `tab:capacity` | `results/phase1/tab_capacity.tex` |
| `tab:ppa_ng45` | `results/phase1/tab_ppa_nangate45.tex` |
| `tab:ppa_asap7` | `results/phase1/tab_ppa_asap7.tex` |
| `tab:survival` | `results/phase1/tab_survival.tex` |

---

## 4. Phase 2 — Security Analysis

### Step 2-1. Wrong-key null distribution (tab:wrong-key)

Generates N random 32-byte master keys and, for each, computes the four
per-stage evidence values plus the combined statistic:

| Column | Definition |
|---|---|
| `r_P`, `r_C` | placement / CTS extraction rates (paper Eqs. eq:placement_extraction / eq:cts_extraction) |
| `Z_R`, `p_R` | routing z-statistic and one-sided p-value (paper Eqs. eq:routing_z / eq:routing_pvalue) |
| `r_R` | `1 if p_R ≤ α_R else 0` (paper Eq. eq:routing_extraction) |
| `pc_all` | product of per-stage `P_c` (legacy combined coincidence probability) |
| `r_all` | **mean** of available per-stage extraction rates (paper Eq. eq:combined_extraction) |

Two false-positive rates are reported:

- `false_positive_rate_r_all` — fraction of wrong keys with `r_all ≥ true r_all`. **This matches the paper's `tab:wrong-key` definition.**
- `false_positive_rate_pc` — legacy: fraction of wrong keys with `pc_all ≤ true pc_all`.

**ASAP7 routing is skipped by default.** ASAP7's strict-direction router
produces zero wrong-way segments, pinning `Z_R = 0` and `p_R = 0.5` on every
trial.  Including that channel would only add uniform noise to `r_all` /
`pc_all`, so the script omits routing whenever `b.platform` is in
`--no-routing-platforms` (default: `asap7`).  The summary JSON records
`"routing_skipped": true` when this happens.

```bash
./sbpy wrong_key/run_wrong_key.py              # N=1000 (default)
./sbpy wrong_key/run_wrong_key.py -n 500       # smaller sweep
./sbpy wrong_key/run_wrong_key.py --fraction 0.03  # routing fraction override
./sbpy wrong_key/run_wrong_key.py --alpha-R 0.01   # tighter threshold for r_R
./sbpy wrong_key/run_wrong_key.py --no-routing-platforms ""  # include routing for ALL benches
```

Aggregate and render:
```bash
python3.11 aggregate.py --what wrong_key
python3.11 render_tex.py
# writes results/phase2/tab_wrong_key.tex
```

Generate the distribution figure:
```bash
./sbpy wrong_key/plot_wrong_key.py
# writes plots/wrong_key_analysis.png
```

### Step 2-2. Parameter sensitivity sweep (tab:sensitivity)

Paper §sensitivity (main.tex §6.x) sweeps six paper-named knobs on
SweRV × {NG45, ASAP7} and reports the resulting **capacity / extraction /
ΔPPA** tradeoffs.  Each cell runs the full per-module driver
(`drivers/run_{p,c,r}_only.sh`) so the embed, the verify, and the PPA
back-end all flow off a single command, with `FLOW_VARIANT=sens-<stage>-<knob>-<value>`
namespacing the outputs.

| Stage | Paper symbol | Env var | Default | Sweep values |
|---|---|---|---|---|
| Placement | `D_pair` | `WM_PAIR_DIST_UM` | 1.0 µm | `0.5, 1.0, 2.0` |
| Placement | `θ_HPWL` | `WM_HPWL_EPS_PAIR_DBU` | 100 DBU | `50, 100, 200` |
| Placement | `δ_guard` | `WM_GUARD_DEGRADE_NS` | 0.02 ns | `0.01, 0.02, 0.05` |
| CTS | sibling dist | `WM_CTS_SIBLING_DIST_UM` | 50 µm | `25, 50, 100` |
| Routing | `f` | `WATERMARK_FRACTION` | 0.05 | `0.025, 0.05, 0.10` |
| Routing | `λ_wm` | `WATERMARK_STRENGTH` | 100 | `10, 100, 1000` |

**Cell count**: 18 placement + 6 CTS + 6 routing (NG45 only; ASAP7
routing channel is structurally undefined under the strict-direction
router — set `SENS_ROUTE_ASAP7=1` to force-include) = **30 cells**.
Per-cell wall time on SweRV is roughly 20–60 min (full embed + CTS +
route + report).  Total: ≈ 15 h, overnight scale.

The runner is idempotent (skips cells whose `6_report.json` already
exists).  Force re-run with `SENS_FORCE=1`.

```bash
# 1. Run the full sweep (overnight).  Routing sweeps run unless SENS_ROUTE=0.
bash sensitivity/run_sensitivity.sh
# Skip routing entirely:
SENS_ROUTE=0 bash sensitivity/run_sensitivity.sh

# 2. Verify pass (fast) -- runs the per-stage verifier on each cell's embed
#    ODB to produce r_P / r_C / Z_R / p_R JSONs under
#    results/phase2/raw/sens_*.json.
#    NOTE: invoke with bare `python3.11`, NOT `./sbpy`.  The verifier shells
#    out to place_wm.sh / cts_wm.sh, which themselves call `singularity exec`;
#    nesting singularity (`./sbpy` -> singularity exec) breaks with rc=127.
python3.11 sensitivity/verify_sweep.py

# 3. Aggregate + render.
python3.11 sensitivity/aggregate_sensitivity.py
python3.11 render_tex.py
# writes results/phase2/sensitivity.csv  +  results/phase2/tab_sensitivity.tex
```

**Output CSV schema** (`results/phase2/sensitivity.csv`):
`platform, design, stage, knob, value, variant, eligible, selected,
r_P, r_C, Z_R, p_R, r_R, r_all, pass_{P,C,R}, num_pass, pass_all,
accept, dWNS_vs_ref, dTNS_vs_ref, dRWL_vs_ref, dPower_vs_ref,
dRuntime_vs_ref, note` — same r-rate / ownership-pass columns as the
attack CSVs so the same plotting code can pull from both.

---

## 5. Phase 3 — Attack Evaluation

### Step 3-1. Blind perturbation attack (tab:blind_attack)

Faithful implementation of paper §7.1 — for each `(design, stage, q_s)`:

| Stage | What the attacker does |
|---|---|
| `placement` | Reconstruct the public eligible co-row tuple pool via `lib.eligibility.reconstruct_placement_pool`. For q_P of those tuples: **swap 2-tuples**, apply a random non-identity permutation to 3-tuples. Then `dpl.detailedPlacement(WM_MAX_DISP_X, WM_MAX_DISP_Y, …)`. Writes `results/phase3/raw/atk_p_<plat>_<design>_qs<q>.odb`. |
| `cts` | Reconstruct the public LCB-pair pool via `build_proximity_pairs`. For q_C of those pairs: pick a mutable sequential sink on `L_A`'s output net, disconnect it, reconnect to `L_B`'s net (skipping dont_touch / repair-buffer / LCB-as-sink). Writes `results/phase3/raw/atk_c_<plat>_<design>_qs<q>.odb`. |
| `routing` | Pick q_R of eligible signal nets, then **invoke `run_attack_route.sh`** which (a) reloads `4_cts.odb`, (b) tags every WM net normally via `set_routing_watermark -key_hex`, (c) destroys the `dbBoolProperty "watermark"` on each net in `WM_NETS_ATTACK`, (d) re-runs the full `make wm_route_wrong_way`. Output: `experiments/results/<plat>/<nick>/atk-r-<design>-qs<q>/5_route.odb` + a real `6_report.json`. **Skipped for ASAP7 designs** (strict-direction router → zero wrong-way segments). |
| `all_stage` | Runs placement, CTS, and routing attacks on the same flow; reports combined `r_P / r_C / Z_R / p_R` and `r_all`. |

After each row the post-attack verifier produces `r_P` / `r_C` (or `Z_R / p_R`
for routing). Then `lib.thresholds.ownership_pass()` computes:

- `r_R = 1{p_R ≤ α_R}` (default α_R = 0.05),
- `r_all = mean(available_per_stage_rates)` (paper Eq. eq:combined_extraction),
- per-stage `pass_P / pass_C / pass_R` flags vs **τ_P = τ_C = 0.75**,
- final `accept` decision (`r_all ≥ τ_all = 0.5` AND ≥ 2 stages pass).

```bash
# Full sweep (10 benches × 4 stages × 5 q_s values)
./sbpy attacks/blind/run_blind_attack.py
# defaults: --qs-list 0.10,0.20,0.50,0.80,1.00
#           --stages placement,cts,routing,all_stage
#           --alpha-R 0.05  --fraction 0.05  --no-routing-platforms asap7

# Subset
./sbpy attacks/blind/run_blind_attack.py --designs aes,jpeg --stages placement,cts
./sbpy attacks/blind/run_blind_attack.py --qs-list 0.20,0.50 --stages routing
./sbpy attacks/blind/run_blind_attack.py --no-routing-platforms ""   # include ASAP7 routing
```

Outputs per `(bench, stage, q_s)` in `results/phase3/raw/`:

| File | Stage(s) | Contents |
|---|---|---|
| `blind_<plat>_<design>_<stage>_qs<q>.json` | all | `r_P, r_C, Z_R, p_R, r_R, r_all, pass_*, num_pass, pass_all, accept, atk_odb, note` |
| `atk_p_<plat>_<design>_qs<q>.odb` | placement, all_stage | attacked placed ODB |
| `atk_c_<plat>_<design>_qs<q>.odb` | cts, all_stage | attacked post-CTS ODB |
| `…/atk-r-<design>-qs<q>/5_route.odb` | routing, all_stage (NG45 only) | re-routed attacked ODB |
| `atk_r_<plat>_<design>_qs<q>_nets.txt` | routing | net list passed to `run_attack_route.sh` |

Aggregate and render:
```bash
python3.11 aggregate.py --what blind
python3.11 render_tex.py
# writes results/phase3/tab_blind_attack.tex
```

#### Step 3-1b. ΔPPA continuation (paper §7 PPA columns)

`run_blind_attack.py` reports extraction-rate metrics only. The placement and
CTS attacks need a separate back-end continuation to produce
`ΔWNS / ΔTNS / ΔPower / ΔrWL / attack_runtime`. The routing attack already
runs the full flow inside `run_attack_route.sh` — its row picks up the
resulting `6_report.json` automatically.

```bash
# 1) Preview which (design, stage, q_s) combos have an attacked ODB on disk.
#    --attack {all,blind,targeted} restricts to atk_{p,c}_*, atk_{tp,tc}_*,
#    or both (default: all).
python3.11 attacks/ppa/run_attack_ppa.py --dry-run
python3.11 attacks/ppa/run_attack_ppa.py --attack blind    --dry-run
python3.11 attacks/ppa/run_attack_ppa.py --attack targeted --dry-run

# 2) Re-run the back-end:  placement attack → CTS+route+finish via
#    place_ordering/run_ppa.sh;  CTS attack → route+finish via cts_v2/run_ppa.sh.
#    Long: minutes-to-hours per design.  Filter to taste.
./sbpy attacks/ppa/run_attack_ppa.py --qs 0.10,0.20
./sbpy attacks/ppa/run_attack_ppa.py --designs aes,jpeg --stages placement
./sbpy attacks/ppa/run_attack_ppa.py --attack blind    --skip-done
./sbpy attacks/ppa/run_attack_ppa.py --attack targeted --skip-done

# 3) Aggregate. Picks up all seven variant families (atk-p, atk-c, atk-r,
#    atk-tp, atk-tc, atk-tr, atk-all) automatically; stages map to
#    {placement, cts, routing, targeted_placement, targeted_cts,
#    targeted_routing, all_stage}.
python3.11 attacks/ppa/aggregate_attack_ppa.py
# writes results/phase3/blind_ppa.csv with columns:
#   platform, design, stage, q_s, attack_variant, wm_baseline_variant,
#   dWNS_vs_ref,  dTNS_vs_ref,  dRWL_vs_ref,  dPower_vs_ref,  dRuntime_vs_ref,
#   dWNS_vs_wm,   dTNS_vs_wm,   dRWL_vs_wm,   dPower_vs_wm,   dRuntime_vs_wm,
#   note
```

`*_vs_ref` columns are deltas vs the un-watermarked reference flow (total
PPA cost of watermark + attack). `*_vs_wm` columns are deltas vs the matching
un-attacked PDMarks variant (incremental cost of the attack alone).

See [Step 3-3 Troubleshooting](#step-3-3-troubleshooting-phase-3) below for the
caveats and known failure modes (routing-attack fidelity, all-stage chaining,
ASAP7 limitation, CTS timing repair).

### Step 3-2. Targeted ML attack (tab:targeted_attack)

Faithful implementation of paper §7.2. For each of the three stages:

1. **Build a labeled feature dataset**.
   - **Placement** positives = embed-CSV rows with `satisfied=True` and
     non-rejecting `skipped_reason`; **negatives** are inferred from the
     same CSV's rejected rows (or absent entirely — see single-class note
     below).
   - **CTS** positives = embed-CSV rows with non-rejecting `skipped_reason`;
     negatives = rows the embedder skipped (lcb_already_used etc.).
   - **Routing** positives = nets listed in `watermark_nets.txt`; negatives
     = other routable signal nets in `route_counts*.csv`.
2. **Train a `RandomForestClassifier`** (`n_estimators=400, class_weight="balanced"`),
   stratified 80/20 split when both classes have ≥ 2 samples.
3. **Report diagnostics**: AUC, precision-at-recall (≥ 0.5), and
   recall-at-top-K where K = q_s × |pool|.
4. **Convert into a real attack** by ranking the eligible objects by
   predicted P(WM) and perturbing the top-K with the same legal mutator
   the §7.1 blind attack uses:
   - **Routing**: drives `run_attack_route.sh` on the top-K, producing a
     real re-routed ODB at `experiments/results/<plat>/<nick>/atk-tr-<design>-qs<q>/`.
   - **Placement**: writes the top-K tuple-keys (`A_name|B_name[|C_name]`,
     same id scheme as `attacks/targeted/features.py`) to
     `results/phase3/raw/targeted_p_<plat>_<design>_qs<q>_tuples.txt`, then
     re-invokes `attack_placement.py` with `WM_TUPLES_ATTACK=<that file>`.
     The mutator filters its eligibility pool to the listed tuples (file
     order; `ATK_QS` is ignored) and writes
     `results/phase3/raw/atk_tp_<plat>_<design>_qs<q>.odb`.  Post-attack
     `r_P` is measured via `place_wm.sh verify_stages`.
   - **CTS**: analogous, writing top-K `pair_key`s (the embedder's
     `L_A+L_B` string) to `targeted_c_<plat>_<design>_qs<q>_pairs.txt`,
     re-invoking `attack_cts.py` with `WM_PAIRS_ATTACK=<file>`, output
     `atk_tc_<plat>_<design>_qs<q>.odb`.  `r_C` from `cts_wm.sh verify`.

   Before the ranked top-K is handed to the placement / CTS mutator, the
   driver runs `_spread_by_components()` over the keys.  The blind mutators
   refuse to touch the same cell (placement, `attack_placement.py:165`) or
   the same LCB (CTS, `attack_cts.py:147`) twice in one batch; without
   spreading, classifier-ranked top-Ks tend to concentrate collisions
   (high-fanout LCBs / dense cell rows rank similarly) and the mutator
   silently skips most of the requested K.  The spread does one pass in
   rank order, putting non-colliding keys at the front (the mutator
   mutates these in full) and demoting colliders to the back (the mutator
   skips them, exactly as it would have done unordered) -- so the top-K
   semantic is preserved subject to the mutator's feasibility constraint.

   The `perturbed` field in each `targeted_*.json` reports the **actually
   mutated** count parsed from the `[atk_p]` / `[atk_c]` summary line in
   the mutator log (`out_root/logs/{tp,tc}_<plat>_<design>_qs<q>_atk.log`),
   not the requested top-K size.  The requested K can be recovered as
   `recall_top_K * num_pos`.

   `ownership_pass` then fills `r_R / r_all / pass_{P,C,R} / accept`,
   matching the blind-attack row schema so the two tables read the same way.

> **Single-class fallback**: when an embed CSV has all-positive labels
> (the embedder only wrote accepted constraints — typical for placement),
> AUC / precision-at-recall become undefined.  The script logs
> `[targeted] single-class labels (n_pos=N, n_neg=0); using margin-based
> score, AUC/precision unavailable` and falls back to a per-feature
> z-score-magnitude ranking.  `recall_top_K` is still reported.

```bash
# Full sweep
./sbpy attacks/targeted/run_targeted_attack.py
# defaults: --qs-list 0.10,0.20,0.50,0.80,1.00
#           --stages placement,cts,routing
#           --alpha-R 0.05  --fraction 0.01  --no-routing-platforms asap7

# Subset
./sbpy attacks/targeted/run_targeted_attack.py --designs aes --stages routing
./sbpy attacks/targeted/run_targeted_attack.py --qs-list 0.10,0.20
```

Outputs per `(bench, stage, q_s)`:

| File | Contents |
|---|---|
| `results/phase3/raw/targeted_<plat>_<design>_<stage>_qs<q>.json` | `r_P, r_C, Z_R, p_R, r_R, r_all, pass_{P,C,R}, accept, auc, precision_at_recall, recall_top_K, perturbed, atk_odb, note` |
| `results/phase3/raw/atk_tp_<plat>_<design>_qs<q>.odb` | targeted placement attacked ODB (input to `run_attack_ppa.py` for ΔPPA) |
| `results/phase3/raw/atk_tc_<plat>_<design>_qs<q>.odb` | targeted CTS attacked ODB (input to `run_attack_ppa.py`) |
| `results/phase3/raw/targeted_p_<plat>_<design>_qs<q>_tuples.txt` | ranked top-K placement tuple keys passed to `attack_placement.py` |
| `results/phase3/raw/targeted_c_<plat>_<design>_qs<q>_pairs.txt`  | ranked top-K CTS LCB pair_keys passed to `attack_cts.py` |
| `experiments/results/<plat>/<nick>/atk-tr-<design>-qs<q>/5_route.odb` | re-routed targeted ODB (routing stage, NG45 only) |
| `experiments/logs/<plat>/<nick>/atk-tr-<design>-qs<q>/6_report.json` | post-attack ORFS metrics (routing) |
| `results/phase3/raw/atk_tr_<plat>_<design>_qs<q>_counts.csv` | dumped per-net (wrong_way, total) on the attacked ODB |
| `results/phase3/raw/targeted_r_<plat>_<design>_qs<q>_nets.txt` | net list passed to `run_attack_route.sh` |

ΔPPA continuation for the targeted placement / CTS attacks (the routing one
already self-completes):

```bash
# Run only the targeted (atk_tp_* / atk_tc_*) back-end queue; --skip-done
# is idempotent.  --attack {blind,targeted,all} selects the family.
./sbpy attacks/ppa/run_attack_ppa.py --attack targeted --skip-done
# --designs aes --stages placement --qs 0.1,0.2
```

Aggregate and render:
```bash
python3.11 aggregate.py --what targeted             # widened schema
python3.11 attacks/ppa/aggregate_attack_ppa.py      # adds targeted_* rows
python3.11 render_tex.py
# writes results/phase3/tab_targeted_attack.tex (extraction + ΔPPA columns)
```

> **ΔPPA**: targeted placement / CTS / routing rows all flow into
> `attacks/ppa/aggregate_attack_ppa.py` under variant prefixes
> `atk-tp-` / `atk-tc-` / `atk-tr-` and stages
> `targeted_placement` / `targeted_cts` / `targeted_routing`.  Each is
> compared against the matching unattacked watermark baseline
> (`pdmarks-p-only` / `pdmarks-c-only` / `pdmarks-r-only`) for the
> `*_vs_wm` deltas in `blind_ppa.csv`.

---

### Step 3-3. Troubleshooting (Phase 3)

Symptoms you may see and what they mean:

| Symptom | Cause | Resolution |
|---|---|---|
| Routing attack aborts with `Error: invalid command name "odb::dbBoolProperty_destroy"` | Outdated `routing_wrong_way/attack_route_pre.tcl` | The SWIG-bound destroy is on the base class: use `odb::dbProperty_destroy` (already patched in this repo). |
| Routing attack aborts with `Error: invalid command name "attack_route_pre"` | `[attack_route_pre]` inside a Tcl double-quoted `puts` triggers command substitution | The TCL prefix should be a bare string (already patched). |
| Routing attack aborts with `[ERROR GUI-0077] QStandardPaths: error creating runtime directory '/run/user/<uid>'` at `final_report.tcl:82` | The optional GUI snapshot step needs writable `$XDG_RUNTIME_DIR`, which is read-only inside the Singularity image | `final_report.tcl` now wraps `gui::show` in `catch`; the snapshot warning is non-fatal. |
| Targeted-routing JSON has `auc=`, `precision_at_recall=` empty but `recall_top_K` populated | Single-class labels (typically placement embed CSVs that contain only accepted constraints) | Expected — `[targeted] single-class labels …` is logged.  The recall-at-top-K diagnostic still indicates whether the margin-based ranking concentrates positives at the top. |
| ASAP7 routing rows are blank | Strict-direction router → zero wrong-way segments → Z_R / p_R structurally undefined | Expected.  ASAP7 routing is skipped by `--no-routing-platforms asap7`; set this to `""` to force-include at the cost of meaningless rows. |
| `attacks/ppa/aggregate_attack_ppa.py` reports `0 rows` despite a completed run | Earlier bug where the scan walked the platform directory instead of the design directory | Patched.  If it still happens, verify `experiments/logs/<plat>/<nick>/atk-*/6_report.json` exists. |
| Placement attack reports `r_P` close to 1.0 even at q_s=1.0 | Pre-§7 ±1-site shift attack — too gentle | Replaced by the eligible-tuple swap/permute + legalize implementation.  Confirm by running on aes: `r_P` should drop to ~0.6–0.9 at q_s=0.5. |

> **Caveats** (paper §7 vs. implementation)
> - **Routing attack fidelity**: clear-tag + re-`detailed_route` is the closest OpenROAD primitive to "locally reroute the selected nets without the wrong-way penalty." A surgical per-net rip-up does not exist in DRT.
> - **all-stage attack**: perturbs each stage independently from the unperturbed input of the previous stage (not chained through legalize → re-CTS → attacked-CTS). A truly chained attack is a follow-up.
> - **ASAP7 routing**: skipped (see above).
> - **CTS incremental timing repair**: best-effort; some q_s=1.0 cases leave slack violations that surface as ΔTNS in the PPA continuation.

---

## 6. Full pipeline (one shot)

```bash
cd OR0415/OpenROAD-flow-scripts/flow/watermarking/experiments

# ── Phase 1 ──────────────────────────────────────────────────────────────────
# 1a. Run all watermarked flows (hours; run overnight or in parallel)
for bench in \
    "nangate45 aes            watermarking-test1" \
    "nangate45 jpeg           watermarking-test1" \
    "nangate45 swerv_wrapper  base" \
    "nangate45 ariane136      base_tcp3p5" \
    "nangate45 bp_multi_top   base_tcp3p2" \
    "asap7     aes            base" \
    "asap7     jpeg           base_tcp540" \
    "asap7     swerv_wrapper  base_tcp1455" \
    "asap7     ariane         base_fixed" \
    "asap7     cva6           base_tcp950"; do
  read -r plat dsgn var <<< "$bench"
  DESIGN=$dsgn PLATFORM=$plat WM_FLOW_VARIANT=$var bash drivers/run_all_stage.sh
done

# 1b. Baseline methods
SKIP_DONE=1 bash run_phase1_baselines.sh

# 1c. Analysis (minutes)
python3.11 phase1_ppa.py
python3.11 phase1_capacity.py
python3.11 phase1_survival.py
python3.11 aggregate.py

# ── Phase 2 ──────────────────────────────────────────────────────────────────
./sbpy wrong_key/run_wrong_key.py -n 1000
./sbpy wrong_key/plot_wrong_key.py
bash sensitivity/run_sensitivity.sh
python3.11 sensitivity/verify_sweep.py
python3.11 sensitivity/aggregate_sensitivity.py
python3.11 aggregate.py --what wrong_key

# ── Phase 3 ──────────────────────────────────────────────────────────────────
# Blind attack: 10 benches × 4 stages × 5 q_s; placement+CTS run in minutes,
# routing runs detail_route (hours).  ASAP7 routing is skipped automatically.
./sbpy attacks/blind/run_blind_attack.py

# Targeted attack: same q_s sweep; routing top-K triggers run_attack_route.sh.
./sbpy attacks/targeted/run_targeted_attack.py

# ΔPPA continuation for placement / CTS attacks only (routing self-completed).
# Long: minutes-to-hours per (design, stage, q_s) -- use --dry-run first.
python3.11 attacks/ppa/run_attack_ppa.py --dry-run
./sbpy attacks/ppa/run_attack_ppa.py --skip-done

# Aggregate everything (atk-p, atk-c, atk-r, atk-tr, atk-all variants).
python3.11 attacks/ppa/aggregate_attack_ppa.py
python3.11 aggregate.py --what blind
python3.11 aggregate.py --what targeted

# ── Render all LaTeX fragments ────────────────────────────────────────────────
python3.11 render_tex.py
cat results/tables_index.md    # see where each table's fragment lives
```

---

## 7. Runtime overrides

### Change the reference flow root

```bash
export ORFS_FLOW_HOME=/path/to/other/OpenROAD-flow-scripts/flow
```

All helpers in `lib/orfs.py` derive their paths from this variable.

### Pin a specific watermarked variant instead of auto-discovering the latest

By default `find_latest_wm_variant()` picks the most recently completed run
(highest-mtime `6_report.json`). Override per module:

| Module | Environment variable |
|---|---|
| `place_ordering` | `WM_VARIANT_PLACE_ORDERING` |
| `cts_v2` | `WM_VARIANT_CTS_V2` |
| `routing_wrong_way` | `WM_VARIANT_ROUTING_WRONG_WAY` |

Example — pin the routing variant and use an alternate flow root:
```bash
ORFS_FLOW_HOME=/scratch/orfs/flow \
WM_VARIANT_ROUTING_WRONG_WAY=pdmarks-r-only-20240510_143000 \
python3.11 phase1_ppa.py
```

### Skip already-finished baseline runs

```bash
SKIP_DONE=1 bash run_phase1_baselines.sh
```

### Override watermark capacity K for all baselines

```bash
BASELINE_K=80 bash run_phase1_baselines.sh
```

### Disable adaptive PDMarks parameters

The PDMarks experiment drivers adapt placement/CTS/routing parameters from the
reference timing by default.  Disable this and use script defaults or explicit
environment overrides with:

```bash
PDMARKS_ADAPTIVE_PARAMS=0 bash drivers/run_c_only.sh
```

You can also override only one parameter and leave the rest adaptive:

```bash
WM_CTS_NUM_PAIRS=16 bash drivers/run_c_only.sh
```

### Include routing points in the sensitivity sweep

```bash
SENS_ROUTE=1 bash sensitivity/run_sensitivity.sh
```

---

## 8. Output reference

### Raw JSON (produced by analysis scripts)

| Glob | Contents |
|---|---|
| `results/phase1/raw/ppa_*.json` | Per-(design, method) Δ-PPA + P_c |
| `results/phase1/raw/capacity_*.json` | Eligible / selected counts per stage |
| `results/phase1/raw/survival_*.json` | Extraction rates at 4 checkpoints |
| `results/phase2/raw/wrong_key_*.json` | True `r_P / r_C / r_R / pc_all / r_all` + two FPRs |
| `results/phase2/raw/wrong_key_*_dist.csv` | N-row distribution: `idx, r_P, r_C, Z_R, p_R, r_R, pc_all, r_all` |
| `results/phase3/raw/blind_*.json` | Post-attack extraction rates (`r_P / r_C / Z_R / p_R`) |
| `results/phase3/raw/atk_p_*.odb / atk_c_*.odb` | Attacked ODBs for placement / CTS attacks |
| `results/phase3/raw/atk_r_*_counts.csv` | Attacked route counts for routing attacks |
| `results/phase3/raw/targeted_*.json` | Targeted attack `Z_R / p_R` + `precision_topK` |
| `experiments/logs/<plat>/<nick>/atk-{p,c}-<design>-qs<q>/6_report.json` | Post-attack ORFS reports (produced by `attacks/ppa/run_attack_ppa.py`) |

### Aggregated CSVs (produced by `aggregate.py`)

| File | Contents |
|---|---|
| `results/phase1/summary_ref.csv` | Reference PPA for all 10 designs |
| `results/phase1/ppa_nangate45.csv` | Δ-PPA table, NanGate45 |
| `results/phase1/ppa_asap7.csv` | Δ-PPA table, ASAP7 |
| `results/phase1/capacity.csv` | Capacity table |
| `results/phase1/survival.csv` | Survival table |
| `results/phase2/wrong_key.csv` | Wrong-key summary |
| `results/phase2/sensitivity.csv` | Sensitivity sweep summary |
| `results/phase3/blind.csv` | Blind attack: extraction-rate summary |
| `results/phase3/blind_ppa.csv` | Blind attack: Δ-PPA (vs ref **and** vs wm baseline) |
| `results/phase3/targeted.csv` | Targeted attack summary |

### LaTeX fragments (produced by `render_tex.py`)

| Paper table | Fragment file |
|---|---|
| `tab:exp_setup` | `results/phase1/tab_exp_setup.tex` |
| `tab:capacity` | `results/phase1/tab_capacity.tex` |
| `tab:ppa_ng45` | `results/phase1/tab_ppa_nangate45.tex` |
| `tab:ppa_asap7` | `results/phase1/tab_ppa_asap7.tex` |
| `tab:survival` | `results/phase1/tab_survival.tex` |
| `tab:wrong-key` | `results/phase2/tab_wrong_key.tex` |
| `tab:blind_attack` | `results/phase3/tab_blind_attack.tex` |
| `tab:targeted_attack` | `results/phase3/tab_targeted_attack.tex` |
| `fig:wrong_key_analysis` | `plots/wrong_key_analysis.png` |

---

## 9. Cryptographic invariants

The system follows the **Kerckhoffs principle**: security rests on key secrecy, not algorithm secrecy.

### Seed derivation

All three watermark stages share a common keying structure:

```
master_key (32 bytes, from gen_key/sign_and_derive.py)
  └─ seed_placement = SHA256(master_key || b"placement")
  └─ seed_cts       = SHA256(master_key || b"cts")
  └─ seed_routing   = SHA256(master_key || b"routing")
```

### Routing net selection

Net `n` is watermarked iff:

```
HMAC-SHA256(seed_routing, b"net\0" + n)[0:4] / 2^32  <  fraction
```

The Python verifier in `lib/keyless_verify.routing_wm_set` mirrors the C++ implementation in `OpenROAD/src/wmk/` byte-for-byte.

### Null-distribution validation

Under a random wrong key the routing test produces `Z_R ~ N(0,1)` and
`p_R ~ Uniform[0,1]`, confirming the security argument holds empirically.
For NG45/aes over 500 wrong keys: `Z_R ∈ [-2.84, 2.97]`, mean ≈ -0.09;
`p_R ∈ [0.0015, 0.998]`, mean ≈ 0.524, roughly uniform across 5 quintile
buckets.  Per-stage extraction rates also center on their random baselines:
mean `r_P ≈ 0.495`, mean `r_C ≈ 0.498`.

**ASAP7 does not exercise this channel.** Its strict-direction router
produces zero wrong-way segments, so the two-proportion z-test denominator
is zero and `Z_R / p_R` collapse to `0 / 0.5` deterministically.  The
wrong-key script (and the survival / attack scripts) skip the routing
channel entirely for ASAP7 — only `r_P` and `r_C` contribute to `r_all` /
`pc_all` for those benches.

---

## 10. Baseline methods (Tables V & VI)

Five prior-work baselines are implemented for comparison, one per row of
`tab:ppa_ng45`/`tab:ppa_asap7`. Each has its own `baselines/<name>/{embed,verify}.py`
and a `run.sh` that auto-re-execs inside Singularity, embeds on the reference
ODB, runs `make wm_cts_and_route` (CTS + route + finish), and verifies at DRT.
All five honor `DESIGN_NICKNAME` for filesystem paths (so BP works:
`DESIGN=bp_multi_top`, `DESIGN_NICKNAME=bp_multi`).

| Method | Ref | Variant | Source ODB | Selection domain |
|---|---|---|---|---|
| Kahng (row-parity) | KahngMMP98/LMM01 | `baseline-kahng` | `3_place.odb` | `kahng_row\0` + inst |
| Cell-scattering | Cai et al. ISIC'07 | `baseline-cellscatter` | `3_place.odb` | `cellscatter\0` + inst |
| Buffer-insertion | Sun et al. ISQED'06 | `baseline-bufins` | `4_cts.odb` | `bufins\0` + net |
| ICMarks | Zhang et al. TCAD'25 | `baseline-icmarks` | `3_place.odb` | min-score region + keyed cells |
| AutoMarks | Zhang et al. MLCAD'24 | `baseline-automarks` | `3_place.odb` | node-score heuristic + keyed cells |

- **Kahng**: select K cells; shift y by ±1 row pitch to set row-index parity; legalize.
- **Cell-scattering**: shift each selected cell ±1 site to set X-column parity; legalize.
- **Buffer-insertion**: insert one extra buffer to flip each net's buffer-count parity; legalize.
- **ICMarks / AutoMarks**: post-DP re-implementations (the original GW region search /
  GNN scorer require DREAMPlace); select a low-cost region/center, then ±1-site DW shifts
  on K keyed cells. See each `embed.py` docstring for the faithfulness notes.

All five select `K = capacity_for(...)` matched to the PDMarks P-only accepted
pair count (`baselines/_common.py`), so capacity is held equal across methods.
Override for every design with `export BASELINE_K=<int>`.

### Baseline runbook

```bash
cd OR0415/OpenROAD-flow-scripts/flow/watermarking/experiments

# 1. Run all 5 baselines × 8 paper designs = 40 flows (detached; hours)
nohup bash run_baselines_all.sh > logs/baselines_all.log 2>&1 &
#    subsets: BASELINES="kahng icmarks" bash run_baselines_all.sh
#             bash run_baselines_all.sh --only cell_scattering
#             SKIP_DONE=1 bash run_baselines_all.sh

# 2. Compute Δ-PPA + P_c (also updates PDMarks rows if not yet done)
python3.11 phase1_ppa.py

# 3. Aggregate into per-platform CSVs
python3.11 aggregate.py --what ppa

# 4. Render LaTeX row fragments
python3.11 render_tex.py
# → results/phase1/tab_ppa_nangate45.tex  (paste baseline rows into tab:ppa_ng45)
# → results/phase1/tab_ppa_asap7.tex      (paste baseline rows into tab:ppa_asap7)
```

> The legacy `run_phase1_baselines.sh` (cell-scattering + buffer-insertion only,
> 7-bench matrix) is kept for backward compatibility; prefer `run_baselines_all.sh`.

### Baseline full-flow survival (tab:survival_baseline)

`baseline_survival.py` measures each baseline's extraction rate `r = accepted/K`
at the four PD checkpoints (post-place / post-CTS / post-GRT / post-DRT) by
running each method's own `verify.py` (read-only) on the checkpoint ODBs the
baseline flow already left on disk — **no OpenROAD flow re-runs**. Run after
`run_baselines_all.sh`:

```bash
python3.11 baseline_survival.py                      # all 5 methods, 8 designs
python3.11 baseline_survival.py --methods buffer_insertion   # subset
# writes results/phase1/baseline_survival.csv (upserted per cell)
```

Checkpoint→ODB: placement baselines use `3_place_<suffix>.odb` / `4_cts.odb` /
`5_1_grt.odb` / `5_route.odb`; buffer-insertion (CTS-stage) uses
`4_cts_bufins.odb` for post-CTS and has `n/a` at post-place. A cell is
`no_claims` (→ n/a) when the embedder committed no surviving watermark (e.g.
AutoMarks whose region heuristic was too small). The table is hand-built into
`tab:survival_baseline` in main.tex from this CSV.

<!-- /home/yil375/.claude/projects/-home-fetzfs-projects-MISC-ytliu-watermarking/3560d185-3505-4d4d-8deb-712b9c904224.json
-->