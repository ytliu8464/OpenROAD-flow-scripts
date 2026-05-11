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
├── bench_matrix.py               # Active 7-cell + paper 10-cell matrix
├── sbpy                          # Shim: picks Singularity Python w/ sklearn/matplotlib
│
├── lib/
│   ├── orfs.py                   # Path resolution, 6_report.json / *.log readers
│   ├── pc.py                     # P_c  (Eqs. pc_stage / pc_total)
│   ├── route_stat.py             # Z_R / p_R (Eqs. routing_stat)
│   ├── keys.py                   # SHA-256 seed derivation; HMAC-SHA256 net selection
│   └── keyless_verify.py         # Placement / CTS / routing extraction-rate functions
│
├── drivers/
│   ├── _common.sh                # Shared env setup, ensure_keys()
│   ├── run_ref.sh                # Unmodified reference ORFS flow
│   ├── run_p_only.sh             # Placement watermark + PPA continuation
│   ├── run_c_only.sh             # CTS watermark + PPA continuation
│   ├── run_r_only.sh             # Routing wrong-way bias + PPA continuation
│   └── run_all_stage.sh          # Chained P → C → R + PPA
│
├── baselines/
│   ├── _common.py                # Capacity lookup, HMAC object selection
│   ├── cell_scattering/          # Cai et al. ISIC'07
│   │   ├── embed.py
│   │   ├── verify.py
│   │   └── run.sh
│   └── buffer_insertion/         # Sun et al. ISQED'06
│       ├── embed.py
│       ├── verify.py
│       └── run.sh
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
│   ├── blind/
│   │   ├── attack_placement.py   # Random cell shuffle attacker
│   │   ├── attack_cts.py         # Random LCB parity flip attacker
│   │   ├── attack_routing.py     # Random wrong-way count perturbation
│   │   └── run_blind_attack.py   # Driver: all stages × all q_s values
│   └── targeted/
│       ├── features.py           # Layout feature extraction
│       └── run_targeted_attack.py# RF classifier → ranked perturbation
│
├── tools/
│   ├── dump_route_counts.py      # OpenROAD-Python: per-net (wrong_way, total) CSV
│   └── dump_route_counts.sh      # Wrapper for dump_route_counts.py
│
├── phase1_capacity.py            # Parse embed logs → raw/capacity_*.json
├── phase1_ppa.py                 # Δ-PPA + P_c per variant → raw/ppa_*.json
├── phase1_survival.py            # Verify watermark at 4 post-stage checkpoints
├── run_phase1_embeds.sh          # Fast embed-only pass (no full PPA round-trip)
├── run_phase1_baselines.sh       # Run all baseline methods for all 7 designs
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

The 7 active benches must have a completed reference ORFS run (`6_report.json` on disk).
These are already present. To add a new design later:

```bash
DESIGN=my_design PLATFORM=nangate45 WM_FLOW_VARIANT=base \
  bash drivers/run_ref.sh
```

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

**All 7 designs in a loop:**

```bash
for bench in \
    "nangate45 aes            watermarking-test1" \
    "nangate45 jpeg           watermarking-test1" \
    "nangate45 swerv_wrapper  base" \
    "nangate45 ariane136      base_tcp3p5" \
    "asap7     aes            base" \
    "asap7     jpeg           base_tcp540" \
    "asap7     swerv_wrapper  base_tcp1455"; do
  read -r plat dsgn var <<< "$bench"
  DESIGN=$dsgn PLATFORM=$plat WM_FLOW_VARIANT=$var bash drivers/run_all_stage.sh
done
```

**Artifact locations after a run:**

| Artifact | Location |
|---|---|
| Embed/verify CSVs, watermarked ODBs (`3_place_order_wm_v2.odb`, `4_cts_wm.odb`) | `flow/results/{plat}/{design}/{WM_FLOW_VARIANT}/` |
| PPA stage logs, `6_report.json` | `flow/watermarking/{module}/logs/{plat}/{design}/{FLOW_VARIANT}/` |
| Post-GRT/DRT ODBs (`5_1_grt.odb`, `5_route.odb`, `6_final.odb`) | `flow/watermarking/{module}/results/{plat}/{design}/{FLOW_VARIANT}/` |
| Routing artifacts (`watermark_nets.txt`, `route_counts.csv`) | `flow/watermarking/routing_wrong_way/results/{plat}/{design}/{FLOW_VARIANT}/` |

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
| `run_all_stage.sh` | all three | `pdmarks-all-stage` / `pdmarks-all-stage-routed` | fixed |
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

