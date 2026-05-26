#!/usr/bin/env python3.11
# SPDX-License-Identifier: BSD-3-Clause
"""Run blind attacks across all active benches and q_s values (paper §7.1).

For each (design, stage, q_s):
  * placement -> attack_placement.py (eligible-tuple swap/permute + legalize)
  * cts       -> attack_cts.py       (sink-move between proximity LCB pairs)
  * routing   -> attack_routing.py + run_attack_route.sh
                  (clear WM tag on chosen nets, re-detail_route)
  * all_stage -> chain placement -> CTS -> routing on the same flow

Routing is *skipped* on ASAP7 designs by default (ASAP7's strict-direction
router produces zero wrong-way segments, making Z_R / p_R structurally
undefined).  Override with --no-routing-platforms "".

For each row we report r_P, r_C, Z_R, p_R, r_R, r_all, and the ownership
decision (paper Tab. wrong-key thresholds).  PPA deltas are produced by the
decoupled ``attacks/ppa/run_attack_ppa.py`` step on the attacked ODBs.

JSON output: results/phase3/raw/blind_<plat>_<design>_<stage>_qs<q>.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE))
from bench_matrix import ACTIVE_BENCHES
from lib.keys import load_seed_hex
from lib.keyless_verify import routing_wm_set
from lib.orfs import (
    FLOW_HOME, flow_results, experiment_results,
    wm_module_results, find_latest_wm_variant,
)
from lib.route_stat import read_counts_csv, route_stat_from_counts
from lib.thresholds import ownership_pass


OPENROAD_EXE = os.environ.get(
    "OPENROAD_EXE",
    "/home/fetzfs_projects/MISC-ytliu/watermarking/OR0415/OpenROAD/build/bin/openroad")
SIF = os.environ.get("SINGULARITY_SIF",
                     "/home/tool/singularity/images/ispd26.sif")

# subprocess passes env=... directly to the child but resolves the executable
# name against the *parent's* os.environ PATH (POSIX execvp semantics).  If
# this script is launched in an environment where singularity isn't on PATH,
# fall back to a known absolute path.
import shutil as _shutil
SINGULARITY = _shutil.which("singularity") or "/usr/local/bin/singularity"


def or_python(script: Path, env: dict, log: Optional[Path] = None) -> int:
    cmd = [SINGULARITY, "exec", "-B", "/home", SIF, OPENROAD_EXE,
           "-python", "-exit", str(script)]
    if log is None:
        return subprocess.run(cmd, env={**os.environ, **env}).returncode
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w") as f:
        return subprocess.run(cmd, env={**os.environ, **env},
                              stdout=f, stderr=subprocess.STDOUT).returncode


def _run_logged(cmd, env: dict, log: Path) -> int:
    """Run a subprocess teeing stdout+stderr into ``log``. Returns rc."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w") as f:
        return subprocess.run(cmd, env={**os.environ, **env},
                              stdout=f, stderr=subprocess.STDOUT).returncode


# ---------------------------------------------------------------------------
# Verify-CSV parsers (post-attack r_P / r_C from the place_wm / cts_wm verifiers)
# ---------------------------------------------------------------------------

def _parse_placement_verify(v_csv: Path):
    """Return r_P from place_wm.sh verify_stages CSV.  Handles 3 formats."""
    if not v_csv.exists():
        return None
    try:
        rows = list(csv.DictReader(open(v_csv)))
    except Exception:
        return None
    if not rows:
        return None
    fields = rows[0].keys()
    ok = tot = 0
    if "constraints_ok" in fields and "constraints_total" in fields:
        for r in rows:
            try:
                ok  += int(r.get("constraints_ok", 0))
                tot += int(r.get("constraints_total", 0))
            except (TypeError, ValueError):
                pass
    elif "metric" in fields and "value" in fields:
        summary = {r["metric"]: r["value"] for r in rows if r.get("metric") is not None}
        try:
            ok  = int(summary.get("pairs_ok", 0)) + int(summary.get("groups_ok", 0))
            tot = int(summary.get("pairs_checked", 0)) + int(summary.get("groups_checked", 0))
        except (TypeError, ValueError):
            return None
    elif "ok" in fields or "satisfied" in fields:
        for r in rows:
            v = r.get("ok") or r.get("satisfied")
            if v is None:
                continue
            tot += 1
            if str(v).strip().lower() in ("true", "1"):
                ok += 1
    else:
        return None
    return (ok / tot) if tot > 0 else None


