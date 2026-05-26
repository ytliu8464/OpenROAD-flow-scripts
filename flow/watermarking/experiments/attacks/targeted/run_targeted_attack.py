#!/usr/bin/env python3.11
# SPDX-License-Identifier: BSD-3-Clause
"""Targeted ML-based attacker (paper §7.2).

For each stage s ∈ {placement, cts, routing}:

  1. Build a labeled feature dataset over the embed CSV (positives) and the
     reconstructed eligibility pool (negatives = eligible - positives).
  2. Train a RandomForestClassifier per stage; report AUC, precision at
     fixed recall (0.5), and recall@top-K (K = q_s * |pool|).
  3. Rank objects by predicted P(WM); take the top-K and perturb them with
     the same per-stage mutator as the blind attack:
         placement: swap 2-tuples / permute 3-tuples + legalize
         cts:       move sequential sink between L_A/L_B
         routing:   clear watermark tag + re-detail_route

Routing is skipped for platforms in --no-routing-platforms (default: asap7;
ASAP7's strict-direction router produces zero wrong-way segments, leaving
Z_R / p_R structurally undefined).

Outputs per (bench, stage, q_s):
  results/phase3/raw/targeted_<plat>_<design>_<stage>_qs<q>.json
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
from attacks.targeted.features import (
    placement_features, cts_features, routing_features,
)
# Reuse the blind driver's mutator+verify helpers; they now accept an
# ``out_prefix`` (``tp`` / ``tc``) and ``extra_env`` so the targeted path can
# pass WM_TUPLES_ATTACK / WM_PAIRS_ATTACK and write atk_tp_*/atk_tc_* ODBs.
from attacks.blind.run_blind_attack import (
    _attack_placement as blind_attack_placement,
    _attack_cts       as blind_attack_cts,
    _pick_embed_dir   as blind_pick_embed_dir,
)


OPENROAD_EXE = os.environ.get(
    "OPENROAD_EXE",
    "/home/fetzfs_projects/MISC-ytliu/watermarking/OR0415/OpenROAD/build/bin/openroad")
SIF = os.environ.get("SINGULARITY_SIF",
                     "/home/tool/singularity/images/ispd26.sif")


def or_python(script: Path, env: dict) -> int:
    cmd = ["singularity", "exec", "-B", "/home", SIF, OPENROAD_EXE,
           "-python", "-exit", str(script)]
    return subprocess.run(cmd, env={**os.environ, **env}).returncode


# ---------------------------------------------------------------------------
# Path resolution mirrors run_blind_attack.py
# ---------------------------------------------------------------------------

def _pick_embed_dir(plat, nick, wm_flow_variant):
    """Thin wrapper around the blind driver's helper -- returns
    (embed_dir, p_odb_name, p_embed_csv, c_odb_name, c_embed_csv) so the
    targeted placement/CTS paths know which ODB to mutate."""
    return blind_pick_embed_dir(plat, nick, wm_flow_variant)


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
# Classifier + diagnostics
# ---------------------------------------------------------------------------

def _margin_score(X: List[List[float]]) -> List[float]:
    """Z-score-magnitude sum across feature columns; used when sklearn
    can't train (e.g., single-class labels)."""
    import statistics
    if not X or not X[0]:
        return [0.0] * len(X)
    means = [statistics.mean([row[j] for row in X]) for j in range(len(X[0]))]
    sds   = [statistics.pstdev([row[j] for row in X]) or 1.0 for j in range(len(X[0]))]
    return [
        sum(abs((row[j] - means[j]) / sds[j]) for j in range(len(row)))
        for row in X
    ]


