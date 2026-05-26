#!/usr/bin/env python3.11
# SPDX-License-Identifier: BSD-3-Clause
"""Run PPA continuation on attacked watermarked ODBs.

run_blind_attack.py writes attacked ODBs to experiments/results/phase3/raw/:

  atk_p_<plat>_<design>_qs<q>.odb   (placement attack -> post-placement ODB)
  atk_c_<plat>_<design>_qs<q>.odb   (cts attack       -> post-CTS ODB)
  atk_r_<plat>_<design>_qs<q>_counts.csv   (routing attack: no ODB)

This script discovers each attacked ODB and continues the ORFS back-end:
  placement attack -> CTS + route + finish via place_ordering/run_ppa.sh
  cts attack       -> route + finish via cts_v2/run_ppa.sh
  routing attack   -> SKIPPED (no ODB perturbation; PPA is undefined)

Each continuation writes its own ORFS results / logs under FLOW_VARIANT
``atk-<initial>-<design>-qs<q>``, producing a fresh 6_report.json that
aggregate_attack_ppa.py then reads to compute dWNS/dTNS/dRWL/dPower/dRuntime
vs the unattacked watermarked baseline (pdmarks-p-only or pdmarks-c-only).

Filters:
  --designs aes,jpeg          only run these designs (matches DESIGN_NAME)
  --stages  placement,cts     restrict to specific stages (default: both)
  --qs      0.05,0.10         restrict to specific q_s values
  --attack  {all,blind,targeted}
                              all  : process atk_{p,c}_*  AND atk_{tp,tc}_*
                              blind: process atk_{p,c}_*  only (§7.1)
                              targeted: process atk_{tp,tc}_* only (§7.2)
                              default: all
  --dry-run                   print the commands without running them
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Optional

HERE = Path(__file__).resolve().parents[2]   # .../watermarking/experiments
sys.path.insert(0, str(HERE))
from bench_matrix import ACTIVE_BENCHES, Bench
from lib.orfs import FLOW_HOME, experiment_logs, experiment_results

RAW_DIR        = HERE / "results" / "phase3" / "raw"
PLACE_RUN_PPA  = FLOW_HOME / "watermarking" / "place_ordering" / "run_ppa.sh"
CTS_RUN_PPA    = FLOW_HOME / "watermarking" / "cts_v2"        / "run_ppa.sh"

# Filename pattern for placement / CTS attacks.
#   blind §7.1:   atk_p_<plat>_<design>_qs<q>.odb   /   atk_c_<plat>_<design>_qs<q>.odb
#   targeted §7.2: atk_tp_<plat>_<design>_qs<q>.odb /   atk_tc_<plat>_<design>_qs<q>.odb
# (Routing attacks self-complete via run_attack_route.sh, so they don't need a
# separate PPA continuation step.)  Longer alternatives come first so the
# regex doesn't shadow "tp" / "tc" with the leading "p" / "c".
_ATK_RX = re.compile(
    r"^atk_(?P<initial>tp|tc|p|c)_(?P<plat>[^_]+)_(?P<design>.+)_qs(?P<qs>[0-9.eE+-]+)\.odb$"
)

# Map ODB-name initial -> (stage, attack_mode) for downstream filtering /
# logging.  ``stage`` selects which back-end continuation runs;
# ``attack_mode`` lets the CLI --attack flag include/exclude families.
_INITIAL_INFO = {
    "p":  ("placement", "blind"),
    "c":  ("cts",       "blind"),
    "tp": ("placement", "targeted"),
    "tc": ("cts",       "targeted"),
}


def _find_bench(platform: str, design: str) -> Optional[Bench]:
    for b in ACTIVE_BENCHES:
        if b.platform == platform and (b.design == design
                                       or b.design_nickname == design):
            return b
    return None


def _variant_label(stage: str, design: str, q_s: str, *,
                    attack_mode: str = "blind") -> str:
    base = "p" if stage == "placement" else "c"
    prefix = ("t" + base) if attack_mode == "targeted" else base
    return f"atk-{prefix}-{design}-qs{q_s}"


def discover_attacked_odbs(raw_dir: Path = RAW_DIR):
    """Yield (Bench, stage, q_s_str, attacked_odb_path, attack_mode).

    ``attack_mode`` is "blind" for atk_p_* / atk_c_* and "targeted" for
    atk_tp_* / atk_tc_*.
    """
    if not raw_dir.is_dir():
        return
    for p in sorted(raw_dir.glob("atk_*.odb")):
        m = _ATK_RX.match(p.name)
        if not m:
            continue
        plat   = m.group("plat")
        design = m.group("design")
        q_s    = m.group("qs")
        stage, attack_mode = _INITIAL_INFO[m.group("initial")]
        b = _find_bench(plat, design)
        if b is None:
            # Filenames may use either b.design or b.design_nickname.
            # Try once more by stripping platform-design as a single key.
            print(f"[skip] cannot resolve bench for {p.name}", file=sys.stderr)
            continue
        yield b, stage, q_s, p, attack_mode


def _continue_placement(b: Bench, q_s: str, attacked_odb: Path,
                         dry_run: bool, *, attack_mode: str = "blind") -> int:
    """Continue CTS + route + finish from an attacked post-placement ODB."""
    variant = _variant_label("placement", b.design, q_s, attack_mode=attack_mode)
    wm_results = experiment_results(b.platform, b.design_nickname, variant)
    wm_results.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "DESIGN":           b.design,
        "DESIGN_NICKNAME":  b.design_nickname,
        "PLATFORM":         b.platform,
        "WM_FLOW_VARIANT":  b.wm_flow_variant,
        "FLOW_VARIANT":     variant,
        "WM_RESULTS":       str(wm_results),
        "DP_ODB":           str(attacked_odb),
    }
    cmd = ["bash", str(PLACE_RUN_PPA)]
    print(f"[run-p {attack_mode}] {b.platform}/{b.design} qs={q_s}")
    print(f"        WM_RESULTS={wm_results}")
    print(f"        DP_ODB     ={attacked_odb}")
    if dry_run:
        return 0
    return subprocess.run(cmd, env=env).returncode


def _continue_cts(b: Bench, q_s: str, attacked_odb: Path,
                  dry_run: bool, *, attack_mode: str = "blind") -> int:
    """Continue route + finish from an attacked post-CTS ODB."""
    variant = _variant_label("cts", b.design, q_s, attack_mode=attack_mode)
    wm_results = experiment_results(b.platform, b.design_nickname, variant)
    wm_results.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "DESIGN":           b.design,
        "DESIGN_NICKNAME":  b.design_nickname,
        "PLATFORM":         b.platform,
        "WM_FLOW_VARIANT":  b.wm_flow_variant,
        "FLOW_VARIANT":     variant,
        "WM_RESULTS":       str(wm_results),
        "CTS_ODB":          str(attacked_odb),
    }
    cmd = ["bash", str(CTS_RUN_PPA)]
    print(f"[run-c {attack_mode}] {b.platform}/{b.design} qs={q_s}")
    print(f"        WM_RESULTS={wm_results}")
    print(f"        CTS_ODB    ={attacked_odb}")
    if dry_run:
        return 0
    return subprocess.run(cmd, env=env).returncode


def _filter(values: Iterable[str], allowed_csv: Optional[str]) -> set:
    if not allowed_csv:
        return None  # type: ignore[return-value]
    return {v.strip() for v in allowed_csv.split(",") if v.strip()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--designs", default="",
                    help="comma-separated DESIGN_NAMEs to include (default: all)")
    ap.add_argument("--stages",  default="placement,cts",
                    help="which stages' attacked ODBs to process")
    ap.add_argument("--qs",      default="",
                    help="comma-separated q_s values to include (default: all)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would run, do not invoke the flow")
    ap.add_argument("--skip-done", action="store_true",
                    help="skip combos that already have a 6_report.json")
    ap.add_argument("--attack", default="all",
                    choices=("all", "blind", "targeted"),
                    help="filter ODB families: blind picks atk_{p,c}_*, "
                         "targeted picks atk_{tp,tc}_*, all picks both "
                         "(default: all)")
    args = ap.parse_args()

    want_designs = _filter([], args.designs)
    want_stages  = {s.strip() for s in args.stages.split(",") if s.strip()}
    want_qs      = _filter([], args.qs)

    pending = []
    for b, stage, q_s, odb, attack_mode in discover_attacked_odbs():
        if args.attack != "all" and attack_mode != args.attack:
            continue
        if want_designs and b.design not in want_designs and b.design_nickname not in want_designs:
            continue
        if stage not in want_stages:
            continue
        if want_qs and q_s not in want_qs:
            continue
        variant = _variant_label(stage, b.design, q_s, attack_mode=attack_mode)
        if args.skip_done:
            rep = experiment_logs(b.platform, b.design_nickname, variant) / "6_report.json"
            if rep.exists():
                print(f"[skip-done] {b.platform}/{b.design}/{variant}")
                continue
        pending.append((b, stage, q_s, odb, attack_mode))

    if not pending:
        print("[run_attack_ppa] nothing to do")
        return 0
    print(f"[run_attack_ppa] {len(pending)} attack-PPA runs queued "
          f"(--attack={args.attack})")

    pass_n = fail_n = 0
    for b, stage, q_s, odb, attack_mode in pending:
        if stage == "placement":
            rc = _continue_placement(b, q_s, odb, args.dry_run,
                                     attack_mode=attack_mode)
        elif stage == "cts":
            rc = _continue_cts(b, q_s, odb, args.dry_run,
                               attack_mode=attack_mode)
        else:
            print(f"[skip] {stage} attack has no ODB", file=sys.stderr)
            continue
        if rc == 0:
            pass_n += 1
        else:
            fail_n += 1
            print(f"[FAIL] rc={rc} for {b.platform}/{b.design}/{stage}/qs{q_s} "
                  f"({attack_mode})", file=sys.stderr)
    print(f"[run_attack_ppa] done: {pass_n} ok, {fail_n} failed")
    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
