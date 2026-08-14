# Routing Watermark

Embeds ownership evidence in the routing direction statistics of a keyed subset
of signal nets. Unlike the placement and CTS stages, this one is not a per-object
bit. It is a population-level bias produced by the detailed router, and it is
verified with a statistical test rather than an exact match.

The key comes from [`../gen_key/`](../gen_key/) as `seed_routing.hex`.

## Requirements

This stage needs an OpenROAD build that provides four extra Tcl commands.

| Command | Description |
| ----- | ----- |
| `set_routing_watermark -key_hex <hex> -fraction <f>` | Tag the keyed net subset. |
| `set_routing_watermark_strength <lambda>` | Wrong-way cost multiplier in DRT. |
| `report_routing_watermark -p <p>` | Post-route summary. |
| `clear_routing_watermark` | Drop all tags. |

```{warning}
These commands are not in upstream OpenROAD. They live in `src/wmk/` on the
`watermarking` branch of <https://github.com/ytliu8464/OpenROAD.git>, which is
what the `tools/OpenROAD` submodule points at. Placement and CTS watermarking do
not need them and run on a stock OpenROAD build.
```

```bash
# From the repository root: fetch the fork and build it.
git submodule update --init --recursive OpenROAD-flow-scripts/tools/OpenROAD
cd OpenROAD-flow-scripts/tools/OpenROAD && ./etc/Build.sh
export OPENROAD_EXE=$PWD/build/src/openroad
```

`git submodule update --init` checks out the pinned commit
`0d9d73ffba0228f1a7263953fb9b41de800ba301`, which is the revision this tree was
developed and tested against. To follow the branch tip instead:

```bash
git submodule update --remote OpenROAD-flow-scripts/tools/OpenROAD
```

Confirm the commands are present before running this stage.

```bash
echo 'puts [info commands set_routing_watermark]' | "$OPENROAD_EXE" -no_init
# prints "set_routing_watermark" on a PDMarks build, an empty line on a stock one
```

## What the watermark is

**Selection.** Net `n` is watermarked if and only if

```
HMAC-SHA256(seed_routing, b"net\0" + n)[0:4]  /  2^32   <   f
```

read as a little-endian uint32, where `f` is `WATERMARK_FRACTION`. This is the
same rule the C++ side applies, mirrored in Python by
`experiments/lib/keyless_verify.routing_wm_set`.

**Carrier.** During detailed routing, tagged nets pay an increased penalty for
routing in the non-preferred direction of a layer, controlled by
`set_routing_watermark_strength`. Selected nets therefore use less wrong-way
routing than the rest of the design. The observable per-net statistic is the
wrong-way wirelength fraction

```
q_R(n) = l_ww(n) / l_tot(n)
```

computed on canonicalized geometry, with overlapping collinear wire intervals
merged and vias excluded, so it does not depend on how the router happened to
split route records.

**Evidence.** The statistic is the difference in mean `q_R` between the
watermarked set and the rest of the eligible set,

```
T_R = mean_{n in WM_R} q_R(n)  -  mean_{n in E_R \ WM_R} q_R(n)
```

A more negative `T_R` means stronger evidence. Its significance comes from a
net-level randomization test. Draw `B` uniform k-subsets of `E_R`, where
`k = |WM_R|`, from a stream seeded by the public design id, and report

```
p_R = (1 + #{ b : T_R^(b) <= T_R }) / (B + 1)
```

The null depends only on `k` and the fixed `q_R` vector, so wrong-key trials at
the same `k` reuse one null table. The smallest reportable `p_R` is `1/(B+1)`.
When the watermarked nets carry zero wrong-way wirelength, `exact_tail_log10`
gives the exact combinatorial tail instead of the Monte-Carlo floor.

## Files

| File | Description |
| ----- | ----- |
| `run.sh` | Embed the watermark, then run detail_route and finish. |
| `pre_route_watermark.tcl` | Pre-GRT hook: tag nets, set strength, dump `watermark_nets.txt`. |
| `post_route_watermark.tcl` | Post-DRT hook: `report_routing_watermark`. |
| `run_attack_route.sh` | Attack driver for the paper's Section VII.A. |
| `attack_route_pre.tcl` | Pre-DRT hook: tag normally, then clear tags on the attacker's net list. |
| `reroute_experiment.tcl` | Surgical against full reroute, for removal and timing studies. |
| `surgical_reroute.tcl` | Reroute only the watermark nets on a routed ODB. |

The keyed primitives live in [`../wm_prf.py`](../wm_prf.py), shared with the
placement and CTS stages.

## Example script

```bash
export DESIGN=jpeg PLATFORM=nangate45 WM_FLOW_VARIANT=base
./run.sh
```