### Step 1-3. Embed-only pass (capacity only, no full PPA run)

If you only need the embed CSVs and capacity counts — without running the
full ORFS flow — this is much faster (seconds per design):

```bash
bash run_phase1_embeds.sh
```

### Step 1-4. Run baseline methods (Tables V & VI)

Cell-scattering and buffer-insertion baselines for all 7 designs:

```bash
bash run_phase1_baselines.sh           # both baselines, all 7 designs
bash run_phase1_baselines.sh --skip-bufins      # cell-scattering only
bash run_phase1_baselines.sh --skip-cellscatter # buffer-insertion only
SKIP_DONE=1 bash run_phase1_baselines.sh        # skip already-finished runs
```

Override the watermark capacity K (matched to PDMarks P-only by default):
```bash
BASELINE_K=64 bash run_phase1_baselines.sh
```

### Step 1-5. Compute Δ-PPA and P_c (tab:ppa_ng45, tab:ppa_asap7)

```bash
python3.11 phase1_ppa.py
# reads: flow/watermarking/{module}/logs/{plat}/{design}/{latest}/6_report.json
#        flow/results/{plat}/{design}/{WM_FLOW_VARIANT}/wm_*.csv
# writes: results/phase1/raw/ppa_*.json
```

### Step 1-6. Compute watermark capacity (tab:capacity)

```bash
python3.11 phase1_capacity.py
# reads: watermarking/{module}/wm_log/{design}_run_*.log
#        routing_wrong_way/results/{plat}/{design}/{latest}/watermark_nets.txt
# writes: results/phase1/raw/capacity_*.json
```

### Step 1-7. Compute survival rates (tab:survival)

Verifies the watermark at four checkpoints: `post_place`, `post_cts`,
`post_grt`, `post_drt`.

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

