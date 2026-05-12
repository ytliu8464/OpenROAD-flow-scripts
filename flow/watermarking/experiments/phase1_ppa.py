#!/usr/bin/env python3.11
# SPDX-License-Identifier: BSD-3-Clause
"""Compute Delta-PPA and P_c for each PDMarks variant against the reference.

For each ACTIVE bench (DESIGN, PLATFORM, WM_FLOW_VARIANT):
- load the reference metrics from flow/logs/<platform>/<design>/<wm_flow_variant>/
- for each PDMarks method, find and load the watermarked PPA run from the
  experiment harness log directory:
    flow/watermarking/experiments/logs/<plat>/<design>/<variant>/
  The variant is auto-discovered as the most recently completed run, or can be
  overridden via WM_VARIANT_OVERRIDE (see below).
- Embed/verify CSVs (wm_place_order_verify_v2.csv, wm_cts_stage_report.csv,
  route_counts.csv) live in:
    flow/results/<platform>/<design>/<wm_flow_variant>/   (the reference-flow dir)
- Baseline methods (Cell-scattering, Buffer-insertion) use the standard
  flow/logs/<platform>/<design>/<variant>/ path because their run.sh calls
  make from within flow/ (not from a module subdirectory).
- emit results/phase1/raw/ppa_<plat>_<design>_<method>.json
"""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from bench_matrix import ACTIVE_BENCHES
from lib.orfs import (
    FLOW_HOME,
    load_reference, load_experiment_metrics,
    find_latest_experiment_variant, experiment_results,
)
from lib.pc import pc_stage, pc_total
from lib.route_stat import read_counts_csv, read_watermark_nets, route_stat_from_counts


OUT_DIR = HERE / "results" / "phase1" / "raw"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# PDMarks method -> default experiment harness variant prefix.
# ---------------------------------------------------------------------------
PDMARKS_VARIANT_PREFIX = {
    "P-only":    "pdmarks-p-only",
    "C-only":    "pdmarks-c-only",
    "R-only":    "pdmarks-r-only",
    "All-stage": "pdmarks-all-stage-routed",
}

# Baseline methods: use standard flow/logs/ path (their run.sh calls make -C flow/)
BASELINE_METHODS = {"Cell-scattering", "Buffer-insertion"}

# VARIANT_MAP: for baselines, the FLOW_VARIANT name under flow/logs/.
BASELINE_VARIANT_MAP = {
    "Cell-scattering":  "baseline-cellscatter",
    "Buffer-insertion": "baseline-bufins",
}

# Optional explicit variant override: populated by env PDMARKS_VARIANT_<METHOD>
# e.g. PDMARKS_VARIANT_P_ONLY=base-ppa-v2
# When set, overrides auto-discovery for that method.
_ENV_PREFIX = "PDMARKS_VARIANT_"
WM_VARIANT_OVERRIDE: dict[str, str] = {}
for _method_key in PDMARKS_VARIANT_PREFIX:
    _env_key = _ENV_PREFIX + _method_key.upper().replace("-", "_")
    _v = os.environ.get(_env_key, "")
    if _v:
        WM_VARIANT_OVERRIDE[_method_key] = _v


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _delta(ref, val):
    if ref is None or val is None:
        return ""
    if ref == 0:
        return val - ref
    return (val - ref) / max(abs(ref), 1e-12)


def _baseline_has_run(platform: str, design: str, variant: str) -> bool:
    """Check standard flow/logs path (used by baseline run.sh drivers)."""
    return (FLOW_HOME / "logs" / platform / design / variant
            / "6_report.json").exists()


def _load_baseline_metrics(platform: str, design: str, variant: str):
    """Load PPA from standard flow/logs path (for baselines)."""
    try:
        return load_reference(platform, design, variant)
    except Exception:
        return None


def _load_pdmarks_metrics(platform: str, design: str, variant: str):
    """Load PPA from the experiment harness logs/results dir."""
    try:
        return load_experiment_metrics(platform, design, variant)
    except Exception:
        return None


def _find_pdmarks_run(method: str, platform: str, design: str):
    """Return (variant, metrics) for a PDMarks method in experiments/logs."""
    explicit = WM_VARIANT_OVERRIDE.get(method)
    if explicit:
        var = explicit
    else:
        var = find_latest_experiment_variant(
            platform, design, PDMARKS_VARIANT_PREFIX[method]
        )
    if var is None:
        return None, None
    return var, _load_pdmarks_metrics(platform, design, var)


