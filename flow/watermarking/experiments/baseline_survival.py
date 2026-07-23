#!/usr/bin/env python3.11
# SPDX-License-Identifier: BSD-3-Clause
"""Full-flow watermark survival for the 5 prior-work baselines (tab:survival_baseline).

For each (method, design), re-verify the embedded watermark at four PD
checkpoints by running the method's own verify.py (read-only) on the
checkpoint ODB that the baseline flow already left on disk -- NO OpenROAD flow
re-runs.  The reported metric is the extraction rate r = accepted / K, the same
quantity as the PDMarks r_P / r_C rows in tab:survival.

Checkpoint -> ODB (under experiments/results/<plat>/<nick>/baseline-<m>/):
  placement baselines (kahng, cell_scattering, icmarks, automarks):
    post_place 3_place_<suffix>.odb   post_cts 4_cts.odb
    post_grt   5_1_grt.odb            post_drt 5_route.odb
  buffer_insertion (CTS-stage): post_place n/a; post_cts 4_cts_bufins.odb;
    post_grt 5_1_grt.odb; post_drt 5_route.odb

Writes results/phase1/baseline_survival.csv (appended/upserted per cell).
Usage:
  python3.11 baseline_survival.py                       # all 5 methods
  python3.11 baseline_survival.py --methods buffer_insertion
"""
from __future__ import annotations
import argparse, csv, os, re, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
OUT_CSV = RESULTS / "phase1" / "baseline_survival.csv"
OR = "/home/fetzfs_projects/MISC-ytliu/watermarking/OR0415/OpenROAD/build/bin/openroad"
SIF = os.environ.get("SINGULARITY_SIF", "/home/tool/singularity/images/ispd26.sif")

# 8 paper designs: (platform, nickname)
BENCHES = [
    ("nangate45", "jpeg"), ("nangate45", "swerv_wrapper"),
    ("nangate45", "ariane136"), ("nangate45", "bp_multi"),
    ("asap7", "jpeg"), ("asap7", "swerv_wrapper"),
    ("asap7", "cva6"), ("asap7", "ariane"),
]
# method -> (verify_dir, variant_dir, embed_csv, place_odb_suffix or None for CTS-stage)
METHODS = {
    "kahng":            ("kahng",            "baseline-kahng",       "kahng_embed.csv",            "kahng"),
    "cell_scattering":  ("cell_scattering",  "baseline-cellscatter", "cell_scattering_embed.csv",  "cellscatter"),
    "icmarks":          ("icmarks",          "baseline-icmarks",     "icmarks_embed.csv",          "icmarks"),
    "automarks":        ("automarks",        "baseline-automarks",   "automarks_embed.csv",        "automarks"),
    "buffer_insertion": ("buffer_insertion", "baseline-bufins",      "buffer_insertion_embed.csv", None),
}
CHECKPOINTS = ["post_place", "post_cts", "post_grt", "post_drt"]
_KA = re.compile(r"K=(\d+)\s+accepted=(\d+)")


def odb_for(method, ckpt, suffix):
    if ckpt == "post_place":
        return None if suffix is None else f"3_place_{suffix}.odb"
    if ckpt == "post_cts":
        return "4_cts_bufins.odb" if suffix is None else "4_cts.odb"
    if ckpt == "post_grt":
        return "5_1_grt.odb"
    if ckpt == "post_drt":
        return "5_route.odb"
    return None


def run_verify(method, vdir, rdir, embed_csv, odb_name, ckpt):
    odb = rdir / odb_name
    emb = rdir / embed_csv
    if not odb.exists() or not emb.exists():
        return None
    out_csv = rdir / f"survival_{method}_{ckpt}.csv"
    cmd = ["singularity", "exec", "-B", "/home", "-B", "/tmp", "-e", SIF,
           OR, "-python", "-exit", str(HERE / "baselines" / vdir / "verify.py"),
           "--odb", str(odb), "--embed-csv", str(emb),
           "--stage", ckpt, "--out-csv", str(out_csv)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    except subprocess.TimeoutExpired:
        return {"note": "timeout"}
    blob = p.stdout + p.stderr
    m = _KA.search(blob)
    if not m:
        # Embedder committed no surviving claims for this design -> nothing to
        # verify (e.g. AutoMarks whose heuristic region was too small).  This is
        # a real "no watermark embedded" outcome, not a parse error.
        if "nothing to verify" in blob:
            return {"note": "no_claims"}
        return {"note": "parse_fail"}
    K, acc = int(m.group(1)), int(m.group(2))
    return {"K": K, "accepted": acc, "r": (acc / K if K else "")}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods", nargs="*", default=list(METHODS))
    args = ap.parse_args(argv)

    # upsert into existing CSV
    rows = {}
    if OUT_CSV.exists():
        for r in csv.DictReader(open(OUT_CSV)):
            rows[(r["platform"], r["design"], r["method"], r["checkpoint"])] = r

    for method in args.methods:
        vdir, variant, embed_csv, suffix = METHODS[method]
        for plat, nick in BENCHES:
            rdir = RESULTS / plat / nick / variant
            for ckpt in CHECKPOINTS:
                odb_name = odb_for(method, ckpt, suffix)
                key = (plat, nick, method, ckpt)
                if odb_name is None:        # e.g. bufins post_place
                    rows[key] = {"platform": plat, "design": nick, "method": method,
                                 "checkpoint": ckpt, "K": "", "accepted": "", "r": "", "note": "n/a"}
                    continue
                res = run_verify(method, vdir, rdir, embed_csv, odb_name, ckpt)
                if res is None:
                    rec = {"K": "", "accepted": "", "r": "", "note": "missing_odb_or_csv"}
                else:
                    rec = {"K": res.get("K", ""), "accepted": res.get("accepted", ""),
                           "r": res.get("r", ""), "note": res.get("note", "")}
                rows[key] = {"platform": plat, "design": nick, "method": method,
                             "checkpoint": ckpt, **rec}
                print(f"[surv] {plat}/{nick} {method:16s} {ckpt:11s} "
                      f"r={rec['r'] if rec['r']=='' else round(float(rec['r']),3)} "
                      f"({rec['accepted']}/{rec['K']}) {rec['note']}", flush=True)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fields = ["platform", "design", "method", "checkpoint", "K", "accepted", "r", "note"]
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for k in sorted(rows):
            w.writerow({fld: rows[k].get(fld, "") for fld in fields})
    print(f"[surv] wrote {OUT_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