def _parse_cts_verify(v_csv: Path):
    if not v_csv.exists():
        return None
    try:
        rows = list(csv.DictReader(open(v_csv)))
    except Exception:
        return None
    ok = tot = 0
    for r in rows:
        v = (r.get("satisfied") or r.get("post_drt_satisfied")
             or r.get("post_final_satisfied") or r.get("ok"))
        if v is None or v == "":
            continue
        tot += 1
        if str(v).strip().lower() in ("true", "1"):
            ok += 1
    return (ok / tot) if tot > 0 else None


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def _pick_embed_dir(plat, nick, wm_flow_variant):
    """Prefer the consolidated all-stage dir; fall back to legacy flow/results/."""
    all_stage = experiment_results(plat, nick, "pdmarks-all-stage")
    legacy    = flow_results(plat, nick, wm_flow_variant)

    p_all = all_stage / "wm_place_order_embed_all_stage.csv"
    c_all = all_stage / "wm_cts_pairs_embed_all_stage.csv"
    if p_all.exists() or c_all.exists():
        return (all_stage,
                "3_place_order_wm.odb", p_all,
                "4_cts_wm.odb",         c_all)
    return (legacy,
            "3_place_order_wm_v2.odb", legacy / "wm_place_order_embed_v2.csv",
            "4_cts_wm.odb",            legacy / "wm_cts_pairs_embed.csv")


def _route_counts_csv(embed_dir: Path, b) -> Optional[Path]:
    """Return the per-net (wrong_way, total) CSV for this bench, or None."""
    # All-stage consolidated layout
    for cand in ("route_counts_5_route.csv", "route_counts.csv"):
        p = embed_dir / cand
        if p.exists():
            return p
    # Legacy routing_wrong_way module dir
    route_var = find_latest_wm_variant("routing_wrong_way", b.platform, b.design_nickname)
    if route_var:
        route_dir = wm_module_results("routing_wrong_way", b.platform,
                                       b.design_nickname, route_var)
        for cand in ("route_counts_5_route.csv", "route_counts.csv"):
            p = route_dir / cand
            if p.exists():
                return p
    return None


# ---------------------------------------------------------------------------
# Per-stage attack drivers
# ---------------------------------------------------------------------------

def _log_path(out_root: Path, b, stage_tag: str, q_s, kind: str) -> Path:
    return out_root / "logs" / f"{stage_tag}_{b.platform}_{b.design}_qs{q_s}_{kind}.log"


def _attack_placement(b, q_s, out_root, embed_dir, in_odb, embed_csv,
                       *, out_prefix: str = "p", extra_env: Optional[dict] = None):
    """Run attack_placement.py and the placement verifier on the result.

    ``out_prefix`` controls the ODB filename family (``p`` for blind §7.1,
    ``tp`` for targeted §7.2).  ``extra_env`` is merged into the OpenROAD
    child env -- the targeted driver uses it to pass
    ``WM_TUPLES_ATTACK=<key file>``, which makes attack_placement.py ignore
    ``ATK_QS`` and perturb the listed tuples instead.
    """
    out_odb = out_root / f"atk_{out_prefix}_{b.platform}_{b.design}_qs{q_s}.odb"
    if not in_odb.exists():
        return None, out_odb, f"no placement odb at {in_odb}"
    atk_log = _log_path(out_root, b, out_prefix, q_s, "atk")
    env = {"WM_ODB": str(in_odb), "WM_OUT_ODB": str(out_odb),
           "ATK_QS": str(q_s)}
    if extra_env:
        env.update(extra_env)
    rc = or_python(HERE / "attacks" / "blind" / "attack_placement.py",
                   env, log=atk_log)
    # If the legalizer aborted we still trust the ODB: the new attack_placement
    # writes the perturbed (un-legalized) layout on its way out.
    if not out_odb.exists():
        return None, out_odb, f"attack_placement.py failed (rc={rc}, log={atk_log})"
    if not embed_csv.exists():
        return None, out_odb, f"placement embed CSV missing: {embed_csv}"
    v_csv = out_root / f"atk_{out_prefix}_{b.platform}_{b.design}_qs{q_s}_verify.csv"
    ver_log = _log_path(out_root, b, out_prefix, q_s, "verify")
    sh = FLOW_HOME / "watermarking" / "place_ordering" / "place_wm.sh"
    _run_logged(["bash", str(sh), "verify_stages"],
                env={"WM_CELL_LIST":      str(embed_csv),
                     "WM_VERIFY_STAGES":  f"atk:{out_odb}",
                     "WM_STAGE_REPORT":   str(v_csv)},
                log=ver_log)
    r_P = _parse_placement_verify(v_csv)
    note = "" if r_P is not None else (
        f"verify_stages produced no usable v_csv (log={ver_log})")
    if rc != 0:
        note = (note + "; " if note else "") + f"atk_rc={rc} log={atk_log}"
    return r_P, out_odb, note