Generates N random 32-byte master keys and computes `r_P`, `r_C`, `Z_R`,
`p_R` for each key. Reports the empirical false-positive rate under H₀
(fraction of wrong keys whose combined P_c is as small as the true key's).

```bash
./sbpy wrong_key/run_wrong_key.py              # N=1000 (default)
./sbpy wrong_key/run_wrong_key.py -n 200       # quick smoke test
./sbpy wrong_key/run_wrong_key.py --fraction 0.03  # routing fraction override
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

### Step 2-2. Parameter sensitivity sweep

Sweeps three knobs per stage on SweRV NG45 and SweRV ASAP7:

| Stage | Knobs swept |
|---|---|
| Placement | pairs-per-tile (2/4/8), grid size (4/6/8), HPWL tolerance (50/100/200) |
| CTS | pair count (16/32/64), sibling distance (25/50/100 µm) |
| Routing | watermark fraction (0.025/0.05/0.10), wrong-way strength (10/100/1000) |

```bash
bash sensitivity/run_sensitivity.sh          # placement + CTS sweeps
SENS_ROUTE=1 bash sensitivity/run_sensitivity.sh  # also routing sweeps (slower)
```

Aggregate:
```bash
./sbpy sensitivity/aggregate_sensitivity.py
python3.11 render_tex.py
```

---

## 5. Phase 3 — Attack Evaluation

### Step 3-1. Blind perturbation attack (tab:blind_attack)

Randomly perturbs a fraction `q_s` of watermark objects (cells / LCBs / nets)
**without any knowledge of the key** and measures the residual extraction rates.

```bash
./sbpy attacks/blind/run_blind_attack.py
# default: q_s ∈ {0.01, 0.05, 0.10, 0.20}, stages = placement, cts, routing

./sbpy attacks/blind/run_blind_attack.py --qs-list 0.05,0.10
./sbpy attacks/blind/run_blind_attack.py --stages routing
```

Aggregate and render:
```bash
python3.11 aggregate.py --what blind
python3.11 render_tex.py
# writes results/phase3/tab_blind_attack.tex
```

### Step 3-2. Targeted ML attack (tab:targeted_attack)

Trains a Random Forest classifier on public-information layout features
(no key access) to rank likely watermarked objects, then perturbs the
top-K candidates.

```bash
./sbpy attacks/targeted/run_targeted_attack.py
# default: q_s ∈ {0.01, 0.05, 0.10, 0.20}, routing stage only

./sbpy attacks/targeted/run_targeted_attack.py --qs-list 0.05,0.10
```

Aggregate and render:
```bash
python3.11 aggregate.py --what targeted
python3.11 render_tex.py
# writes results/phase3/tab_targeted_attack.tex
```

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
    "asap7     aes            base" \
    "asap7     jpeg           base_tcp540" \
    "asap7     swerv_wrapper  base_tcp1455"; do
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
./sbpy sensitivity/aggregate_sensitivity.py
python3.11 aggregate.py --what wrong_key

# ── Phase 3 ──────────────────────────────────────────────────────────────────
./sbpy attacks/blind/run_blind_attack.py
./sbpy attacks/targeted/run_targeted_attack.py
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
| `results/phase2/raw/wrong_key_*.json` | True P_c + false-positive rate |
| `results/phase2/raw/wrong_key_*_dist.csv` | Full N-row P_c distribution |
| `results/phase3/raw/blind_*.json` | Post-attack extraction rates |
| `results/phase3/raw/targeted_*.json` | Targeted attack Z_R / p_R + precision |

### Aggregated CSVs (produced by `aggregate.py`)

| File | Contents |
|---|---|
| `results/phase1/summary_ref.csv` | Reference PPA for all 7 designs |
| `results/phase1/ppa_nangate45.csv` | Δ-PPA table, NanGate45 |
| `results/phase1/ppa_asap7.csv` | Δ-PPA table, ASAP7 |
| `results/phase1/capacity.csv` | Capacity table |
| `results/phase1/survival.csv` | Survival table |
| `results/phase2/wrong_key.csv` | Wrong-key summary |
| `results/phase2/sensitivity.csv` | Sensitivity sweep summary |
| `results/phase3/blind.csv` | Blind attack summary |
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
`p_R ~ Uniform[0,1]`, confirming the security argument holds empirically
(200 trials on AES NG45: mean Z = 0.099, sd = 1.057; mean p = 0.473).

---

## 10. Baseline methods (Tables V & VI)

Two baseline watermarking methods are implemented for comparison.

### Cell scattering — Cai et al. ISIC'07 (`baselines/cell_scattering/`)

- **Embed**: loads `3_place.odb`, selects K cells via HMAC-SHA256(`seed_placement`, `"cellscatter\0" + name`), shifts each cell ±1 site to set X-column parity, re-legalizes.
- **Flow continuation**: `make wm_cts_and_route` → `flow/results/.../baseline-cellscatter/`
- **Verify**: re-reads current X-column parity from ODB; compares against embed CSV.

### Buffer insertion — Sun et al. ISQED'06 (`baselines/buffer_insertion/`)

- **Embed**: loads `4_cts.odb`, selects K signal nets via HMAC-SHA256(`seed_routing`, `"bufins\0" + name`), inserts one extra buffer to flip each net's buffer-count parity, re-legalizes.
- **Flow continuation**: `make wm_route_wrong_way` → `flow/results/.../baseline-bufins/`
- **Verify**: counts current buffers on selected nets; checks parity.

### Capacity K (matched to PDMarks P-only accepted pair count)

| Design | Platform | K source |
|---|---|---|
| AES | NG45 | `wm_place_order_embed_v2.csv` row count |
| JPEG | NG45 | `wm_place_order_embed_v2.csv` row count |
| SweRV | NG45 | `wm_place_order_embed_v2.csv` row count |
| Ariane | NG45 | `wm_place_order_embed_v2.csv` row count |
| AES | ASAP7 | `wm_place_order_embed_v2.csv` row count |
| JPEG | ASAP7 | ~129 (from embed CSV) |
| SweRV | ASAP7 | ~80 (from embed CSV) |

Override all designs: `export BASELINE_K=64`

### Baseline runbook (4 commands)

```bash
cd OR0415/OpenROAD-flow-scripts/flow/watermarking/experiments

# 1. Run 14 baseline flows (7 designs × 2 baselines; ~same wall-time as P-only)
bash run_phase1_baselines.sh

# 2. Compute Δ-PPA + P_c (also updates PDMarks rows if not yet done)
python3.11 phase1_ppa.py

# 3. Aggregate into per-platform CSVs
python3.11 aggregate.py --what ppa

# 4. Render LaTeX row fragments
python3.11 render_tex.py
# → results/phase1/tab_ppa_nangate45.tex  (paste into Table V)
# → results/phase1/tab_ppa_asap7.tex      (paste into Table VI)
```