# ---------------------------------------------------------------------------
# Embed/verify CSV readers — all look in the REFERENCE flow variant dir
# (flow/results/<platform>/<design>/<wm_flow_variant>/) because that is where
# the watermark embedding scripts write their output files.
# ---------------------------------------------------------------------------

def _ref_results(b) -> Path:
    """The reference-flow results dir where embed/verify CSVs live."""
    return FLOW_HOME / "results" / b.platform / b.design / b.wm_flow_variant


def _placement_xX(ref_results_dir: Path, suffix: str = ""):
    """Return (X, x) from the post-finish place-ordering verify CSV.

    wm_place_order_verify_v2.csv uses a *summary* format:
        metric,value
        pairs_checked,45
        pairs_ok,45
        ...
    Fall back to parsing the per-pair embed CSV (satisfied column) when the
    summary CSV is missing or has zero counts.
    """
    verify_names = []
    if suffix:
        verify_names.append(f"wm_place_order_verify{suffix}.csv")
    verify_names.extend(("wm_place_order_verify_v2.csv", "wm_place_order_verify.csv"))
    for name in verify_names:
        p = ref_results_dir / name
        if p.exists():
            try:
                summary = {r["metric"]: r["value"]
                           for r in csv.DictReader(open(p))
                           if r.get("metric") and r.get("value") is not None}
                X = int(summary.get("pairs_checked", 0)) + int(summary.get("groups_checked", 0))
                ok = int(summary.get("pairs_ok", 0)) + int(summary.get("groups_ok", 0))
                if X > 0:
                    return X, X - ok  # miss = checked - ok
            except Exception:
                pass

    # Fallback: per-pair embed CSV
    embed_names = []
    if suffix:
        embed_names.append(f"wm_place_order_embed{suffix}.csv")
    embed_names.extend(("wm_place_order_embed_v2.csv", "wm_place_order_embed.csv"))
    for name in embed_names:
        p2 = ref_results_dir / name
        if not p2.exists():
            continue
        big = miss = 0
        for row in csv.DictReader(open(p2)):
            if row.get("skipped_reason", "") not in ("", "already_satisfied"):
                continue
            big += 1
            if row.get("satisfied", "True") not in ("True", "true", "1"):
                miss += 1
        if big > 0:
            return big, miss
    return 0, 0


def _cts_xX(*results_dirs: Path, suffix: str = ""):
    """Return (X, x) from the CTS-watermark stage report CSV.

    wm_cts_stage_report.csv has one row per pair, with columns including
    post_final_satisfied (True/False).  We use the post_final stage as the
    ground truth (most conservative, matches what the verifier reports).
    """
    stage_names = []
    if suffix:
        stage_names.append(f"wm_cts_stage_report{suffix}.csv")
    stage_names.append("wm_cts_stage_report.csv")
    for ref_results_dir in results_dirs:
        for name in stage_names:
            p = ref_results_dir / name
            if p.exists():
                big = miss = 0
                try:
                    for row in csv.DictReader(open(p)):
                        # Use post_final_satisfied if present; else post_drt_satisfied
                        sat_val = (row.get("post_final_satisfied") or
                                   row.get("post_drt_satisfied") or
                                   row.get("ok", ""))
                        if sat_val == "":
                            continue
                        big += 1
                        if sat_val in ("False", "false", "0", "mismatch"):
                            miss += 1
                except Exception:
                    pass
                if big > 0:
                    return big, miss

    # Fallback: embed CSV with final_bit check
    embed_names = []
    if suffix:
        embed_names.append(f"wm_cts_pairs_embed{suffix}.csv")
    embed_names.append("wm_cts_pairs_embed.csv")
    for ref_results_dir in results_dirs:
        for name in embed_names:
            p2 = ref_results_dir / name
            if not p2.exists():
                continue
            big = miss = 0
            for row in csv.DictReader(open(p2)):
                if row.get("skipped_reason", "") not in ("", "ok"):
                    continue
                big += 1
                try:
                    if int(row.get("final_bit", "-1")) != int(row.get("target_bit", "-2")):
                        miss += 1
                except Exception:
                    pass
            if big > 0:
                return big, miss
    return 0, 0


def _routing_p(ref_results_dir: Path):
    """Read routing stat from route_counts.csv + watermark_nets.txt in the ref dir."""
    rc = ref_results_dir / "route_counts.csv"
    wm = ref_results_dir / "watermark_nets.txt"
    if not rc.exists() or not wm.exists():
        return None, None
    counts = read_counts_csv(rc)
    wm_set = read_watermark_nets(wm)
    st = route_stat_from_counts(counts, wm_set)
    return st.p_R, st