def _attack_cts(b, q_s, out_root, embed_dir, in_odb, embed_csv,
                *, out_prefix: str = "c", extra_env: Optional[dict] = None):
    """Run attack_cts.py and the CTS verifier on the result.

    ``out_prefix`` is ``c`` for blind §7.1, ``tc`` for targeted §7.2.
    ``extra_env`` lets the targeted driver pass ``WM_PAIRS_ATTACK=<file>``,
    which switches attack_cts.py into selective-pair mode (ignores ATK_QS).
    """
    out_odb = out_root / f"atk_{out_prefix}_{b.platform}_{b.design}_qs{q_s}.odb"
    if not in_odb.exists():
        return None, out_odb, f"no cts odb at {in_odb}"
    atk_log = _log_path(out_root, b, out_prefix, q_s, "atk")
    env = {"WM_ODB": str(in_odb), "WM_OUT_ODB": str(out_odb),
           "ATK_QS": str(q_s)}
    if extra_env:
        env.update(extra_env)
    rc = or_python(HERE / "attacks" / "blind" / "attack_cts.py",
                   env, log=atk_log)
    if rc != 0 or not out_odb.exists():
        return None, out_odb, f"attack_cts.py failed (rc={rc}, log={atk_log})"
    if not embed_csv.exists():
        return None, out_odb, f"cts embed CSV missing: {embed_csv}"
    v_csv = out_root / f"atk_{out_prefix}_{b.platform}_{b.design}_qs{q_s}_verify.csv"
    ver_log = _log_path(out_root, b, out_prefix, q_s, "verify")
    sh = FLOW_HOME / "watermarking" / "cts_v2" / "cts_wm.sh"
    _run_logged(["bash", str(sh), "verify"],
                env={"WM_CELL_LIST":        str(embed_csv),
                     "WM_CTS_VERIFY_INPUT": str(out_odb),
                     "WM_CTS_VERIFY_CSV":   str(v_csv)},
                log=ver_log)
    r_C = _parse_cts_verify(v_csv)
    note = "" if r_C is not None else (
        f"cts_wm.sh verify produced no usable v_csv (log={ver_log})")
    return r_C, out_odb, note