def _classifier_diagnostics(X, y, *, top_K: int):
    """Train an RF and return (probs, AUC, precision@recall=0.5, recall_top_K).

    Falls back to a margin-based ranking when sklearn can't train.  Handles:
      - single-class labels (all 1s or all 0s)  -> AUC/precision undefined,
        margin-based probs, recall@K computed only if both classes exist
      - too few samples / stratify failure      -> retry without stratify
    """
    auc = pr_at_recall = recall_top = None
    probs: List[float] = [0.0] * len(y)

    n_pos = sum(1 for v in y if v == 1)
    n_neg = sum(1 for v in y if v == 0)
    single_class = (n_pos == 0) or (n_neg == 0)

    if single_class:
        # Cannot train a binary classifier; use margin-based heuristic.
        sys.stderr.write(
            f"[targeted] single-class labels (n_pos={n_pos}, n_neg={n_neg}); "
            f"using margin-based score, AUC/precision unavailable\n"
        )
        probs = _margin_score(X)
    else:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import roc_auc_score, precision_recall_curve

        try:
            if min(n_pos, n_neg) >= 2 and len(y) >= 4:
                Xtr, Xte, ytr, yte = train_test_split(
                    X, y, test_size=0.2, random_state=0, stratify=y)
            else:
                # Not enough of each class to stratify; use whole set for fit.
                Xtr, ytr = X, y
            clf = RandomForestClassifier(
                n_estimators=400, max_depth=None,
                random_state=0, n_jobs=-1, class_weight="balanced")
            clf.fit(Xtr, ytr)
            probs = clf.predict_proba(X)[:, 1].tolist()
            try:
                auc = float(roc_auc_score(y, probs))
            except Exception:
                pass
            try:
                p, r, _ = precision_recall_curve(y, probs)
                idxs = [i for i, rv in enumerate(r) if rv >= 0.5]
                if idxs:
                    pr_at_recall = float(p[max(idxs)])
            except Exception:
                pass
        except Exception as e:
            sys.stderr.write(f"[targeted] sklearn fallback: {e}\n")
            probs = _margin_score(X)

    # recall@top_K: among the K objects with highest predicted score, what
    # fraction of all positives did we hit?  (Only meaningful when positives
    # exist; when single_class==all positives, this trivially equals K/N.)
    if probs and n_pos > 0:
        order = sorted(range(len(probs)), key=lambda i: probs[i], reverse=True)
        top = set(order[:max(1, top_K)])
        hit = sum(1 for i in top if y[i] == 1)
        recall_top = hit / n_pos

    return probs, auc, pr_at_recall, recall_top


# ---------------------------------------------------------------------------
# Targeted-attack hygiene: deconflict the top-K so the mutator's per-batch
# dedup doesn't silently throw most of it away, and count what actually got
# mutated instead of what we asked the mutator to touch.
# ---------------------------------------------------------------------------

def _spread_by_components(ranked_keys: List[str],
                          components_fn: Callable[[str], List[str]]) -> List[str]:
    """Reorder a classifier-ranked key list so the mutator's per-batch
    dedup (one touch per LCB / cell) can actually mutate the top of the
    list.

    Both blind mutators refuse to revisit a component (`attack_cts.py`
    line 147 ``used_lcbs``; `attack_placement.py` line 165 ``touched``).
    Random selection rarely collides; classifier-ranked selection
    concentrates on objects sharing the same high-fanout LCB / dense
    region, so the mutator mutates the first few and silently skips the
    rest -- which made targeted CTS look *weaker* than blind at high q_s
    (see commit notes / README §3-2).

    One pass in rank order: a key whose components are still free goes
    into ``front`` (mutator will mutate it); a key whose components are
    already claimed is demoted to ``back`` (mutator will see and skip
    it).  The relative rank order is preserved within each section, so
    the "perturb the top-K by P(WM)" semantic is preserved subject to
    the mutator's feasibility constraint.
    """
    seen: set = set()
    front: List[str] = []
    back:  List[str] = []
    for k in ranked_keys:
        comps = components_fn(k)
        if any(c in seen for c in comps):
            back.append(k)
        else:
            front.append(k)
            seen.update(comps)
    return front + back


def _atk_log_path(out_root: Path, b, out_prefix: str, q_s) -> Path:
    """Mirrors blind run_blind_attack.py:_log_path('atk') for a given prefix."""
    return out_root / "logs" / f"{out_prefix}_{b.platform}_{b.design}_qs{q_s}_atk.log"


_CTS_PERTURBED_RX = re.compile(r"\[atk_c\][^\n]*\bperturbed=(\d+)")
_PLACE_PERTURBED_RX = re.compile(
    r"\[atk_p\][^\n]*\bperturbed_pairs=(\d+)\s+perturbed_triples=(\d+)"
)


def _parse_mutated_count(log_path: Path) -> Optional[int]:
    """Return the number of objects the mutator actually mutated.

    The blind mutators emit a summary line on exit:
        [atk_c] pool=16 target=16 perturbed=12 skipped_no_sink=4 ...
        [atk_p] pool=53 target=53 perturbed_pairs=40 perturbed_triples=8 skipped=5 ...
    Returns ``None`` if the log is missing or has no parseable summary.
    """
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