`run.sh` generates the key bundle on demand, exports the two ORFS hook
variables, and runs the `wm_route_wrong_way` make target, which is
`copy_inputs_v2 route finish` starting from the reference `4_cts.odb`.

## Parameters

| Variable | Description | Default |
| ----- | ----- | ----- |
| `DESIGN`, `PLATFORM`, `WM_FLOW_VARIANT` | Identify the reference run. | *required* |
| `DESIGN_NICKNAME` | On-disk ORFS name. | `DESIGN` |
| `FLOW_VARIANT` | Output variant. | `pdmarks-r-only` |
| `WATERMARK_FRACTION` | `f`, the fraction of signal nets selected. | 0.02 |
| `WATERMARK_STRENGTH` | `lambda_wm`, the wrong-way cost multiplier. | 100.0 |
| `WATERMARK_P` | Cutoff passed to `report_routing_watermark`. | 0.4 |
| `CTS_ODB` | Explicit post-CTS ODB to start from. | Derived from the reference run. |
| `OWNER_ID` | Identity recorded in the key bundle. | `pdmarks-owner` |

Outputs land in `experiments/results/<platform>/<design>/<FLOW_VARIANT>/`. They
include `watermark_nets.txt`, the committed list of tagged nets, and
`wm_route_params.json`, which records `f`, `lambda_wm` and the report cutoff.

## Verification

Verification has two steps. Dump the per-net statistic from the routed ODB, then
run the randomization test against the keyed net set.

```bash
cd ../experiments

# 1. Per-net (ww_len, tot_len) from the routed ODB.
WM_ODB=/path/to/5_route.odb \
WM_QR_CSV=/tmp/route_qr.csv \
  bash tools/dump_route_qr.sh

# 2. T_R and p_R against the keyed net set.
python3 - <<'EOF'
from pathlib import Path
from lib.route_stat import per_net_qr, read_watermark_nets, route_stat_from_qr
counts = per_net_qr(Path("/tmp/route_qr.csv"))
wm     = read_watermark_nets(Path(".../watermark_nets.txt"))
st = route_stat_from_qr(counts, wm, design_id="jpeg")
print(f"T_R={st.T_R:.6f}  p_R={st.p_R:.3e}  k={st.k}  |E_R|={st.n_eligible}")
EOF
```

`watermark_nets.txt` is a convenience record. A verifier holding the key does
not need it. The `lib.keyless_verify.routing_wm_set(seed, net_names, fraction)`
function reconstructs `WM_R` from the seed alone, which makes this stage
key-recoverable rather than CSV-dependent.

### Where `f` comes from

`routing_wm_set` needs `fraction`, so key-only reconstruction works only if `f`
is recorded. It is recorded in two places.

- `wm_route_params.json`, written into `${WM_RESULTS}` by `run.sh` on every run
  as `{"f": …, "lambda_wm": …, "p_report": …}`.
- The watermark certificate, which seals `f` and `lambda_wm` into its metadata.
  See [`../certificate/`](../certificate/). `verify_ownership.py` reads `f` from
  there, so ownership verification needs no side channel.

Earlier revisions recorded `f` only in this stage's `wm_log/` transcript, which
is gitignored, and the analysis scripts compensated by hard-coding it: `0.01` in
`wrong_key/run_wrong_key.py` and `0.05` in `sensitivity/verify_sweep.py`. Prefer
the recorded value over those defaults.

## Limitations

- ASAP7's strict-direction router produces no wrong-way wirelength, so `q_R` is
  identically zero and `T_R` and `p_R` are structurally undefined. The harness
  skips the routing channel on ASAP7 by default.
- `run.sh` defaults `f` to `0.02` while `pre_route_watermark.tcl` falls back to
  `0.05` when the variable is empty, so an out-of-band invocation can differ
  silently. Read `f` back from `wm_route_params.json` rather than assuming it.

## License

BSD 3-Clause License. See the [LICENSE](../../../LICENSE_BUILD_RUN_SCRIPTS) file.

<!-- ## Attack driver

`run_attack_route.sh` reproduces the paper's Section VII.A routing attack: tag
every net normally, then clear the `watermark` property on the attacker's chosen
subset so detailed routing lays those nets out without the bias.

```bash
DESIGN=jpeg PLATFORM=nangate45 WM_FLOW_VARIANT=base \
FLOW_VARIANT=atk-r-jpeg-qs0.50 \
WM_NETS_ATTACK=/path/to/attack_nets.txt \
  ./run_attack_route.sh
```

Because OpenROAD has no per-net rip-up primitive, this re-runs `detail_route`
over the whole design. `reroute_experiment.tcl` exists to measure how much
cheaper a surgical reroute of only the watermark nets would be
(`MODE=surgical` against `MODE=full`).
-->