def _cellscatter_xX(baseline_results_dir: Path):
    """Baseline cell-scattering: use DRT verify CSV or embed CSV."""
    for fname in ("cell_scattering_verify_DRT.csv", "cell_scattering_embed.csv"):
        p = baseline_results_dir / fname
        if not p.exists():
            continue
        big = miss = 0
        for row in csv.DictReader(open(p)):
            sat = row.get("satisfied", row.get("match", "True"))
            if sat in ("True", "true", "1"):
                big += 1
            elif sat in ("False", "false", "0"):
                big += 1
                miss += 1
        if big > 0:
            return big, miss
    return 0, 0


def _bufins_xX(baseline_results_dir: Path):
    """Baseline buffer-insertion: verify or embed CSV."""
    for fname in ("buffer_insertion_verify_DRT.csv", "buffer_insertion_embed.csv"):
        p = baseline_results_dir / fname
        if not p.exists():
            continue
        big = miss = 0
        for row in csv.DictReader(open(p)):
            sat = row.get("satisfied", row.get("match", "True"))
            if sat in ("True", "true", "1"):
                big += 1
            elif sat in ("False", "false", "0"):
                big += 1
                miss += 1
        if big > 0:
            return big, miss
    return 0, 0


# ---------------------------------------------------------------------------
# Main collector
# ---------------------------------------------------------------------------

