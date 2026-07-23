#!/usr/bin/env python3.11
# SPDX-License-Identifier: BSD-3-Clause
"""Targeted ML-based attacker (paper §7.2), corrected construction.

For each stage s ∈ {placement, cts, routing} and each design we:

  1. Reconstruct the *public eligible* object set E_s from the leaked layout
     (not the embedder's private attempt log):
       * placement / cts -> ``dump_features.py`` runs inside OpenROAD-python,
         reconstructs E_s from the ODB, labels WM_s positive / E_s\\WM_s
         negative, and writes observable features.
       * routing         -> the post-DRT ``route_counts`` CSV already enumerates
         every routed net; labels come from the seed-derived WM_R.
  2. Train a RandomForest with **cross-validation** (``classify.py``) and report
     the out-of-fold AUC and precision-at-recall.  Out-of-fold scoring removes
     the in-sample AUC≈1.0 memorization artifact of the old code and gives a
     leakage-free P(WM) ranking.
  3. Convert the ranking into a removal attack: take the top-K = round(q_s|E_s|)
     objects by P(WM) and perturb them with the same per-stage mutator as the
     blind attack, then re-verify with the owner key.

Routing is skipped for platforms in --no-routing-platforms (default: asap7).

Outputs per (bench, stage, q_s):
  results/phase3/raw/targeted_<plat>_<design>_<stage>_qs<q>.json
Cached per (bench, stage):
  results/phase3/raw/datasets/feat_<plat>_<design>_<stage>.csv
  results/phase3/raw/datasets/diag_<plat>_<design>_<stage>.json
  results/phase3/raw/datasets/rank_<plat>_<design>_<stage>.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE))
from bench_matrix import ACTIVE_BENCHES
from lib.keys import load_seed_hex
from lib.keyless_verify import routing_wm_set
from lib.orfs import (
    FLOW_HOME, experiment_results, flow_results,
    find_latest_wm_variant, wm_module_results,
)
from lib.route_stat import read_counts_csv, route_stat_from_counts
from lib.thresholds import ownership_pass
from attacks.targeted.features import routing_features
from attacks.blind.run_blind_attack import (
    _attack_placement as blind_attack_placement,
    _attack_cts       as blind_attack_cts,
    _pick_embed_dir   as blind_pick_embed_dir,
)

import shutil as _shutil
OPENROAD_EXE = os.environ.get(
    "OPENROAD_EXE",
    "/home/fetzfs_projects/MISC-ytliu/watermarking/OR0415/OpenROAD/build/bin/openroad")
SIF = os.environ.get("SINGULARITY_SIF", "/home/tool/singularity/images/ispd26.sif")
SINGULARITY = _shutil.which("singularity") or "/usr/local/bin/singularity"
# Interpreter that actually has scikit-learn in this environment.
SKLEARN_PY = os.environ.get("WM_SKLEARN_PY", "python3")

WM_CTS_SIBLING_DIST_UM = os.environ.get("WM_CTS_SIBLING_DIST_UM", "50")


def or_python(script: Path, env: dict, log: Optional[Path] = None) -> int:
    cmd = [SINGULARITY, "exec", "-B", "/home", SIF, OPENROAD_EXE,
           "-python", "-exit", str(script)]
    if log is None:
        return subprocess.run(cmd, env={**os.environ, **env}).returncode
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w") as f:
        return subprocess.run(cmd, env={**os.environ, **env},
                              stdout=f, stderr=subprocess.STDOUT).returncode


def _pick_embed_dir(plat, nick, wm_flow_variant):
    return blind_pick_embed_dir(plat, nick, wm_flow_variant)


_LIB_CACHE: dict = {}


def _resolve_placement_timing(b) -> Tuple[str, str]:
    """Resolve (WM_LIB_FILES, WM_SDC) the same way run_place_wm.sh does, so the
    dump's STA slack gate reproduces the embedder's eligible set.

    WM_LIB_FILES <- `make print-LIB_FILES DESIGN_CONFIG=<config.mk>`
    WM_SDC       <- reference variant's 3_place.sdc
    """
    key = (b.platform, b.design)
    if key not in _LIB_CACHE:
        cfg = FLOW_HOME / "designs" / b.platform / b.design / "config.mk"
        libs = ""
        try:
            out = subprocess.run(
                ["make", "-C", str(FLOW_HOME), "print-LIB_FILES",
                 f"DESIGN_CONFIG={cfg}"],
                capture_output=True, text=True, timeout=180).stdout
            for line in out.splitlines():
                if line.startswith("LIB_FILES:"):
                    libs = line.split(":", 1)[1].strip()
                    break
        except Exception:
            libs = ""
        _LIB_CACHE[key] = libs
    sdc = flow_results(b.platform, b.design_nickname, b.wm_flow_variant) / "3_place.sdc"
    return _LIB_CACHE[key], (str(sdc) if sdc.exists() else "")


def _route_counts_csv(embed_dir: Path, b) -> Optional[Path]:
    for cand in ("route_counts_5_route.csv", "route_counts.csv"):
        p = embed_dir / cand
        if p.exists():
            return p
    route_var = find_latest_wm_variant("routing_wrong_way", b.platform, b.design_nickname)
    if route_var:
        rdir = wm_module_results("routing_wrong_way", b.platform, b.design_nickname, route_var)
        for cand in ("route_counts_5_route.csv", "route_counts.csv"):
            p = rdir / cand
            if p.exists():
                return p
    return None


# ---------------------------------------------------------------------------
# Dataset construction (per bench, stage) -- cached
# ---------------------------------------------------------------------------

def _dataset_paths(out_root: Path, b, stage: str):
    d = out_root / "datasets"
    d.mkdir(parents=True, exist_ok=True)
    slug = f"{b.platform}_{b.design}_{stage}"
    return (d / f"feat_{slug}.csv", d / f"diag_{slug}.json", d / f"rank_{slug}.csv")


def _build_routing_dataset(b, embed_dir, sr, fraction, feat_csv: Path) -> Optional[int]:
    """Write object_id,label,<routing features> for every routed net.  Returns
    the number of positives, or None if no route_counts CSV exists."""
    rc_in = _route_counts_csv(embed_dir, b)
    if rc_in is None:
        return None
    header, rows_X, ids = routing_features(rc_in)
    if not rows_X:
        return None
    # Positives: seed-derived WM_R (Kerckhoffs label), falling back to the
    # committed watermark_nets.txt if the seed is unavailable.
    pos: set = set()
    if sr is not None:
        pos = routing_wm_set(sr, ids, fraction)
    if not pos:
        wm_path = embed_dir / "watermark_nets.txt"
        if wm_path.exists():
            pos = {ln.strip() for ln in open(wm_path) if ln.strip()}
    with open(feat_csv, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["object_id", "label"] + header)
        for nid, feats in zip(ids, rows_X):
            wr.writerow([nid, 1 if nid in pos else 0] + [f"{v:.6g}" for v in feats])
    return sum(1 for nid in ids if nid in pos)


def ensure_dataset(b, stage, out_root, embed_dir, odb_name, embed_csv,
                   sr, fraction, force: bool) -> Tuple[Optional[Path], dict, Optional[Path]]:
    """Build (if needed) the features CSV, run the classifier, and return
    (feat_csv, diag_dict, rank_csv).  diag carries auc / precision_at_recall /
    n / n_pos.  Cached across q_s."""
    feat_csv, diag_json, rank_csv = _dataset_paths(out_root, b, stage)
    diag: dict = {}

    if force or not feat_csv.exists():
        if stage in ("placement", "cts"):
            in_odb = embed_dir / odb_name
            if not in_odb.exists():
                return None, {"note": f"no odb {in_odb}"}, None
            if not embed_csv.exists():
                return None, {"note": f"no embed csv {embed_csv}"}, None
            log = out_root / "logs" / f"dump_{b.platform}_{b.design}_{stage}.log"
            dump_env = {"STAGE": stage, "WM_ODB": str(in_odb),
                        "WM_EMBED_CSV": str(embed_csv),
                        "WM_OUT_FEATURES": str(feat_csv),
                        "WM_CTS_SIBLING_DIST_UM": WM_CTS_SIBLING_DIST_UM}
            if stage == "placement":
                # STA slack gate needs the standard-cell liberty + placement SDC
                # so the eligible set excludes timing-critical cells (matching
                # the embedder); otherwise placement AUC is inflated by an
                # eligibility signal rather than the key.
                libs, sdc = _resolve_placement_timing(b)
                if libs:
                    dump_env["WM_LIB_FILES"] = libs
                if sdc:
                    dump_env["WM_SDC"] = sdc
            rc = or_python(HERE / "attacks" / "targeted" / "dump_features.py",
                           dump_env, log=log)
            if rc != 0 or not feat_csv.exists():
                return None, {"note": f"dump_features failed (rc={rc}, log={log})"}, None
        elif stage == "routing":
            npos = _build_routing_dataset(b, embed_dir, sr, fraction, feat_csv)
            if npos is None:
                return None, {"note": "no route_counts.csv / no routed nets"}, None
        else:
            return None, {"note": f"unknown stage {stage}"}, None

    # Classify (cross-validated) -> diag json + ranking.
    if force or not rank_csv.exists() or not diag_json.exists():
        log = out_root / "logs" / f"classify_{b.platform}_{b.design}_{stage}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "w") as lf:
            rc = subprocess.run(
                [SKLEARN_PY, str(HERE / "attacks" / "targeted" / "classify.py"),
                 "--features", str(feat_csv),
                 "--out-json", str(diag_json),
                 "--out-ranking", str(rank_csv)],
                stdout=lf, stderr=subprocess.STDOUT).returncode
        if rc != 0:
            return feat_csv, {"note": f"classify.py failed (rc={rc}, log={log})"}, None
    try:
        diag = json.loads(diag_json.read_text())
    except Exception as e:
        diag = {"note": f"could not read diag json: {e}"}
    return feat_csv, diag, rank_csv


def _read_ranking(rank_csv: Path) -> List[Tuple[str, int]]:
    """Return [(object_id, label), ...] sorted by descending P(WM)."""
    out = []
    if rank_csv is None or not rank_csv.exists():
        return out
    for r in csv.DictReader(open(rank_csv)):
        out.append((r["object_id"], int(float(r.get("label", 0)))))
    return out


# ---------------------------------------------------------------------------
# Attack hygiene (unchanged from the original): deconflict the top-K so the
# mutator's per-batch dedup can actually mutate the highest-ranked objects.
# ---------------------------------------------------------------------------

def _spread_by_components(ranked_keys: List[str],
                          components_fn: Callable[[str], List[str]]) -> List[str]:
    seen: set = set()
    front: List[str] = []
    back: List[str] = []
    for k in ranked_keys:
        comps = components_fn(k)
        if any(c in seen for c in comps):
            back.append(k)
        else:
            front.append(k)
            seen.update(comps)
    return front + back


def _atk_log_path(out_root: Path, b, out_prefix: str, q_s) -> Path:
    return out_root / "logs" / f"{out_prefix}_{b.platform}_{b.design}_qs{q_s}_atk.log"


_CTS_PERTURBED_RX = re.compile(r"\[atk_c\][^\n]*\bperturbed=(\d+)")
_PLACE_PERTURBED_RX = re.compile(
    r"\[atk_p\][^\n]*\bperturbed_pairs=(\d+)\s+perturbed_triples=(\d+)")


def _parse_mutated_count(log_path: Path) -> Optional[int]:
    if not log_path.exists():
        return None
    try:
        content = log_path.read_text(errors="replace")
    except OSError:
        return None
    m = _CTS_PERTURBED_RX.search(content)
    if m:
        return int(m.group(1))
    mp = _PLACE_PERTURBED_RX.search(content)
    if mp:
        return int(mp.group(1)) + int(mp.group(2))
    return None


def _recall_top_k(ranking: List[Tuple[str, int]], K: int, n_pos: int) -> Optional[float]:
    if n_pos <= 0 or not ranking:
        return None
    hit = sum(1 for _id, lab in ranking[:max(1, K)] if lab == 1)
    return hit / n_pos


# ---------------------------------------------------------------------------
# Per-stage targeted drivers
# ---------------------------------------------------------------------------

def _base_rec(b, stage, q_s, diag):
    return {
        "platform": b.platform, "design": b.design, "stage": stage, "q_s": q_s,
        "r_P": "", "r_C": "", "Z_R": "", "p_R": "",
        "auc": diag.get("auc") if diag.get("auc") is not None else "",
        "precision_at_recall": (diag.get("precision_at_recall")
                                if diag.get("precision_at_recall") is not None else ""),
        "recall_top_K": "", "n_eligible": diag.get("n", ""),
        "n_pos": diag.get("n_pos", ""),
        "perturbed": 0, "atk_odb": "", "note": diag.get("note", ""),
    }


def _targeted_placement(b, q_s, out_root, embed_dir, p_odb_name, embed_csv,
                        diag, ranking) -> dict:
    rec = _base_rec(b, "placement", q_s, diag)
    if not ranking:
        rec["note"] = (rec["note"] + "; " if rec["note"] else "") + "no ranking"
        return rec
    n = len(ranking)
    n_pos = diag.get("n_pos", 0) or 0
    K = max(1, int(round(q_s * n)))
    rec["recall_top_K"] = _recall_top_k(ranking, K, n_pos) or ""

    chosen = [oid for oid, _lab in ranking[:K]]
    chosen = _spread_by_components(chosen, lambda k: k.split("|"))
    atk_list = out_root / f"targeted_p_{b.platform}_{b.design}_qs{q_s}_tuples.txt"
    atk_list.parent.mkdir(parents=True, exist_ok=True)
    atk_list.write_text("".join(k + "\n" for k in chosen))

    in_odb = embed_dir / p_odb_name
    rP, atk_odb, note = blind_attack_placement(
        b, q_s, out_root, embed_dir, in_odb, embed_csv,
        out_prefix="tp", extra_env={"WM_TUPLES_ATTACK": str(atk_list)})
    if rP is not None:
        rec["r_P"] = rP
    rec["atk_odb"] = str(atk_odb)
    rec["note"] = (rec["note"] + "; " if rec["note"] else "") + (note or "")
    mutated = _parse_mutated_count(_atk_log_path(out_root, b, "tp", q_s))
    rec["perturbed"] = mutated if mutated is not None else len(chosen)
    return rec


def _targeted_cts(b, q_s, out_root, embed_dir, c_odb_name, embed_csv,
                  diag, ranking) -> dict:
    rec = _base_rec(b, "cts", q_s, diag)
    if not ranking:
        rec["note"] = (rec["note"] + "; " if rec["note"] else "") + "no ranking"
        return rec
    n = len(ranking)
    n_pos = diag.get("n_pos", 0) or 0
    K = max(1, int(round(q_s * n)))
    rec["recall_top_K"] = _recall_top_k(ranking, K, n_pos) or ""

    chosen = [oid for oid, _lab in ranking[:K]]
    chosen = _spread_by_components(chosen, lambda k: k.split("+", 1))
    atk_list = out_root / f"targeted_c_{b.platform}_{b.design}_qs{q_s}_pairs.txt"
    atk_list.parent.mkdir(parents=True, exist_ok=True)
    atk_list.write_text("".join(k + "\n" for k in chosen))

    in_odb = embed_dir / c_odb_name
    rC, atk_odb, note = blind_attack_cts(
        b, q_s, out_root, embed_dir, in_odb, embed_csv,
        out_prefix="tc", extra_env={"WM_PAIRS_ATTACK": str(atk_list),
                                     "WM_CTS_SIBLING_DIST_UM": WM_CTS_SIBLING_DIST_UM})
    if rC is not None:
        rec["r_C"] = rC
    rec["atk_odb"] = str(atk_odb)
    rec["note"] = (rec["note"] + "; " if rec["note"] else "") + (note or "")
    mutated = _parse_mutated_count(_atk_log_path(out_root, b, "tc", q_s))
    rec["perturbed"] = mutated if mutated is not None else len(chosen)
    return rec


def _targeted_routing(b, q_s, out_root, embed_dir, sr, fraction, no_route_plats,
                      diag, ranking) -> dict:
    rec = _base_rec(b, "routing", q_s, diag)
    if b.platform in no_route_plats:
        rec["note"] = f"routing skipped for platform {b.platform}"
        return rec
    if not ranking:
        rec["note"] = (rec["note"] + "; " if rec["note"] else "") + "no ranking"
        return rec
    n = len(ranking)
    n_pos = diag.get("n_pos", 0) or 0
    K = max(1, int(round(q_s * n)))
    rec["recall_top_K"] = _recall_top_k(ranking, K, n_pos) or ""

    chosen = [oid for oid, _lab in ranking[:K]]
    atk_list = out_root / f"targeted_r_{b.platform}_{b.design}_qs{q_s}_nets.txt"
    atk_list.parent.mkdir(parents=True, exist_ok=True)
    atk_list.write_text("".join(s + "\n" for s in chosen))
    rec["perturbed"] = len(chosen)

    flow_variant = f"atk-tr-{b.design}-qs{q_s}"
    wm_results = experiment_results(b.platform, b.design_nickname, flow_variant)
    wm_results.mkdir(parents=True, exist_ok=True)
    sh = FLOW_HOME / "watermarking" / "routing_wrong_way" / "run_attack_route.sh"
    proc = subprocess.run(["bash", str(sh)],
                          env={**os.environ,
                               "DESIGN": b.design, "DESIGN_NICKNAME": b.design_nickname,
                               "PLATFORM": b.platform, "WM_FLOW_VARIANT": b.wm_flow_variant,
                               "FLOW_VARIANT": flow_variant, "WM_RESULTS": str(wm_results),
                               "WM_NETS_ATTACK": str(atk_list),
                               "WATERMARK_FRACTION": str(fraction)})
    if proc.returncode != 0:
        rec["note"] = f"run_attack_route.sh failed (rc={proc.returncode})"
        return rec
    routed_odb = wm_results / "5_route.odb"
    if not routed_odb.exists():
        rec["note"] = f"no 5_route.odb at {routed_odb}"
        return rec
    rec["atk_odb"] = str(routed_odb)
    rc_out = out_root / f"atk_tr_{b.platform}_{b.design}_qs{q_s}_counts.csv"
    subprocess.run([str(HERE / "tools" / "dump_route_counts.sh")],
                   env={**os.environ, "WM_ODB": str(routed_odb),
                        "WM_COUNTS_CSV": str(rc_out)}, check=False)
    if rc_out.exists():
        counts = read_counts_csv(rc_out)
        wm = routing_wm_set(sr, counts.keys(), fraction)
        st = route_stat_from_counts(counts, wm)
        rec["Z_R"] = st.Z_R
        rec["p_R"] = st.p_R
    return rec


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def attack_stage(b, stage, qs_list, out_root, no_route_plats, fraction, alpha_R,
                 force_features) -> List[dict]:
    nick = b.design_nickname
    (embed_dir, p_odb_name, p_embed_csv,
     c_odb_name, c_embed_csv) = _pick_embed_dir(b.platform, nick, b.wm_flow_variant)
    seeds_dir = FLOW_HOME / "watermarking" / "gen_key" / "out" / b.design
    try:
        sr = load_seed_hex(seeds_dir / "seed_routing.hex")
    except Exception:
        sr = None

    if stage == "placement":
        odb_name, embed_csv = p_odb_name, p_embed_csv
    elif stage == "cts":
        odb_name, embed_csv = c_odb_name, c_embed_csv
    else:
        odb_name, embed_csv = None, None

    # Build dataset + diagnostics ONCE (q_s-independent).
    feat_csv, diag, rank_csv = ensure_dataset(
        b, stage, out_root, embed_dir, odb_name, embed_csv, sr, fraction,
        force_features)
    ranking = _read_ranking(rank_csv)

    recs = []
    for q_s in qs_list:
        if stage == "placement":
            rec = _targeted_placement(b, q_s, out_root, embed_dir, p_odb_name,
                                      p_embed_csv, diag, ranking)
        elif stage == "cts":
            rec = _targeted_cts(b, q_s, out_root, embed_dir, c_odb_name,
                                c_embed_csv, diag, ranking)
        elif stage == "routing":
            rec = _targeted_routing(b, q_s, out_root, embed_dir, sr, fraction,
                                    no_route_plats, diag, ranking)
        else:
            rec = _base_rec(b, stage, q_s, {"note": f"unknown stage: {stage}"})

        rP_v = rec.get("r_P") if isinstance(rec.get("r_P"), (int, float)) else None
        rC_v = rec.get("r_C") if isinstance(rec.get("r_C"), (int, float)) else None
        pR_v = rec.get("p_R") if isinstance(rec.get("p_R"), (int, float)) else None
        own = ownership_pass(rP_v, rC_v, pR_v, alpha_R=alpha_R)
        for k, v in own.items():
            rec[k] = v if not isinstance(v, bool) else int(v)
        recs.append(rec)
    return recs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qs-list", default="0.10,0.20,0.50,0.80,1.00")
    ap.add_argument("--stages", default="placement,cts,routing")
    ap.add_argument("--designs", default="")
    ap.add_argument("--alpha-R", type=float, default=0.05)
    ap.add_argument("--fraction", type=float, default=0.01)
    ap.add_argument("--no-routing-platforms", default="asap7")
    ap.add_argument("--force-features", action="store_true",
                    help="rebuild cached feature CSVs / diagnostics even if present")
    ap.add_argument("--diagnostics-only", action="store_true",
                    help="build datasets + AUC only; skip the mutate/verify attack")
    args = ap.parse_args()

    qs_list = [float(x) for x in args.qs_list.split(",") if x]
    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    want_designs = {d.strip() for d in args.designs.split(",") if d.strip()}
    no_route_plats = {p.strip() for p in args.no_routing_platforms.split(",") if p.strip()}

    out_root = HERE / "results" / "phase3" / "raw"
    out_root.mkdir(parents=True, exist_ok=True)

    for b in ACTIVE_BENCHES:
        if want_designs and b.design not in want_designs and b.design_nickname not in want_designs:
            continue
        for stg in stages:
            if args.diagnostics_only:
                nick = b.design_nickname
                (embed_dir, p_odb, p_csv, c_odb, c_csv) = _pick_embed_dir(
                    b.platform, nick, b.wm_flow_variant)
                seeds_dir = FLOW_HOME / "watermarking" / "gen_key" / "out" / b.design
                try:
                    sr = load_seed_hex(seeds_dir / "seed_routing.hex")
                except Exception:
                    sr = None
                odb_name = p_odb if stg == "placement" else (c_odb if stg == "cts" else None)
                embed_csv = p_csv if stg == "placement" else (c_csv if stg == "cts" else None)
                _, diag, _ = ensure_dataset(b, stg, out_root, embed_dir, odb_name,
                                            embed_csv, sr, args.fraction,
                                            args.force_features)
                print(f"[targeted-diag] {b.platform}_{b.design}_{stg}: "
                      f"AUC={diag.get('auc','')} prec@rec={diag.get('precision_at_recall','')} "
                      f"n={diag.get('n','')} n_pos={diag.get('n_pos','')} {diag.get('note','')}")
                continue

            recs = attack_stage(b, stg, qs_list, out_root, no_route_plats,
                                args.fraction, args.alpha_R, args.force_features)
            for rec in recs:
                slug = f"{b.platform}_{b.design}_{stg}_qs{rec['q_s']}"
                (out_root / f"targeted_{slug}.json").write_text(
                    json.dumps(rec, indent=2, default=str))
                print(f"[targeted] {slug}: AUC={rec.get('auc','')} "
                      f"prec@rec={rec.get('precision_at_recall','')} "
                      f"recall@topK={rec.get('recall_top_K','')} "
                      f"r_P={rec.get('r_P','')} r_C={rec.get('r_C','')} "
                      f"Z_R={rec.get('Z_R','')} r_all={rec.get('r_all','')} "
                      f"{rec.get('note','')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
