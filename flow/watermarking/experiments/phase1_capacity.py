#!/usr/bin/env python3.11
# SPDX-License-Identifier: BSD-3-Clause
"""Extract per-stage watermark capacity from existing embed logs / CSVs.

For each active bench we look at:

* Placement: the most recent ``wm_log/<design>_run_place_wm_ordering_*.log``
  in ``flow/watermarking/place_ordering/wm_log/``.  The embed script logs
  three lines we care about:
      "cascade counters: raw_pairs=N ..."
      "candidate enumeration done in ...: pair_candidates=N triple_candidates=N"
      "selection done in ...: selected_pairs=N selected_groups=N"
  We use raw_pairs (+ "raw_triples" if present) as the Eligible count and
  selected_pairs/selected_groups as the Selected counts.

* CTS: the most recent ``wm_log/<design>_run_cts_wm_*.log``.  Lines (in
  preference order for ``Eligible``):
      "pre-attempt feasible: pure=P quasi_leaf=Q pure_quasi=M"     -> Eligible (preferred)
      "candidates after filters: pure=P quasi_leaf=Q pure_quasi=M" -> Eligible (legacy fallback)
      "proximity pairs pure=P quasi_leaf=Q pure_quasi=M (within ...)" -> Eligible (raw fallback)
      "attempts=N satisfied=M failed=K ..."                        -> Selected
  The "pre-attempt feasible" line is emitted by
  ``cts_watermark_embed._count_pre_attempt_feasible`` and counts pairs that
  pass the static HMAC-derived parity / boundary-FF check the attempt
  loop applies before running incremental STA -- the meaningful eligible
  denominator for paper tab:capacity.  Older embed logs without this line
  fall back to the looser "candidates after filters" or raw "proximity
  pairs" counts.
  Then for "zero_edit / reassign" split we walk the embed CSV
  ``wm_cts_pairs_embed.csv`` and count accepted rows by num_reassigned == 0
  vs > 0.

* Routing: counts the eligible routable signal nets in the post-DRT ODB and
  the selected WM_R subset (via ``experiments/results/.../watermark_nets.txt``).
  Routed segments per net come from a previously dumped ``route_counts.csv``
  (produced by experiments/tools/dump_route_counts.py).  If either file is
  missing we still emit a row with the placeholder fields filled and the
  routing fields left blank.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from bench_matrix import ACTIVE_BENCHES
from lib.orfs import (
    FLOW_HOME, flow_results, experiment_results,
    find_latest_experiment_variant,
)

OUT_DIR = HERE / "results" / "phase1" / "raw"
OUT_DIR.mkdir(parents=True, exist_ok=True)

PLACE_LOGS = FLOW_HOME / "watermarking" / "place_ordering" / "wm_log"
CTS_LOGS   = FLOW_HOME / "watermarking" / "cts_v2" / "wm_log"
ROUTE_LOGS = FLOW_HOME / "watermarking" / "routing_wrong_way" / "wm_log"


# -----------------------------------------------------------------------------
# Placement
# -----------------------------------------------------------------------------

_RX_RAW = re.compile(
    r"cascade counters:\s*raw_pairs=(?P<raw_pairs>\d+)"
    r"(?:\s+\S+){0,8}?\s+accepted=(?P<after_filter>\d+)"
)
_RX_CAND = re.compile(
    r"candidate enumeration done.*?pair_candidates=(?P<pairs>\d+)"
    r"\s+triple_candidates=(?P<triples>\d+)"
)
_RX_SEL = re.compile(
    r"selection done.*?selected_pairs=(?P<sel_pairs>\d+)"
    r"\s+selected_groups=(?P<sel_grp>\d+)"
)


def _latest_log(directory: Path, design: str, pattern: str,
                platform: str | None = None) -> Path | None:
    cands = sorted(directory.glob(pattern.format(design=design)),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if platform is None:
        return cands[0] if cands else None
    # Filter to logs that mention the requested platform.  The run scripts
    # emit a "design : DESIGN/PLATFORM/VARIANT" line near the top.
    plat_marker = f"/{platform}/"
    for p in cands:
        try:
            head = p.read_text(errors="replace")
        except Exception:
            continue
        if plat_marker in head or f"PLATFORM={platform}" in head \
                or f"platform={platform}" in head:
            return p
    return None


def collect_placement(design: str, platform: str, variant: str,
                      design_nickname: str | None = None) -> dict:
    # wm_log filenames are written by run_place_wm.sh using ${DESIGN}, so they
    # carry the full DESIGN_NAME.  Paths under experiments/results/ follow the
    # ORFS layout and use DESIGN_NICKNAME.
    nick = design_nickname or design
    log = _latest_log(PLACE_LOGS, design,
                      "{design}_run_place_wm_ordering_*.log",
                      platform=platform)
    csv_path = experiment_results(platform, nick, variant) / "wm_place_order_embed.csv"
    out = {
        "stage": "placement", "platform": platform, "design": design,
        "variant": variant,
        "eligible": "", "selected": "",
        "detail_a": "", "detail_b": "",   # Pairs / Triples
        "source_log": str(log) if log else "",
        "source_csv": str(csv_path) if csv_path.exists() else "",
    }
    if log:
        text = log.read_text(errors="replace")
        m_raw = _RX_RAW.search(text)
        m_cand = _RX_CAND.search(text)
        m_sel = _RX_SEL.search(text)
        if m_cand:
            # Eligible = post-bucket candidate pool (pairs + triples)
            out["eligible"] = int(m_cand.group("pairs")) + int(m_cand.group("triples"))
        elif m_raw:
            out["eligible"] = int(m_raw.group("raw_pairs"))
        if m_sel:
            sp = int(m_sel.group("sel_pairs"))
            sg = int(m_sel.group("sel_grp"))
            out["selected"] = sp + sg
            out["detail_a"] = sp
            out["detail_b"] = sg
    if csv_path.exists():
        pairs = groups = 0
        for row in csv.DictReader(open(csv_path)):
            if row.get("skipped_reason", "") not in ("", "already_satisfied"):
                continue
            if row.get("kind") == "group":
                groups += 1
            else:
                pairs += 1
        out["selected"] = pairs + groups
        out["detail_a"] = pairs
        out["detail_b"] = groups
    return out


# -----------------------------------------------------------------------------
# CTS
# -----------------------------------------------------------------------------

_RX_CTS_PAIRS = re.compile(
    r"proximity pairs\s+pure=(?P<pure>\d+)\s+quasi_leaf=(?P<ql>\d+)"
    r"\s+pure_quasi=(?P<pq>\d+)"
)
_RX_CTS_FILT = re.compile(
    r"candidates after filters:\s*pure=(?P<pure>\d+)\s+quasi_leaf=(?P<ql>\d+)"
    r"\s+pure_quasi=(?P<pq>\d+)"
)
# Static HMAC-derived feasibility filter (see _count_pre_attempt_feasible in
# cts_watermark_embed.py).  Preferred over the looser "candidates after
# filters" line because it captures the parity / boundary-FF check the
# attempt loop applies before running incremental STA.  This is the
# meaningful "eligible" denominator for tab:capacity.
_RX_CTS_PREFEAS = re.compile(
    r"pre-attempt feasible:\s*pure=(?P<pure>\d+)\s+quasi_leaf=(?P<ql>\d+)"
    r"\s+pure_quasi=(?P<pq>\d+)"
)
_RX_CTS_RESULT = re.compile(
    r"attempts=(?P<att>\d+)\s+satisfied=(?P<sat>\d+)\s+failed=(?P<fail>\d+)"
)


def collect_cts(design: str, platform: str, variant: str,
                design_nickname: str | None = None) -> dict:
    # Log uses full DESIGN_NAME; csv_path uses DESIGN_NICKNAME (see
    # collect_placement docstring).
    nick = design_nickname or design
    log = _latest_log(CTS_LOGS, design, "{design}_run_cts_wm_*.log",
                      platform=platform)
    csv_path = experiment_results(platform, nick, variant) / "wm_cts_pairs_embed.csv"
    out = {
        "stage": "cts", "platform": platform, "design": design,
        "variant": variant,
        "eligible": "", "selected": "",
        "detail_a": "", "detail_b": "",   # Zero-edit / Reassign
        "source_log": str(log) if log else "",
        "source_csv": str(csv_path) if csv_path.exists() else "",
    }
    if log:
        text = log.read_text(errors="replace")
        # Preference order: pre-attempt feasible (post-static-feasibility)
        # > candidates after filters (light pre-filter) > raw proximity.
        # The new "pre-attempt feasible" line is the paper-aligned eligible
        # denominator; the older two are kept as fallbacks for legacy logs.
        m = (_RX_CTS_PREFEAS.search(text)
             or _RX_CTS_FILT.search(text)
             or _RX_CTS_PAIRS.search(text))
        if m:
            out["eligible"] = (int(m.group("pure"))
                               + int(m.group("ql"))
                               + int(m.group("pq")))
        mr = _RX_CTS_RESULT.search(text)
        if mr:
            out["selected"] = int(mr.group("sat"))
    if csv_path.exists():
        ze = 0
        ra = 0
        for r in csv.DictReader(open(csv_path)):
            if r.get("skipped_reason", "") not in ("", "ok"):
                continue
            try:
                if int(r.get("num_reassigned", "0") or "0") == 0:
                    ze += 1
                else:
                    ra += 1
            except Exception:
                pass
        out["detail_a"] = ze
        out["detail_b"] = ra
        if out["selected"] == "":
            out["selected"] = ze + ra
    return out


# -----------------------------------------------------------------------------
# Routing
# -----------------------------------------------------------------------------

# Emitted by set_routing_watermark (pre_route_watermark.tcl) via OpenROAD:
#   [INFO WMK-0014] Keyed routing watermark: tagged 235 / 15031 signal nets ...
_RX_ROUTE = re.compile(
    r"\[INFO WMK-\d+\] Keyed routing watermark: tagged (?P<selected>\d+)"
    r" / (?P<eligible>\d+) signal nets"
)


def collect_routing(design: str, platform: str, variant: str,
                    design_nickname: str | None = None) -> dict:
    nick = design_nickname or design
    out = {
        "stage": "routing", "platform": platform, "design": design,
        "variant": variant,
        "eligible": "", "selected": "",
        "detail_a": "", "detail_b": "",   # RS_selected / RS_unselected
    }

    # Parse eligible / selected from the routing wm log first (most direct source).
    # Log filename uses full DESIGN_NAME (`${DESIGN}_run_route_wm_*.log`).
    log = _latest_log(ROUTE_LOGS, design, "{design}_run_route_wm_*.log",
                      platform=platform)
    if log:
        text = log.read_text(errors="replace")
        m = _RX_ROUTE.search(text)
        if m:
            out["eligible"] = int(m.group("eligible"))
            out["selected"] = int(m.group("selected"))

    # watermark_nets.txt and route_counts.csv live in the experiment result dir,
    # which mirrors the ORFS layout and uses DESIGN_NICKNAME.
    route_var = find_latest_experiment_variant(platform, nick, "pdmarks-r-only")
    if route_var:
        rdir = experiment_results(platform, nick, route_var)
        out["variant"] = route_var
    else:
        # Fall back to the reference flow results dir in case old artifacts exist.
        rdir = flow_results(platform, nick, variant)

    if not (rdir / "watermark_nets.txt").exists():
        return out

    wm_nets = {ln.strip() for ln in (rdir / "watermark_nets.txt").read_text().splitlines()
               if ln.strip()}
    # watermark_nets.txt is authoritative for the selected count.
    out["selected"] = len(wm_nets)

    rc = rdir / "route_counts.csv"
    if rc.exists():
        sel_seg = 0
        unsel_seg = 0
        eligible = 0
        for row in csv.DictReader(open(rc)):
            try:
                m = int(row["total"])
            except Exception:
                continue
            if m <= 0:
                continue
            eligible += 1
            if row["net"] in wm_nets:
                sel_seg += m
            else:
                unsel_seg += m
        out["eligible"] = eligible
        out["detail_a"] = sel_seg
        out["detail_b"] = unsel_seg
    return out


def main() -> int:
    embed_variant = os.environ.get(
        "PHASE1_EMBED_FLOW_VARIANT",
        os.environ.get("FLOW_VARIANT", "pdmarks-embed-only"),
    )
    for b in ACTIVE_BENCHES:
        for fn, suffix, variant in (
            (collect_placement, "p", embed_variant),
            (collect_cts, "c", embed_variant),
            (collect_routing, "r", b.wm_flow_variant),
        ):
            rec = fn(design=b.design, design_nickname=b.design_nickname,
                     platform=b.platform, variant=variant)
            slug = f"{b.platform}_{b.design}_{suffix}"
            (OUT_DIR / f"capacity_{slug}.json").write_text(json.dumps(rec, indent=2))
            print(f"[capacity] {slug}: eligible={rec['eligible']} "
                  f"selected={rec['selected']} ({rec['detail_a']}/{rec['detail_b']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