# ---------------------------------------------------------------------------
# Per-stage targeted drivers
# ---------------------------------------------------------------------------

def _targeted_placement(b, q_s, out_root, embed_dir, p_odb_name, embed_csv) -> dict:
    """Train classifier on the embed CSV (positives), pick the top-K tuples by
    P(WM), write the keys to a file, then invoke the blind-attack mutator with
    ``WM_TUPLES_ATTACK`` so it perturbs *only* those tuples.  Output ODB is
    ``atk_tp_<plat>_<design>_qs<q>.odb`` and r_P is measured via the standard
    place_wm verify_stages pipeline."""
    rec = {"platform": b.platform, "design": b.design,
           "stage": "placement", "q_s": q_s,
           "r_P": "", "r_C": "", "Z_R": "", "p_R": "",
           "auc": "", "precision_at_recall": "", "recall_top_K": "",
           "perturbed": 0, "atk_odb": "", "note": ""}
    if not embed_csv.exists():
        rec["note"] = f"missing embed_csv {embed_csv}"
        return rec
    header, rows_X, ids = placement_features(embed_csv)
    if not rows_X:
        rec["note"] = "placement embed CSV has no rows"
        return rec
    # Labels: positives = rows where embedder accepted (satisfied=True,
    # skipped_reason in {'','already_satisfied'}).
    labels: List[int] = []
    with open(embed_csv) as f:
        for r in csv.DictReader(f):
            sat = str(r.get("satisfied", "True")).strip().lower()
            skip = str(r.get("skipped_reason", "")).strip()
            is_pos = (sat in ("true", "1")) and (skip in ("", "already_satisfied"))
            labels.append(1 if is_pos else 0)
    if len(labels) != len(rows_X):
        rec["note"] = "label / feature length mismatch"
        return rec
    K = max(1, int(round(q_s * len(rows_X))))
    probs, auc, pr_at_recall, recall_top = _classifier_diagnostics(rows_X, labels, top_K=K)
    rec["auc"] = auc if auc is not None else ""
    rec["precision_at_recall"] = pr_at_recall if pr_at_recall is not None else ""
    rec["recall_top_K"] = recall_top if recall_top is not None else ""

    # Top-K tuple keys -- features.py builds these as "A|B" or "A|B|C",
    # matching the key attack_placement.py derives from the eligibility pool.
    # Spread by cell-name so the mutator's ``touched`` dedup doesn't drop
    # most of the high-ranked tuples (which share cells with each other).
    order = sorted(range(len(probs)), key=lambda i: probs[i], reverse=True)
    chosen = [ids[i] for i in order[:K]]
    chosen = _spread_by_components(chosen, lambda k: k.split("|"))
    atk_list = out_root / f"targeted_p_{b.platform}_{b.design}_qs{q_s}_tuples.txt"
    atk_list.parent.mkdir(parents=True, exist_ok=True)
    with open(atk_list, "w") as f:
        for k in chosen:
            f.write(k + "\n")

    in_odb = embed_dir / p_odb_name
    rP, atk_odb, note = blind_attack_placement(
        b, q_s, out_root, embed_dir, in_odb, embed_csv,
        out_prefix="tp",
        extra_env={"WM_TUPLES_ATTACK": str(atk_list)})
    if rP is not None: rec["r_P"] = rP
    rec["atk_odb"] = str(atk_odb)
    rec["note"] = note

    # Honest mutation count: parse [atk_p] perturbed_pairs+perturbed_triples
    # from the mutator log.  Falls back to the requested K if parse fails.
    mutated = _parse_mutated_count(_atk_log_path(out_root, b, "tp", q_s))
    rec["perturbed"] = mutated if mutated is not None else len(chosen)
    return rec


