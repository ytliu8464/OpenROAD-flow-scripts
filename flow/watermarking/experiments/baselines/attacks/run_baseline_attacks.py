#!/usr/bin/env python3.11
# SPDX-License-Identifier: BSD-3-Clause
"""Baseline attack-robustness sweep (blind + targeted), mirroring PDMarks
§7.1/§7.2 so the methods are compared apples-to-apples.

For each baseline method and each design we:
  1. dump the public eligible set + observable features from the leaked ODB
     (attacks/or_baseline.py MODE=dump) and rank objects with the shared
     cross-validated classifier (attacks/targeted/classify.py) -> AUC.
  2. blind attack: perturb a random fraction q_s of eligible objects.
  3. targeted attack: perturb the top-K = q_s*|E| objects by predicted P(WM).
  4. re-run the baseline's own verify.py on the perturbed ODB and record the
     post-attack extraction rate r = accepted/K and detection p-value Pc.

Extraction is read directly from the perturbed ODB (the parity / buffer-count
carriers are stage-stable), so no re-route is needed -- placement baselines are
cheap; buffer_insertion inserts buffers without re-routing (extraction only).

Output: results/phase3/baseline_attacks/<method>_<plat>_<design>_<attack>_qs<q>.json
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

HERE = Path(__file__).resolve().parents[2]                  # .../experiments
sys.path.insert(0, str(HERE))
from bench_matrix import ACTIVE_BENCHES
from lib.orfs import experiment_results

OPENROAD_EXE = os.environ.get(
    "OPENROAD_EXE",
    "/home/fetzfs_projects/MISC-ytliu/watermarking/OR0415/OpenROAD/build/bin/openroad")
SIF = os.environ.get("SINGULARITY_SIF", "/home/tool/singularity/images/ispd26.sif")
SINGULARITY = shutil.which("singularity") or "/usr/local/bin/singularity"
SKLEARN_PY = os.environ.get("WM_SKLEARN_PY", "python3")

BL = {
    "cell_scattering": dict(dir="baseline-cellscatter", odb="3_place_cellscatter.odb",
                            csv="cell_scattering_embed.csv", summary="CELLSCATTER_VERIFY"),
    "kahng":           dict(dir="baseline-kahng", odb="3_place_kahng.odb",
                            csv="kahng_embed.csv", summary="KAHNG_VERIFY"),
    "icmarks":         dict(dir="baseline-icmarks", odb="3_place_icmarks.odb",
                            csv="icmarks_embed.csv", summary="ICMARKS_VERIFY"),
    "automarks":       dict(dir="baseline-automarks", odb="3_place_automarks.odb",
                            csv="automarks_embed.csv", summary="AUTOMARKS_VERIFY"),
    "buffer_insertion": dict(dir="baseline-bufins", odb="4_cts_bufins.odb",
                             csv="buffer_insertion_embed.csv", summary="BUFINS_VERIFY"),
}
BASELINES_DIR = HERE / "baselines"


def or_run(script: Path, env: dict, log: Optional[Path] = None) -> int:
    cmd = [SINGULARITY, "exec", "-B", "/home", SIF, OPENROAD_EXE,
           "-python", "-exit", str(script)]
    e = {**os.environ, **env}
    if log is None:
        return subprocess.run(cmd, env=e).returncode
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w") as f:
        return subprocess.run(cmd, env=e, stdout=f, stderr=subprocess.STDOUT).returncode


_SUM_RX = re.compile(r"K=(\d+)\s+accepted=(\d+)\s+Pc=([0-9.eE+-]+)")


def run_verify(method, odb, embed_csv, log: Path) -> Tuple[Optional[float], Optional[int], Optional[float]]:
    """Run the baseline's verify.py on `odb`; return (r, K, Pc)."""
    vscript = BASELINES_DIR / method / "verify.py"
    cmd = [SINGULARITY, "exec", "-B", "/home", SIF, OPENROAD_EXE, "-python", "-exit",
           str(vscript), "--", "--odb", str(odb), "--embed-csv", str(embed_csv),
           "--stage", "ATK"]
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w") as f:
        subprocess.run(cmd, env=os.environ, stdout=f, stderr=subprocess.STDOUT)
    txt = log.read_text(errors="replace")
    m = None
    for line in txt.splitlines():
        if BL[method]["summary"] in line:
            m = _SUM_RX.search(line)
    if not m:
        return None, None, None
    K = int(m.group(1)); acc = int(m.group(2)); pc = float(m.group(3))
    return (acc / K if K else None), K, pc


def _read_ranking(rank_csv: Path):
    out = []
    if rank_csv.exists():
        for r in csv.DictReader(open(rank_csv)):
            out.append((r["object_id"], int(float(r.get("label", 0)))))
    return out