def collect_one(b, method: str):
    plat = b.platform
    design = b.design
    ref_res = _ref_results(b)   # flow/results/<plat>/<design>/<wm_flow_variant>/

    out: dict = {
        "platform": plat, "design": design,
        "method": method, "variant": "",
        "dWNS": "", "dTNS": "", "dRWL": "", "dPower": "", "dRuntime": "",
        "Pc": "", "X_P": "", "x_P": "", "X_C": "", "x_C": "", "p_R": "",
    }

    # ------------------------------------------------------------------ #
    # Baseline methods: standard flow/logs/ path                          #
    # ------------------------------------------------------------------ #
    if method in BASELINE_METHODS:
        variant_name = BASELINE_VARIANT_MAP[method]
        out["variant"] = variant_name
        if not _baseline_has_run(plat, design, variant_name):
            out["note"] = f"not yet run; bash baselines/{method.lower().replace('-','_')}/run.sh"
            return out

        ref = load_reference(plat, design, b.wm_flow_variant)
        cur = _load_baseline_metrics(plat, design, variant_name)
        if cur is None:
            out["note"] = "failed to load baseline metrics"
            return out

        out["dWNS"]     = _delta(ref.wns_ns,    cur.wns_ns)
        out["dTNS"]     = _delta(ref.tns_ns,    cur.tns_ns)
        out["dRWL"]     = _delta(ref.rwl_um,    cur.rwl_um)
        out["dPower"]   = _delta(ref.power_w,   cur.power_w)
        out["dRuntime"] = _delta(ref.runtime_s, cur.runtime_s)

        # Embed/verify CSVs land in the reference flow variant dir
        base_rdir = FLOW_HOME / "results" / plat / design / variant_name
        pc_parts = []
        if method == "Cell-scattering":
            X_P, x_P = _cellscatter_xX(base_rdir)
            out["X_P"] = X_P; out["x_P"] = x_P
            if X_P > 0:
                pc_parts.append(pc_stage(X_P, x_P, 0.5))
        elif method == "Buffer-insertion":
            X_P, x_P = _bufins_xX(base_rdir)
            out["X_P"] = X_P; out["x_P"] = x_P
            if X_P > 0:
                pc_parts.append(pc_stage(X_P, x_P, 0.5))
        if pc_parts:
            out["Pc"] = pc_total(*pc_parts)
        return out

    # ------------------------------------------------------------------ #
    # PDMarks methods: experiment harness log directories                 #
    # ------------------------------------------------------------------ #
    pc_parts = []

    if method == "P-only":
        var, cur = _find_pdmarks_run(method, plat, design)
        if cur is None:
            out["note"] = f"not yet run; use experiments/drivers/run_p_only.sh"
            return out
        out["variant"] = var
        ref = load_reference(plat, design, b.wm_flow_variant)
        out["dWNS"]     = _delta(ref.wns_ns,    cur.wns_ns)
        out["dTNS"]     = _delta(ref.tns_ns,    cur.tns_ns)
        out["dRWL"]     = _delta(ref.rwl_um,    cur.rwl_um)
        out["dPower"]   = _delta(ref.power_w,   cur.power_w)
        out["dRuntime"] = _delta(ref.runtime_s, cur.runtime_s)
        X_P, x_P = _placement_xX(ref_res)
        out["X_P"] = X_P; out["x_P"] = x_P
        if X_P > 0:
            pc_parts.append(pc_stage(X_P, x_P, 0.5))

    elif method == "C-only":
        var, cur = _find_pdmarks_run(method, plat, design)
        if cur is None:
            out["note"] = "not yet run; use experiments/drivers/run_c_only.sh"
            return out
        out["variant"] = var
        ref = load_reference(plat, design, b.wm_flow_variant)
        out["dWNS"]     = _delta(ref.wns_ns,    cur.wns_ns)
        out["dTNS"]     = _delta(ref.tns_ns,    cur.tns_ns)
        out["dRWL"]     = _delta(ref.rwl_um,    cur.rwl_um)
        out["dPower"]   = _delta(ref.power_w,   cur.power_w)
        out["dRuntime"] = _delta(ref.runtime_s, cur.runtime_s)
        X_C, x_C = _cts_xX(ref_res)
        out["X_C"] = X_C; out["x_C"] = x_C
        if X_C > 0:
            pc_parts.append(pc_stage(X_C, x_C, 0.5))

    elif method == "R-only":
        var, cur = _find_pdmarks_run(method, plat, design)
        if cur is None:
            out["note"] = "not yet run; use experiments/drivers/run_r_only.sh"
            return out
        out["variant"] = var
        ref = load_reference(plat, design, b.wm_flow_variant)
        out["dWNS"]     = _delta(ref.wns_ns,    cur.wns_ns)
        out["dTNS"]     = _delta(ref.tns_ns,    cur.tns_ns)
        out["dRWL"]     = _delta(ref.rwl_um,    cur.rwl_um)
        out["dPower"]   = _delta(ref.power_w,   cur.power_w)
        out["dRuntime"] = _delta(ref.runtime_s, cur.runtime_s)
        pR, _ = _routing_p(experiment_results(plat, design, var))
        if pR is not None:
            out["p_R"] = pR
            pc_parts.append(pR)

    elif method == "All-stage":
        # All-stage has one final PPA run: pdmarks-all-stage-routed.  Do not
        # auto-pick old direct routing module runs, or they masquerade as
        # successful all-stage experiments.
        ref = load_reference(plat, design, b.wm_flow_variant)
        var, cur = _find_pdmarks_run(method, plat, design)
        if cur is None:
            out["note"] = "no all-stage run found; run experiments/drivers/run_all_stage.sh"
            return out

        out["variant"] = var
        out["dWNS"]     = _delta(ref.wns_ns,    cur.wns_ns)
        out["dTNS"]     = _delta(ref.tns_ns,    cur.tns_ns)
        out["dRWL"]     = _delta(ref.rwl_um,    cur.rwl_um)
        out["dPower"]   = _delta(ref.power_w,   cur.power_w)
        out["dRuntime"] = _delta(ref.runtime_s, cur.runtime_s)

        cts_variant = var[:-len("-routed")] if var.endswith("-routed") else "pdmarks-all-stage"
        cts_res = FLOW_HOME / "results" / plat / design / cts_variant
        route_res = experiment_results(plat, design, var)

        X_P, x_P = _placement_xX(ref_res, "_all_stage")
        out["X_P"] = X_P; out["x_P"] = x_P
        if X_P > 0:
            pc_parts.append(pc_stage(X_P, x_P, 0.5))
        X_C, x_C = _cts_xX(cts_res, ref_res, suffix="_all_stage")
        out["X_C"] = X_C; out["x_C"] = x_C
        if X_C > 0:
            pc_parts.append(pc_stage(X_C, x_C, 0.5))
        pR, _ = _routing_p(route_res)
        if pR is not None:
            out["p_R"] = pR
            pc_parts.append(pR)

    else:
        out["note"] = f"unknown method: {method}"
        return out

    if pc_parts:
        out["Pc"] = pc_total(*pc_parts)
    return out


def main():
    all_methods = (
        list(BASELINE_METHODS) +
        ["P-only", "C-only", "R-only", "All-stage"]
    )
    for b in ACTIVE_BENCHES:
        for method in all_methods:
            rec = collect_one(b, method)
            slug = f"{b.platform}_{b.design}_{method.lower().replace('-','_').replace(' ','_')}"
            (OUT_DIR / f"ppa_{slug}.json").write_text(json.dumps(rec, indent=2))
            var = rec.get("variant", "?")
            note = rec.get("note", "ok")
            print(f"[ppa] {b.platform}/{b.design}  {method:<18}  variant={var!r}  {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