def _targeted_cts(b, q_s, out_root, embed_dir, c_odb_name, embed_csv) -> dict:
    """Train classifier on the CTS embed CSV, pick the top-K LCB pairs, write
    the pair_keys to a file, then invoke the blind CTS mutator with
    ``WM_PAIRS_ATTACK`` so it perturbs *only* those pairs.  r_C is measured
    via cts_wm.sh verify."""
    rec = {"platform": b.platform, "design": b.design, "stage": "cts", "q_s": q_s,
           "r_P": "", "r_C": "", "Z_R": "", "p_R": "",
           "auc": "", "precision_at_recall": "", "recall_top_K": "",
           "perturbed": 0, "atk_odb": "", "note": ""}
    if not embed_csv.exists():
        rec["note"] = f"missing embed_csv {embed_csv}"
        return rec
    header, rows_X, ids = cts_features(embed_csv)
    if not rows_X:
        rec["note"] = "cts embed CSV has no rows"
        return rec
    labels: List[int] = []
    with open(embed_csv) as f:
        for r in csv.DictReader(f):
            skip = str(r.get("skipped_reason", "")).strip()
            ok = skip in ("", "ok", "already_satisfied")
            labels.append(1 if ok else 0)
    if len(labels) != len(rows_X):
        rec["note"] = "label / feature length mismatch"
        return rec
    K = max(1, int(round(q_s * len(rows_X))))
    probs, auc, pr_at_recall, recall_top = _classifier_diagnostics(rows_X, labels, top_K=K)
    rec["auc"] = auc if auc is not None else ""
    rec["precision_at_recall"] = pr_at_recall if pr_at_recall is not None else ""
    rec["recall_top_K"] = recall_top if recall_top is not None else ""

    # Top-K pair_keys.  features.py uses the embedder's "pair_key" column when
    # present, falling back to "L_A+L_B" -- both forms match attack_cts.py's
    # by_key lookup over the reconstructed proximity-pair pool.  Spread by
    # LCB-name so the mutator's ``used_lcbs`` dedup doesn't silently skip
    # most of the high-ranked pairs (they share LCBs because the classifier
    # ranks high-fanout LCBs adjacent to each other).
    order = sorted(range(len(probs)), key=lambda i: probs[i], reverse=True)
    chosen = [ids[i] for i in order[:K]]
    chosen = _spread_by_components(chosen, lambda k: k.split("+", 1))
    atk_list = out_root / f"targeted_c_{b.platform}_{b.design}_qs{q_s}_pairs.txt"
    atk_list.parent.mkdir(parents=True, exist_ok=True)
    with open(atk_list, "w") as f:
        for k in chosen:
            f.write(k + "\n")

    in_odb = embed_dir / c_odb_name
    rC, atk_odb, note = blind_attack_cts(
        b, q_s, out_root, embed_dir, in_odb, embed_csv,
        out_prefix="tc",
        extra_env={"WM_PAIRS_ATTACK": str(atk_list)})
    if rC is not None: rec["r_C"] = rC
    rec["atk_odb"] = str(atk_odb)
    rec["note"] = note

    # Honest mutation count: parse [atk_c] perturbed=N from the mutator log.
    # Falls back to the requested K if parse fails.
    mutated = _parse_mutated_count(_atk_log_path(out_root, b, "tc", q_s))
    rec["perturbed"] = mutated if mutated is not None else len(chosen)
    return rec