def attack_method(b, method, qs_list, out_root, force) -> list:
    info = BL[method]
    bdir = experiment_results(b.platform, b.design_nickname, info["dir"])
    in_odb = bdir / info["odb"]
    embed_csv = bdir / info["csv"]
    recs = []
    if not in_odb.exists() or not embed_csv.exists():
        sys.stderr.write(f"[skip] {method} {b.platform}/{b.design}: missing {in_odb} or {embed_csv}\n")
        return recs

    ds = out_root / "datasets"; ds.mkdir(parents=True, exist_ok=True)
    slug = f"{method}_{b.platform}_{b.design}"
    feat = ds / f"feat_{slug}.csv"; diagj = ds / f"diag_{slug}.json"; rank = ds / f"rank_{slug}.csv"

    # 1) dump features (once) + classify
    if force or not feat.exists():
        or_run(HERE / "baselines" / "attacks" / "or_baseline.py",
               {"BL": method, "MODE": "dump", "WM_ODB": str(in_odb),
                "WM_EMBED_CSV": str(embed_csv), "WM_OUT_FEATURES": str(feat),
                "PLATFORM": b.platform},
               log=out_root / "logs" / f"dump_{slug}.log")
    diag = {}
    if feat.exists() and (force or not rank.exists()):
        with open(out_root / "logs" / f"classify_{slug}.log", "w") as lf:
            subprocess.run([SKLEARN_PY, str(HERE / "attacks" / "targeted" / "classify.py"),
                            "--features", str(feat), "--out-json", str(diagj),
                            "--out-ranking", str(rank)], stdout=lf, stderr=subprocess.STDOUT)
    if diagj.exists():
        try: diag = json.loads(diagj.read_text())
        except Exception: diag = {}
    ranking = _read_ranking(rank)
    n_elig = diag.get("n", len(ranking)); n_pos = diag.get("n_pos", 0)

    # 2,3) attacks
    for attack in ("blind", "targeted"):
        for q in qs_list:
            tag = f"{slug}_{attack}_qs{q}"
            out_odb = out_root / f"atk_{tag}.odb"
            env = {"BL": method, "MODE": "mutate", "WM_ODB": str(in_odb),
                   "WM_EMBED_CSV": str(embed_csv), "WM_OUT_ODB": str(out_odb),
                   "PLATFORM": b.platform}
            recall_topk = ""
            if attack == "blind":
                env["ATK_QS"] = str(q)
            else:
                if not ranking:
                    continue
                K = max(1, int(round(q * len(ranking))))
                chosen = [oid for oid, _ in ranking[:K]]
                if n_pos:
                    hit = sum(1 for _, lab in ranking[:K] if lab == 1)
                    recall_topk = hit / n_pos
                lst = out_root / f"objs_{tag}.txt"
                lst.write_text("".join(c + "\n" for c in chosen))
                env["WM_OBJECTS_ATTACK"] = str(lst)
            rc = or_run(HERE / "baselines" / "attacks" / "or_baseline.py", env,
                        log=out_root / "logs" / f"mutate_{tag}.log")
            r = K_ = pc = None
            if out_odb.exists():
                r, K_, pc = run_verify(method, out_odb, embed_csv,
                                       out_root / "logs" / f"verify_{tag}.log")
            rec = {"method": method, "platform": b.platform, "design": b.design,
                   "attack": attack, "q_s": q, "r": r, "Pc": pc, "K": K_,
                   "auc": diag.get("auc"), "precision_at_recall": diag.get("precision_at_recall"),
                   "n_eligible": n_elig, "n_pos": n_pos, "recall_top_K": recall_topk}
            (out_root / f"{tag}.json").write_text(json.dumps(rec, indent=2, default=str))
            recs.append(rec)
            print(f"[bl-atk] {tag}: AUC={_f(diag.get('auc'))} r={_f(r)} Pc={pc} "
                  f"recall@K={_f(recall_topk)}")
            try:
                out_odb.unlink()
            except OSError:
                pass
    return recs


def _f(v):
    try: return f"{float(v):.3f}"
    except (TypeError, ValueError): return str(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods", default=",".join(BL.keys()))
    ap.add_argument("--designs", default="")
    ap.add_argument("--platforms", default="")
    ap.add_argument("--qs-list", default="0.10,0.20,0.50,0.80,1.00")
    ap.add_argument("--force-features", action="store_true")
    args = ap.parse_args()
    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    want = {d.strip() for d in args.designs.split(",") if d.strip()}
    want_plat = {p.strip() for p in args.platforms.split(",") if p.strip()}
    qs = [float(x) for x in args.qs_list.split(",") if x]
    out_root = HERE / "results" / "phase3" / "baseline_attacks"
    out_root.mkdir(parents=True, exist_ok=True)
    for b in ACTIVE_BENCHES:
        if want and b.design not in want and b.design_nickname not in want:
            continue
        if want_plat and b.platform not in want_plat:
            continue
        for m in methods:
            attack_method(b, m, qs, out_root, args.force_features)
    return 0


if __name__ == "__main__":
    sys.exit(main())