def _attack_routing(b, q_s, out_root, embed_dir, sr, fraction: float, no_route_plats):
    """Run the routing rip-up+reroute attack.

    Returns (Z_R, p_R, atk_odb_path_or_None, note).
    """
    if b.platform in no_route_plats:
        return None, None, None, f"routing skipped for platform {b.platform}"

    rc_in = _route_counts_csv(embed_dir, b)
    if rc_in is None:
        return None, None, None, "no route_counts*.csv on disk"

    # 1) Pick the attack net subset.
    atk_list = out_root / f"atk_r_{b.platform}_{b.design}_qs{q_s}_nets.txt"
    pick_log = _log_path(out_root, b, "r", q_s, "pick")
    rc = _run_logged(
        ["python3.11", str(HERE / "attacks" / "blind" / "attack_routing.py")],
        env={"WM_ROUTE_COUNTS_IN":   str(rc_in),
             "WM_NETS_ATTACK_OUT":   str(atk_list),
             "ATK_QS":               str(q_s)},
        log=pick_log)
    if rc != 0 or not atk_list.exists():
        return None, None, None, f"attack_routing.py failed (rc={rc}, log={pick_log})"

    # 2) Re-route via run_attack_route.sh.  Output ODB lands under
    #    experiments/results/<plat>/<nick>/<flow_variant>/.
    flow_variant = f"atk-r-{b.design}-qs{q_s}"
    wm_results = experiment_results(b.platform, b.design_nickname, flow_variant)
    wm_results.mkdir(parents=True, exist_ok=True)
    sh = FLOW_HOME / "watermarking" / "routing_wrong_way" / "run_attack_route.sh"
    route_log = _log_path(out_root, b, "r", q_s, "route")
    rc2 = _run_logged(["bash", str(sh)],
                      env={"DESIGN":          b.design,
                           "DESIGN_NICKNAME": b.design_nickname,
                           "PLATFORM":        b.platform,
                           "WM_FLOW_VARIANT": b.wm_flow_variant,
                           "FLOW_VARIANT":    flow_variant,
                           "WM_RESULTS":      str(wm_results),
                           "WM_NETS_ATTACK":  str(atk_list),
                           "WATERMARK_FRACTION": str(fraction)},
                      log=route_log)
    if rc2 != 0:
        return None, None, None, f"run_attack_route.sh failed (rc={rc2}, log={route_log})"

    # 3) Dump per-net (wrong_way, total) on the new ODB and run the z-test
    #    using the true owner key.
    routed_odb = wm_results / "5_route.odb"
    if not routed_odb.exists():
        return None, None, None, f"no 5_route.odb at {routed_odb}"
    rc_out = out_root / f"atk_r_{b.platform}_{b.design}_qs{q_s}_counts.csv"
    dump_log = _log_path(out_root, b, "r", q_s, "dump")
    _run_logged([str(HERE / "tools" / "dump_route_counts.sh")],
                env={"WM_ODB":         str(routed_odb),
                     "WM_COUNTS_CSV": str(rc_out)},
                log=dump_log)
    if not rc_out.exists():
        return None, None, routed_odb, (
            f"dump_route_counts.sh produced no CSV (log={dump_log})")

    counts = read_counts_csv(rc_out)
    wm = routing_wm_set(sr, counts.keys(), fraction)
    st = route_stat_from_counts(counts, wm)
    return st.Z_R, st.p_R, routed_odb, ""


# ---------------------------------------------------------------------------
# Per-bench, per-stage dispatch
# ---------------------------------------------------------------------------