def _targeted_routing(b, q_s, out_root, embed_dir, sr, fraction, no_route_plats) -> dict:
    rec = {"platform": b.platform, "design": b.design, "stage": "routing", "q_s": q_s,
           "Z_R": "", "p_R": "",
           "auc": "", "precision_at_recall": "", "recall_top_K": "",
           "perturbed": 0, "atk_odb": "", "note": ""}
    if b.platform in no_route_plats:
        rec["note"] = f"routing skipped for platform {b.platform}"
        return rec

    rc_in = _route_counts_csv(embed_dir, b)
    if rc_in is None:
        rec["note"] = "no route_counts*.csv on disk"
        return rec

    # Labels: positives = nets in watermark_nets.txt.  Try the all-stage dir first.
    wm_path = embed_dir / "watermark_nets.txt"
    if not wm_path.exists():
        wm_path = (embed_dir / "watermark_nets.txt")  # already tried; keep symmetric
    positives: set = set()
    if wm_path.exists():
        positives = {ln.strip() for ln in open(wm_path) if ln.strip()}

    header, rows_X, ids = routing_features(rc_in)
    if not rows_X:
        rec["note"] = "no eligible routed nets"
        return rec
    labels = [1 if nid in positives else 0 for nid in ids]
    K = max(1, int(round(q_s * len(rows_X))))
    probs, auc, pr_at_recall, recall_top = _classifier_diagnostics(rows_X, labels, top_K=K)
    rec["auc"] = auc if auc is not None else ""
    rec["precision_at_recall"] = pr_at_recall if pr_at_recall is not None else ""
    rec["recall_top_K"] = recall_top if recall_top is not None else ""

    # Convert ranked list into an attack subset = top-K by predicted P(WM).
    order = sorted(range(len(probs)), key=lambda i: probs[i], reverse=True)
    chosen = [ids[i] for i in order[:K]]
    atk_list = out_root / f"targeted_r_{b.platform}_{b.design}_qs{q_s}_nets.txt"
    atk_list.parent.mkdir(parents=True, exist_ok=True)
    with open(atk_list, "w") as f:
        for n in chosen:
            f.write(n + "\n")
    rec["perturbed"] = len(chosen)

    # Re-route via the same TCL hook as the blind routing attack.
    flow_variant = f"atk-tr-{b.design}-qs{q_s}"
    wm_results = experiment_results(b.platform, b.design_nickname, flow_variant)
    wm_results.mkdir(parents=True, exist_ok=True)
    sh = FLOW_HOME / "watermarking" / "routing_wrong_way" / "run_attack_route.sh"
    proc = subprocess.run(["bash", str(sh)],
                          env={**os.environ,
                               "DESIGN":          b.design,
                               "DESIGN_NICKNAME": b.design_nickname,
                               "PLATFORM":        b.platform,
                               "WM_FLOW_VARIANT": b.wm_flow_variant,
                               "FLOW_VARIANT":    flow_variant,
                               "WM_RESULTS":      str(wm_results),
                               "WM_NETS_ATTACK":  str(atk_list),
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
                   env={**os.environ,
                        "WM_ODB":         str(routed_odb),
                        "WM_COUNTS_CSV": str(rc_out)},
                   check=False)
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

def attack_one(b, stage: str, q_s: float, out_root: Path,
               no_route_plats, fraction, alpha_R) -> dict:
    nick = b.design_nickname
    (embed_dir, p_odb_name, p_embed_csv,
     c_odb_name, c_embed_csv) = _pick_embed_dir(b.platform, nick, b.wm_flow_variant)
    seeds_dir = FLOW_HOME / "watermarking" / "gen_key" / "out" / b.design
    try:
        sr = load_seed_hex(seeds_dir / "seed_routing.hex")
    except Exception:
        sr = None

    if stage == "placement":
        rec = _targeted_placement(b, q_s, out_root, embed_dir, p_odb_name, p_embed_csv)
    elif stage == "cts":
        rec = _targeted_cts(b, q_s, out_root, embed_dir, c_odb_name, c_embed_csv)
    elif stage == "routing":
        rec = _targeted_routing(b, q_s, out_root, embed_dir, sr,
                                fraction, no_route_plats)
    else:
        rec = {"platform": b.platform, "design": b.design,
               "stage": stage, "q_s": q_s,
               "note": f"unknown stage: {stage}"}

    # r_all / ownership decision (only routing has post-attack r values here).
    rP_v = rec.get("r_P") if isinstance(rec.get("r_P"), (int, float)) else None
    rC_v = rec.get("r_C") if isinstance(rec.get("r_C"), (int, float)) else None
    pR_v = rec.get("p_R") if isinstance(rec.get("p_R"), (int, float)) else None
    own = ownership_pass(rP_v, rC_v, pR_v, alpha_R=alpha_R)
    for k, v in own.items():
        rec[k] = v if not isinstance(v, bool) else int(v)
    return rec


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qs-list", default="0.10,0.20,0.50,0.80,1.00",
                    help="paper sweep: 10/20/50/80/100%%")
    ap.add_argument("--stages", default="placement,cts,routing")
    ap.add_argument("--designs", default="")
    ap.add_argument("--alpha-R", type=float, default=0.05)
    ap.add_argument("--fraction", type=float, default=0.01)
    ap.add_argument("--no-routing-platforms", default="asap7")
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
                (out_root / f"targeted_{slug}.json").write_text(
                    json.dumps(rec, indent=2, default=str))
                print(f"[targeted] {slug}: AUC={rec.get('auc','')} "
                      f"prec@rec={rec.get('precision_at_recall','')} "
                      f"recall@topK={rec.get('recall_top_K','')} "
                      f"r_all={rec.get('r_all','')} {rec.get('note','')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
