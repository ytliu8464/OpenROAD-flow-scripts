#!/usr/bin/env python3.11
# SPDX-License-Identifier: BSD-3-Clause
"""Compute Delta-PPA and P_c for each PDMarks variant against the reference.

For each ACTIVE bench (DESIGN, PLATFORM, WM_FLOW_VARIANT):
- load the reference metrics from flow/logs/<platform>/<design>/<wm_flow_variant>/
- for each PDMarks method, find and load the watermarked PPA run from the
  per-module log directory:
    P-only    -> flow/watermarking/place_ordering/logs/<plat>/<design>/<variant>/
    C-only    -> flow/watermarking/cts_v2/logs/<plat>/<design>/<variant>/
    R-only    -> flow/watermarking/routing_wrong_way/logs/<plat>/<design>/<variant>/
    All-stage -> combination of all three
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
    FLOW_HOME, WM_HOME,
    load_reference, load_wm_metrics,
    find_latest_wm_variant, list_wm_variants,
)
from lib.pc import pc_stage, pc_total
from lib.route_stat import read_counts_csv, read_watermark_nets, route_stat_from_counts


OUT_DIR = HERE / "results" / "phase1" / "raw"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# PDMarks method -> watermarking module name.
# The module name is the subdirectory under flow/watermarking/ that the
# corresponding run.sh writes its results into.
# ---------------------------------------------------------------------------
WM_MODULE_MAP = {
    "P-only":    "place_ordering",
    "C-only":    "cts_v2",
    "R-only":    "routing_wrong_way",
    # All-stage uses all three; handled specially in collect_one()
    "All-stage": None,
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
for _method_key, _module in WM_MODULE_MAP.items():
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


def _pdmarks_has_run(module: str, platform: str, design: str) -> bool:
    """True if at least one completed variant exists in the module logs dir."""
    return find_latest_wm_variant(module, platform, design) is not None


def _load_baseline_metrics(platform: str, design: str, variant: str):
    """Load PPA from standard flow/logs path (for baselines)."""
    try:
        return load_reference(platform, design, variant)
    except Exception:
        return None


def _load_pdmarks_metrics(module: str, platform: str, design: str,
                          variant: str | None = None):
    """Load PPA from watermarking module logs dir."""
    try:
        return load_wm_metrics(module, platform, design, variant)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Embed/verify CSV readers — all look in the REFERENCE flow variant dir
# (flow/results/<platform>/<design>/<wm_flow_variant>/) because that is where
# the watermark embedding scripts write their output files.
# ---------------------------------------------------------------------------

def _ref_results(b) -> Path:
    """The reference-flow results dir where embed/verify CSVs live."""
    return FLOW_HOME / "results" / b.platform / b.design / b.wm_flow_variant


def _placement_xX(ref_results_dir: Path):
    """Return (X, x) from the post-finish place-ordering verify CSV.

    wm_place_order_verify_v2.csv uses a *summary* format:
        metric,value
        pairs_checked,45
        pairs_ok,45
        ...
    Fall back to parsing the per-pair embed CSV (satisfied column) when the
    summary CSV is missing or has zero counts.
    """
    p = ref_results_dir / "wm_place_order_verify_v2.csv"
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
    p2 = ref_results_dir / "wm_place_order_embed_v2.csv"
    if not p2.exists():
        return 0, 0
    big = miss = 0
    for row in csv.DictReader(open(p2)):
        if row.get("skipped_reason", "") not in ("", "already_satisfied"):
            continue
        big += 1
        if row.get("satisfied", "True") not in ("True", "true", "1"):
            miss += 1
    return big, miss


def _cts_xX(ref_results_dir: Path):
    """Return (X, x) from the CTS-watermark stage report CSV.

    wm_cts_stage_report.csv has one row per pair, with columns including
    post_final_satisfied (True/False).  We use the post_final stage as the
    ground truth (most conservative, matches what the verifier reports).
    """
    p = ref_results_dir / "wm_cts_stage_report.csv"
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
    p2 = ref_results_dir / "wm_cts_pairs_embed.csv"
    if not p2.exists():
        return 0, 0
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
    return big, miss


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
    # PDMarks methods: watermarking module log directories                #
    # ------------------------------------------------------------------ #
    pc_parts = []

    def _load_module(module: str):
        """Find and load metrics for a PDMarks module run."""
        explicit = WM_VARIANT_OVERRIDE.get(method)
        var = find_latest_wm_variant(module, plat, design) if explicit is None else explicit
        if var is None:
            return None, None
        m = _load_pdmarks_metrics(module, plat, design, var)
        return var, m

    if method == "P-only":
        module = "place_ordering"
        var, cur = _load_module(module)
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
        module = "cts_v2"
        var, cur = _load_module(module)
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
        module = "routing_wrong_way"
        var, cur = _load_module(module)
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
        pR, _ = _routing_p(ref_res)
        if pR is not None:
            out["p_R"] = pR
            pc_parts.append(pR)

    elif method == "All-stage":
        # Aggregate from all three modules; use ref from the first available module.
        any_cur = None
        ref = load_reference(plat, design, b.wm_flow_variant)
        variants_used = []

        for mod in ("place_ordering", "cts_v2", "routing_wrong_way"):
            explicit = WM_VARIANT_OVERRIDE.get(method)
            var_m = find_latest_wm_variant(mod, plat, design) if explicit is None else explicit
            if var_m is None:
                continue
            cur_m = _load_pdmarks_metrics(mod, plat, design, var_m)
            if cur_m is None:
                continue
            variants_used.append(f"{mod}:{var_m}")
            if any_cur is None:
                any_cur = cur_m   # use first module's PPA for delta

        if any_cur is None:
            out["note"] = "no all-stage run found; run experiments/drivers/run_all_stage.sh"
            return out

        out["variant"] = "; ".join(variants_used)
        out["dWNS"]     = _delta(ref.wns_ns,    any_cur.wns_ns)
        out["dTNS"]     = _delta(ref.tns_ns,    any_cur.tns_ns)
        out["dRWL"]     = _delta(ref.rwl_um,    any_cur.rwl_um)
        out["dPower"]   = _delta(ref.power_w,   any_cur.power_w)
        out["dRuntime"] = _delta(ref.runtime_s, any_cur.runtime_s)

        X_P, x_P = _placement_xX(ref_res)
        out["X_P"] = X_P; out["x_P"] = x_P
        if X_P > 0:
            pc_parts.append(pc_stage(X_P, x_P, 0.5))
        X_C, x_C = _cts_xX(ref_res)
        out["X_C"] = X_C; out["x_C"] = x_C
        if X_C > 0:
            pc_parts.append(pc_stage(X_C, x_C, 0.5))
        pR, _ = _routing_p(ref_res)
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
