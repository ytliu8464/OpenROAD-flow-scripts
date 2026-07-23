#!/usr/bin/env python3.11
# SPDX-License-Identifier: BSD-3-Clause
"""PPA cost of the targeted attack at the *defeat-q* for each baseline.

For each (method, design) we find the smallest targeted-attack fraction q that
defeats ownership (verifier $P_c > \\alpha$), regenerate the attacked layout at
that q (top-K objects by predicted P(WM)), continue the ORFS back-end
(CTS+route+finish for placement carriers, route+finish for the buffer carrier),
and read the post-attack 6_report.json.  aggregate (--aggregate) then compares
to the un-attacked watermarked baseline (baseline-<m>) to report
dWNS/dTNS/dPower/dRWL -- the implementation cost of removing each watermark.

Phase 1 (default): mutate + launch the flow continuation (slow).
Phase 2 (--aggregate): read 6_report.json files -> deltas CSV + paper rows.

This answers: region-based carriers are defeated at small q with small PPA
cost; parity-based carriers (and PDMarks) need large q and large cost.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parents[2]            # .../experiments
sys.path.insert(0, str(HERE))
from bench_matrix import ACTIVE_BENCHES
from lib.orfs import (FLOW_HOME, experiment_results, experiment_logs,
                      load_experiment_metrics)

OPENROAD_EXE = os.environ.get(
    "OPENROAD_EXE",
    "/home/fetzfs_projects/MISC-ytliu/watermarking/OR0415/OpenROAD/build/bin/openroad")
SIF = os.environ.get("SINGULARITY_SIF", "/home/tool/singularity/images/ispd26.sif")
import shutil as _sh
SINGULARITY = _sh.which("singularity") or "/usr/local/bin/singularity"

RAW = HERE / "results" / "phase3" / "baseline_attacks"
PPA_DIR = RAW / "ppa"
ALPHA = 0.05
QS = [0.1, 0.2, 0.5, 0.8, 1.0]

BL = {
    "cell_scattering": dict(dir="baseline-cellscatter", odb="3_place_cellscatter.odb",
                            carrier="place", abbr="cs"),
    "kahng":           dict(dir="baseline-kahng", odb="3_place_kahng.odb",
                            carrier="place", abbr="kg"),
    "icmarks":         dict(dir="baseline-icmarks", odb="3_place_icmarks.odb",
                            carrier="place", abbr="ic"),
    "automarks":       dict(dir="baseline-automarks", odb="3_place_automarks.odb",
                            carrier="place", abbr="am"),
    "buffer_insertion": dict(dir="baseline-bufins", odb="4_cts_bufins.odb",
                             carrier="buffer", abbr="bf"),
}
PLACE_PPA = FLOW_HOME / "watermarking" / "place_ordering" / "run_ppa.sh"
CTS_PPA = FLOW_HOME / "watermarking" / "cts_v2" / "run_ppa.sh"


def _recs():
    return [json.loads(Path(f).read_text())
            for f in glob.glob(str(RAW / "*.json"))]


def defeat_q(recs, method, platform, design) -> Optional[float]:
    rows = {float(r["q_s"]): r for r in recs
            if r["method"] == method and r["platform"] == platform
            and r["design"] == design and r["attack"] == "targeted"}
    for q in QS:
        r = rows.get(q)
        if r and isinstance(r.get("Pc"), (int, float)) and r["Pc"] > ALPHA:
            return q
    return None


def _variant(method, b):
    return f"blatk-{BL[method]['abbr']}-{b.design}"


def regen_and_run(b, method, q, recs):
    info = BL[method]
    variant0 = _variant(method, b)
    rep0 = experiment_logs(b.platform, b.design_nickname, variant0) / "6_report.json"
    if rep0.exists():
        print(f"[skip-done] {method} {b.platform}/{b.design} (have 6_report)")
        return
    bdir = experiment_results(b.platform, b.design_nickname, info["dir"])
    in_odb = bdir / info["odb"]
    embed_csv = bdir / [f for f in os.listdir(bdir) if f.endswith("_embed.csv")][0]
    rank = RAW / "datasets" / f"rank_{method}_{b.platform}_{b.design}.csv"
    if not in_odb.exists() or not rank.exists():
        print(f"[skip] {method} {b.platform}/{b.design}: missing inputs")
        return
    PPA_DIR.mkdir(parents=True, exist_ok=True)

    # top-K objects by P(WM) at the defeat-q
    ranked = [r["object_id"] for r in csv.DictReader(open(rank))]
    K = max(1, int(round(q * len(ranked))))
    objs = PPA_DIR / f"objs_{method}_{b.platform}_{b.design}.txt"
    objs.write_text("".join(o + "\n" for o in ranked[:K]))

    atk_odb = PPA_DIR / f"atk_{method}_{b.platform}_{b.design}.odb"
    env = {**os.environ, "BL": method, "MODE": "mutate", "PLATFORM": b.platform,
           "WM_ODB": str(in_odb), "WM_EMBED_CSV": str(embed_csv),
           "WM_OBJECTS_ATTACK": str(objs), "WM_OUT_ODB": str(atk_odb)}
    log = PPA_DIR / f"mutate_{method}_{b.platform}_{b.design}.log"
    with open(log, "w") as f:
        rc = subprocess.run(
            [SINGULARITY, "exec", "-B", "/home", SIF, OPENROAD_EXE, "-python",
             "-exit", str(HERE / "baselines" / "attacks" / "or_baseline.py")],
            env=env, stdout=f, stderr=subprocess.STDOUT).returncode
    if rc != 0 or not atk_odb.exists():
        print(f"[FAIL mutate] {method} {b.platform}/{b.design} rc={rc}")
        return

    variant = _variant(method, b)
    wm_results = experiment_results(b.platform, b.design_nickname, variant)
    wm_results.mkdir(parents=True, exist_ok=True)
    base_env = {**os.environ,
                "DESIGN": b.design, "DESIGN_NICKNAME": b.design_nickname,
                "PLATFORM": b.platform, "WM_FLOW_VARIANT": b.wm_flow_variant,
                "FLOW_VARIANT": variant, "WM_RESULTS": str(wm_results)}
    flog = PPA_DIR / f"flow_{method}_{b.platform}_{b.design}.log"
    if BL[method]["carrier"] == "place":
        base_env["DP_ODB"] = str(atk_odb)
        sh = PLACE_PPA
    else:
        base_env["CTS_ODB"] = str(atk_odb)
        sh = CTS_PPA
    print(f"[run-ppa] {method} {b.platform}/{b.design} q={q} variant={variant}")
    with open(flog, "w") as f:
        subprocess.run(["bash", str(sh)], env=base_env,
                       stdout=f, stderr=subprocess.STDOUT)


def _delta(ref, cur):
    if ref is None or cur is None:
        return ""
    return cur - ref


def aggregate(recs):
    out = HERE / "results" / "phase3" / "baseline_ppa.csv"
    fields = ["method", "platform", "design", "defeat_q",
              "dWNS_ns", "dTNS_ns", "dPower_w", "dRWL_um", "note"]
    rows = []
    for b in ACTIVE_BENCHES:
        for method in BL:
            valid = any(isinstance(r.get("n_pos"), int) and r["n_pos"] >= 2
                        for r in recs if r["method"] == method
                        and r["platform"] == b.platform and r["design"] == b.design)
            if not valid:
                continue
            q = defeat_q(recs, method, b.platform, b.design)
            variant = _variant(method, b)
            rep = experiment_logs(b.platform, b.design_nickname, variant) / "6_report.json"
            if not rep.exists():
                rows.append(dict(method=method, platform=b.platform, design=b.design,
                                 defeat_q=q, note="no 6_report (flow pending/failed)"))
                continue
            cur = load_experiment_metrics(b.platform, b.design_nickname, variant)
            ref = load_experiment_metrics(b.platform, b.design_nickname, BL[method]["dir"])
            rows.append(dict(
                method=method, platform=b.platform, design=b.design, defeat_q=q,
                dWNS_ns=_delta(ref.wns_ns, cur.wns_ns),
                dTNS_ns=_delta(ref.tns_ns, cur.tns_ns),
                dPower_w=_delta(ref.power_w, cur.power_w),
                dRWL_um=_delta(ref.rwl_um, cur.rwl_um), note=""))
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    print(f"[agg] wrote {out} ({len(rows)} rows)")
    # quick per-method means
    from statistics import mean
    print("method            defeatq(mean)  dWNS    dTNS    dPower   dRWL")
    for method in BL:
        mr = [r for r in rows if r["method"] == method and not r.get("note")]
        if not mr:
            continue
        def mm(k):
            vs = [r[k] for r in mr if isinstance(r.get(k), (int, float))]
            return mean(vs) if vs else None
        qs = [r["defeat_q"] for r in mr if isinstance(r.get("defeat_q"), (int, float))]
        def f(v, p=4): return f"{v:.{p}g}" if isinstance(v, (int, float)) else "--"
        print(f"{method:16s} {f(mean(qs) if qs else None,2):13s}  {f(mm('dWNS_ns'))}  "
              f"{f(mm('dTNS_ns'))}  {f(mm('dPower_w'))}  {f(mm('dRWL_um'))}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods", default=",".join(BL.keys()))
    ap.add_argument("--designs", default="")
    ap.add_argument("--platforms", default="")
    ap.add_argument("--aggregate", action="store_true")
    args = ap.parse_args()
    recs = _recs()
    if args.aggregate:
        aggregate(recs)
        return 0
    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    want = {d.strip() for d in args.designs.split(",") if d.strip()}
    wp = {p.strip() for p in args.platforms.split(",") if p.strip()}
    for b in ACTIVE_BENCHES:
        if want and b.design not in want and b.design_nickname not in want:
            continue
        if wp and b.platform not in wp:
            continue
        for method in methods:
            valid = any(isinstance(r.get("n_pos"), int) and r["n_pos"] >= 2
                        for r in recs if r["method"] == method
                        and r["platform"] == b.platform and r["design"] == b.design)
            if not valid:
                continue
            q = defeat_q(recs, method, b.platform, b.design)
            if q is None:
                print(f"[skip] {method} {b.platform}/{b.design}: never defeated")
                continue
            regen_and_run(b, method, q, recs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