def attack_one(b, stage: str, q_s: float, out_root: Path,
               no_route_plats, fraction: float, alpha_R: float):
    nick = b.design_nickname
    (embed_dir, p_odb_name, p_embed_csv,
     c_odb_name, c_embed_csv) = _pick_embed_dir(b.platform, nick, b.wm_flow_variant)

    seeds_dir = FLOW_HOME / "watermarking" / "gen_key" / "out" / b.design
    sp = load_seed_hex(seeds_dir / "seed_placement.hex")
    sc = load_seed_hex(seeds_dir / "seed_cts.hex")
    sr = load_seed_hex(seeds_dir / "seed_routing.hex")

    rec: dict = {
        "platform": b.platform, "design": b.design, "stage": stage, "q_s": q_s,
        "r_P": "", "r_C": "", "Z_R": "", "p_R": "",
        "atk_odb": "", "note": "",
    }

    if stage == "placement":
        rP, atk_odb, note = _attack_placement(
            b, q_s, out_root, embed_dir, embed_dir / p_odb_name, p_embed_csv)
        if rP is not None: rec["r_P"] = rP
        rec["atk_odb"] = str(atk_odb)
        rec["note"] = note
    elif stage == "cts":
        rC, atk_odb, note = _attack_cts(
            b, q_s, out_root, embed_dir, embed_dir / c_odb_name, c_embed_csv)
        if rC is not None: rec["r_C"] = rC
        rec["atk_odb"] = str(atk_odb)
        rec["note"] = note
    elif stage == "routing":
        ZR, pR, atk_odb, note = _attack_routing(
            b, q_s, out_root, embed_dir, sr, fraction, no_route_plats)
        if ZR is not None: rec["Z_R"] = ZR
        if pR is not None: rec["p_R"] = pR
        rec["atk_odb"] = str(atk_odb) if atk_odb else ""
        rec["note"] = note
    elif stage == "all_stage":
        # Chained P -> C -> R on the same flow.  Each stage writes its own
        # attacked ODB; the verifier metrics in the record reflect the
        # corresponding post-attack measurement of THAT stage.  PPA deltas
        # are produced by attacks/ppa/run_attack_ppa.py.
        notes = []
        rP, atk_p, n1 = _attack_placement(
            b, q_s, out_root, embed_dir, embed_dir / p_odb_name, p_embed_csv)
        if rP is not None: rec["r_P"] = rP
        if n1: notes.append("P:" + n1)
        rC, atk_c, n2 = _attack_cts(
            b, q_s, out_root, embed_dir, embed_dir / c_odb_name, c_embed_csv)
        if rC is not None: rec["r_C"] = rC
        if n2: notes.append("C:" + n2)
        ZR, pR, atk_r, n3 = _attack_routing(
            b, q_s, out_root, embed_dir, sr, fraction, no_route_plats)
        if ZR is not None: rec["Z_R"] = ZR
        if pR is not None: rec["p_R"] = pR
        if n3: notes.append("R:" + n3)
        rec["atk_odb"] = str(atk_p)  # placement ODB is the primary all-stage artifact
        rec["note"] = "; ".join(notes)
    else:
        rec["note"] = f"unknown stage: {stage}"
        return rec

    # r_all + ownership decision per paper Tab. wrong-key.
    rP_v = rec["r_P"] if isinstance(rec["r_P"], (int, float)) else None
    rC_v = rec["r_C"] if isinstance(rec["r_C"], (int, float)) else None
    pR_v = rec["p_R"] if isinstance(rec["p_R"], (int, float)) else None
    own = ownership_pass(rP_v, rC_v, pR_v, alpha_R=alpha_R)
    for k, v in own.items():
        # Booleans become 0/1 in CSV; keep ints for sanity.
        rec[k] = v if not isinstance(v, bool) else int(v)
    return rec


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qs-list", default="0.10,0.20,0.50,0.80,1.00",
                    help="comma-separated q_s values (paper: 10/20/50/80/100%%)")
    ap.add_argument("--stages", default="placement,cts,routing,all_stage",
                    help="comma-separated subset of {placement, cts, routing, all_stage}")
    ap.add_argument("--designs", default="",
                    help="comma-separated DESIGN_NAMEs to include (default: all 10)")
    ap.add_argument("--alpha-R", type=float, default=0.05,
                    help="routing threshold for r_R = 1{p_R<=alpha_R} (default 0.05)")
    ap.add_argument("--fraction", type=float, default=0.01,
                    help="WM_R routing fraction used by set_routing_watermark (default 0.01)")
    ap.add_argument("--no-routing-platforms", default="asap7",
                    help="comma-separated platforms for which routing is skipped (default: asap7)")
    args = ap.parse_args()

    qs_list = [float(x) for x in args.qs_list.split(",") if x]
    stages  = [s.strip() for s in args.stages.split(",")  if s.strip()]
    want_designs = {d.strip() for d in args.designs.split(",") if d.strip()}
    no_route_plats = {p.strip() for p in args.no_routing_platforms.split(",") if p.strip()}

    out_root = HERE / "results" / "phase3" / "raw"
    out_root.mkdir(parents=True, exist_ok=True)

    for b in ACTIVE_BENCHES:
        if want_designs and b.design not in want_designs and b.design_nickname not in want_designs:
            continue
        for stg in stages:
            for q_s in qs_list:
                rec = attack_one(b, stg, q_s, out_root,
                                 no_route_plats, args.fraction, args.alpha_R)
                slug = f"{b.platform}_{b.design}_{stg}_qs{q_s}"
                (out_root / f"blind_{slug}.json").write_text(json.dumps(rec, indent=2))
                accept = rec.get("accept", "")
                print(f"[blind] {slug}: r_P={rec.get('r_P','')} "
                      f"r_C={rec.get('r_C','')} Z_R={rec.get('Z_R','')} "
                      f"p_R={rec.get('p_R','')} r_R={rec.get('r_R','')} "
                      f"r_all={rec.get('r_all','')} accept={accept} "
                      f"{rec.get('note','')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
