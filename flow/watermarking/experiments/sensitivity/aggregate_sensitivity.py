#!/usr/bin/env python3.11
# SPDX-License-Identifier: BSD-3-Clause
"""Aggregate the parameter-sensitivity sweep into per-stage CSVs.

For each (design, stage, knob, value) we read the corresponding tagged log
under flow/watermarking/<stage>/wm_log/*<tag>*.log and extract the eligible /
selected counts (same regexes as phase1_capacity.py).  Output CSV columns:

    design,platform,stage,knob,value,eligible,selected,detail_a,detail_b

PPA columns (dWNS / dTNS / dRWL / dPower) are filled in only when SENS_PPA=1
was set during the sweep run (each parameter point then triggers a P-only /
C-only / R-only PPA flow).  Otherwise those columns stay empty.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from bench_matrix import SENSITIVITY_BENCHES
from lib.orfs import FLOW_HOME

OUT_CSV = HERE / "results" / "phase2" / "sensitivity.csv"

PLACE_LOGS = FLOW_HOME / "watermarking" / "place_ordering" / "wm_log"
CTS_LOGS   = FLOW_HOME / "watermarking" / "cts_v2" / "wm_log"

PLACE_RX_RAW = re.compile(r"raw_pairs=(\d+).*?accepted=(\d+).*?tiles_capped=\d+")
PLACE_RX_CAND = re.compile(r"pair_candidates=(\d+)\s+triple_candidates=(\d+)")
PLACE_RX_SEL = re.compile(r"selected_pairs=(\d+)\s+selected_groups=(\d+)")

CTS_RX_PROX = re.compile(
    r"proximity pairs pure=(\d+)\s+quasi_leaf=(\d+)\s+pure_quasi=(\d+)")
CTS_RX_RES = re.compile(r"attempts=(\d+)\s+satisfied=(\d+)\s+failed=(\d+)")


def _scan_place(design: str, platform: str, tag: str):
    cands = sorted(
        [p for p in PLACE_LOGS.glob(f"{design}_run_place_wm_ordering_*.log")
         if tag in p.read_text(errors="replace")[:4000]
         and f"/{platform}/" in p.read_text(errors="replace")[:4000]],
        key=lambda p: p.stat().st_mtime, reverse=True)
    if not cands:
        return None
    txt = cands[0].read_text(errors="replace")
    cand = PLACE_RX_CAND.search(txt)
    sel  = PLACE_RX_SEL.search(txt)
    elig = (int(cand.group(1)) + int(cand.group(2))) if cand else ""
    pick = (int(sel.group(1)) + int(sel.group(2))) if sel else ""
    a    = int(sel.group(1)) if sel else ""
    b    = int(sel.group(2)) if sel else ""
    return elig, pick, a, b


def _scan_cts(design: str, platform: str, tag: str):
    cands = sorted(
        [p for p in CTS_LOGS.glob(f"{design}_run_cts_wm_*.log")
         if tag in p.read_text(errors="replace")[:4000]
         and f"/{platform}/" in p.read_text(errors="replace")[:4000]],
        key=lambda p: p.stat().st_mtime, reverse=True)
    if not cands:
        return None
    txt = cands[0].read_text(errors="replace")
    m1 = CTS_RX_PROX.search(txt)
    m2 = CTS_RX_RES.search(txt)
    elig = (int(m1.group(1)) + int(m1.group(2)) + int(m1.group(3))) if m1 else ""
    pick = int(m2.group(2)) if m2 else ""
    return elig, pick, "", ""


def main():
    out_dir = OUT_CSV.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    fields = ["platform", "design", "stage", "knob", "value",
              "eligible", "selected", "detail_a", "detail_b"]
    rows = []
    for b in SENSITIVITY_BENCHES:
        for knob, values in (
            ("pairs_per_tile", [2, 4, 8]),
            ("grid",           [4, 6, 8]),
            ("hpwl_eps",       [50, 100, 200]),
        ):
            tag_prefix = {"pairs_per_tile": "sens_p_ppt",
                          "grid": "sens_p_grid",
                          "hpwl_eps": "sens_p_hpwl"}[knob]
            for v in values:
                res = _scan_place(b.design, b.platform, f"{tag_prefix}{v}")
                if res is None:
                    rows.append({"platform": b.platform, "design": b.design,
                                 "stage": "placement", "knob": knob, "value": v,
                                 "eligible": "", "selected": "",
                                 "detail_a": "", "detail_b": ""})
                    continue
                elig, pick, a, b_ = res
                rows.append({"platform": b.platform, "design": b.design,
                             "stage": "placement", "knob": knob, "value": v,
                             "eligible": elig, "selected": pick,
                             "detail_a": a, "detail_b": b_})
        for knob, values in (("pairs", [16, 32, 64]),
                             ("sibling_um", [25, 50, 100])):
            tag_prefix = {"pairs": "sens_c_pairs",
                          "sibling_um": "sens_c_sib"}[knob]
            for v in values:
                res = _scan_cts(b.design, b.platform, f"{tag_prefix}{v}")
                if res is None:
                    rows.append({"platform": b.platform, "design": b.design,
                                 "stage": "cts", "knob": knob, "value": v,
                                 "eligible": "", "selected": "",
                                 "detail_a": "", "detail_b": ""})
                    continue
                elig, pick, a, b_ = res
                rows.append({"platform": b.platform, "design": b.design,
                             "stage": "cts", "knob": knob, "value": v,
                             "eligible": elig, "selected": pick,
                             "detail_a": a, "detail_b": b_})
        # Routing sweeps are derived from PPA variant dirs (sens-r-f*, sens-r-lwm*)
        for f in (0.025, 0.05, 0.10):
            rdir = FLOW_HOME / "results" / b.platform / b.design / f"sens-r-f{f}"
            nets = ""
            if (rdir / "watermark_nets.txt").exists():
                nets = sum(1 for _ in open(rdir / "watermark_nets.txt"))
            rows.append({"platform": b.platform, "design": b.design,
                         "stage": "routing", "knob": "fraction", "value": f,
                         "eligible": "", "selected": nets,
                         "detail_a": "", "detail_b": ""})
        for s in (10, 100, 1000):
            rdir = FLOW_HOME / "results" / b.platform / b.design / f"sens-r-lwm{s}"
            nets = ""
            if (rdir / "watermark_nets.txt").exists():
                nets = sum(1 for _ in open(rdir / "watermark_nets.txt"))
            rows.append({"platform": b.platform, "design": b.design,
                         "stage": "routing", "knob": "strength", "value": s,
                         "eligible": "", "selected": nets,
                         "detail_a": "", "detail_b": ""})
    with open(OUT_CSV, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=fields)
        wr.writeheader()
        wr.writerows(rows)
    print(f"[sensitivity] wrote {OUT_CSV} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
