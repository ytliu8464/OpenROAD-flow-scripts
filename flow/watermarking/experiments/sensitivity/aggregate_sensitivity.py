#!/usr/bin/env python3.11
# SPDX-License-Identifier: BSD-3-Clause
"""Aggregate the parameter-sensitivity sweep into one wide CSV.

For each ``(platform, design, stage, knob, value)`` we pull:

  * **Capacity**: eligible + selected counts from the embedder's wm_log
    (same regexes as phase1_capacity.py).
  * **Extraction**: r_P / r_C / Z_R / p_R from the per-cell JSONs written
    by ``sensitivity/verify_sweep.py``.  ``r_all`` and the ownership pass
    are computed via the standard ``lib.thresholds.ownership_pass()``
    helper so the columns match phase3's blind/targeted CSVs.
  * **ΔPPA**: dWNS / dTNS / dRWL / dPower / dRuntime vs the bench's
    un-watermarked reference flow, computed from each cell's
    experiments/logs/<plat>/<nick>/<FLOW_VARIANT>/6_report.json via
    ``lib.orfs.load_experiment_metrics`` and ``load_reference``.
    Mirrors the delta convention in
    ``attacks/ppa/aggregate_attack_ppa.py``.

Output: ``results/phase2/sensitivity.csv``.

Set ``SENS_BACKFILL_FROM_LOGS=1`` to also rescan the legacy wm_log capacity
records when a verify JSON is missing (rare); by default this script only
reports the union of variant directories we can locate.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parents[1]   # experiments/
sys.path.insert(0, str(HERE))

from bench_matrix import SENSITIVITY_BENCHES
from lib.orfs import (
    FLOW_HOME, experiment_results,
    load_experiment_metrics, load_reference,
)
from lib.thresholds import ownership_pass

OUT_CSV = HERE / "results" / "phase2" / "sensitivity.csv"
RAW_DIR = HERE / "results" / "phase2" / "raw"

# --- Capacity-log regexes ---------------------------------------------------
PLACE_LOGS = FLOW_HOME / "watermarking" / "place_ordering" / "wm_log"
CTS_LOGS   = FLOW_HOME / "watermarking" / "cts_v2" / "wm_log"
PLACE_RX_CAND = re.compile(r"pair_candidates=(\d+)\s+triple_candidates=(\d+)")
PLACE_RX_SEL  = re.compile(r"selected_pairs=(\d+)\s+selected_groups=(\d+)")
CTS_RX_PROX   = re.compile(
    r"proximity pairs pure=(\d+)\s+quasi_leaf=(\d+)\s+pure_quasi=(\d+)")
CTS_RX_RES    = re.compile(r"attempts=(\d+)\s+satisfied=(\d+)\s+failed=(\d+)")

# --- Variant naming ---------------------------------------------------------
# sens-<stage_initial>-<knob>-<value>   e.g. sens-p-D_pair-0.5
_VAR_RX = re.compile(r"^sens-(?P<stage>p|c|r)-(?P<knob>[^-]+)-(?P<value>[^-]+)$")
_STAGE_NAME = {"p": "placement", "c": "cts", "r": "routing"}


# ---------------------------------------------------------------------------
# Capacity extraction (embed logs)
# ---------------------------------------------------------------------------

def _latest_log(glob_dir: Path, glob_pat: str, plat: str, variant: str):
    """Return the most-recent wm_log file whose first 4 KB contains both the
    platform string and the FLOW_VARIANT tag."""
    if not glob_dir.is_dir():
        return None
    cands = []
    for p in glob_dir.glob(glob_pat):
        try:
            head = p.read_text(errors="replace")[:4096]
        except Exception:
            continue
        if variant in head and f"/{plat}/" in head:
            cands.append(p)
    if not cands:
        return None
    cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0]


def _scan_capacity(stage: str, design: str, platform: str, variant: str):
    """Return (eligible, selected, detail_a, detail_b) from the matching wm_log
    or all-blanks if the log can't be found."""
    if stage == "placement":
        log = _latest_log(PLACE_LOGS, f"{design}_run_place_wm_ordering_*.log",
                          platform, variant)
        if not log:
            return "", "", "", ""
        txt = log.read_text(errors="replace")
        cand = PLACE_RX_CAND.search(txt)
        sel  = PLACE_RX_SEL.search(txt)
        elig = (int(cand.group(1)) + int(cand.group(2))) if cand else ""
        pick = (int(sel.group(1)) + int(sel.group(2))) if sel else ""
        a    = int(sel.group(1)) if sel else ""
        b    = int(sel.group(2)) if sel else ""
        return elig, pick, a, b
    if stage == "cts":
        log = _latest_log(CTS_LOGS, f"{design}_run_cts_wm_*.log",
                          platform, variant)
        if not log:
            return "", "", "", ""
        txt = log.read_text(errors="replace")
        m1 = CTS_RX_PROX.search(txt)
        m2 = CTS_RX_RES.search(txt)
        elig = (int(m1.group(1)) + int(m1.group(2)) + int(m1.group(3))) if m1 else ""
        pick = int(m2.group(2)) if m2 else ""
        return elig, pick, "", ""
    if stage == "routing":
        # Routing "selected" = count of nets in watermark_nets.txt.
        # eligible isn't separately logged; leave blank.
        rdir = FLOW_HOME / "results" / platform / "*" / variant
        # ORFS writes flow/results/<plat>/<nick>/<variant>/.  We don't know
        # the nickname here; the caller injects it.
        return "", "", "", ""
    return "", "", "", ""


def _route_selected(plat: str, nick: str, variant: str):
    rdir = FLOW_HOME / "results" / plat / nick / variant
    f = rdir / "watermark_nets.txt"
    if f.exists():
        try:
            return sum(1 for _ in open(f) if _.strip())
        except Exception:
            return ""
    return ""


# ---------------------------------------------------------------------------
# ΔPPA helper
# ---------------------------------------------------------------------------

def _delta(ref, cur):
    if ref is None or cur is None:
        return ""
    if ref == 0:
        return cur - ref
    return (cur - ref) / max(abs(ref), 1e-12)


def _ppa_deltas(b, variant: str) -> dict:
    """Return dWNS/dTNS/dRWL/dPower/dRuntime vs the un-watermarked reference."""
    try:
        ref = load_reference(b.platform, b.design_nickname, b.wm_flow_variant)
    except Exception:
        ref = None
    try:
        atk = load_experiment_metrics(b.platform, b.design_nickname, variant)
    except Exception:
        atk = None
    if ref is None or atk is None:
        return {k: "" for k in (
            "dWNS_vs_ref", "dTNS_vs_ref", "dRWL_vs_ref",
            "dPower_vs_ref", "dRuntime_vs_ref")}
    return {
        "dWNS_vs_ref":     _delta(ref.wns_ns,    atk.wns_ns),
        "dTNS_vs_ref":     _delta(ref.tns_ns,    atk.tns_ns),
        "dRWL_vs_ref":     _delta(ref.rwl_um,    atk.rwl_um),
        "dPower_vs_ref":   _delta(ref.power_w,   atk.power_w),
        "dRuntime_vs_ref": _delta(ref.runtime_s, atk.runtime_s),
    }


# ---------------------------------------------------------------------------
# Cell discovery
# ---------------------------------------------------------------------------

def _iter_variants_for(b):
    """Yield (variant, stage_initial, knob, value) for every sens-* directory
    that exists under experiments/results/<plat>/<nick>/.

    Note: experiment_results(plat, nick, "") returns ``.../<plat>/<nick>``
    (pathlib drops the empty trailing component), which is already the
    design dir.  Earlier code applied ``.parent`` here by mistake, which
    walked up to the platform dir and never found any sens-* entries.
    """
    base = experiment_results(b.platform, b.design_nickname, "")
    if not base.is_dir():
        return
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        m = _VAR_RX.match(d.name)
        if not m:
            continue
        yield d.name, m.group("stage"), m.group("knob"), m.group("value")


def _load_verify_json(b, stage_initial: str, knob: str, value: str) -> dict:
    p = RAW_DIR / f"sens_{stage_initial}_{knob}_{value}_{b.platform}_{b.design}.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "platform", "design", "stage", "knob", "value", "variant",
        "eligible", "selected", "detail_a", "detail_b",
        "r_P", "r_C", "Z_R", "p_R", "r_R", "r_all",
        "pass_P", "pass_C", "pass_R", "num_pass", "pass_all", "accept",
        "dWNS_vs_ref", "dTNS_vs_ref", "dRWL_vs_ref",
        "dPower_vs_ref", "dRuntime_vs_ref",
        "note",
    ]
    rows: list = []
    for b in SENSITIVITY_BENCHES:
        for variant, stage_initial, knob, value in _iter_variants_for(b):
            stage = _STAGE_NAME[stage_initial]
            # Capacity
            elig, pick, a, bx = _scan_capacity(stage, b.design, b.platform, variant)
            if stage == "routing":
                pick = _route_selected(b.platform, b.design_nickname, variant)
            # Extraction
            v = _load_verify_json(b, stage_initial, knob, value)
            rP = v.get("r_P"); rC = v.get("r_C")
            ZR = v.get("Z_R"); pR = v.get("p_R")
            note = v.get("note", "")
            # r_R / r_all / ownership decision via shared helper
            own = ownership_pass(
                rP if isinstance(rP, (int, float)) else None,
                rC if isinstance(rC, (int, float)) else None,
                pR if isinstance(pR, (int, float)) else None,
                alpha_R=0.05,
            )
            row = {
                "platform": b.platform, "design": b.design,
                "stage": stage, "knob": knob, "value": value,
                "variant": variant,
                "eligible": elig, "selected": pick,
                "detail_a": a, "detail_b": bx,
                "r_P": "" if rP is None else rP,
                "r_C": "" if rC is None else rC,
                "Z_R": "" if ZR is None else ZR,
                "p_R": "" if pR is None else pR,
                "r_R":     own.get("r_R", ""),
                "r_all":   own.get("r_all", ""),
                "pass_P":  int(bool(own.get("pass_P", False))),
                "pass_C":  int(bool(own.get("pass_C", False))),
                "pass_R":  int(bool(own.get("pass_R", False))),
                "num_pass":own.get("num_pass", 0),
                "pass_all":int(bool(own.get("pass_all", False))),
                "accept":  int(bool(own.get("accept", False))),
                "note": note,
            }
            row.update(_ppa_deltas(b, variant))
            rows.append(row)

    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    print(f"[sensitivity] wrote {OUT_CSV} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
