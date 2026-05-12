# SPDX-License-Identifier: BSD-3-Clause
"""Benchmark matrix for PDMarks experiments.

Single source of truth for which (platform, design, wm_flow_variant) triples are
considered "active" (have a reference run already on disk) versus listed in the
paper but not yet implemented in this repository.

The paper Table tab:exp_setup expects {AES, JPEG, SweRV, Ariane, BP} x
{NanGate45, ASAP7} = 10 cells.  This module exposes:

- ACTIVE_BENCHES: the 7 cells whose reference flow already lives under
  ``flow/results/<platform>/<design>/<variant>``.  Aggregators run only over
  this list by default.
- PAPER_BENCHES: the full 10-cell matrix used to lay out the LaTeX tables.
  Missing cells are emitted as ``n/a`` rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class Bench:
    platform: str           # "nangate45" or "asap7"
    design: str             # ORFS design directory name
    wm_flow_variant: str    # ORFS FLOW_VARIANT for the reference run
    paper_design: str       # display name used in the paper tables
    paper_platform: str     # display platform name in paper ("NG45" or "ASAP7")

    @property
    def key(self) -> str:
        return f"{self.platform}/{self.design}/{self.wm_flow_variant}"

    @property
    def paper_label(self) -> str:
        return f"{self.paper_design} ({self.paper_platform})"


# Cells whose reference 6_report.json already exists.  Phase 1+ runs over this
# list.  Adding a new design later only requires appending to this list (and
# running run_ref.sh once for the new design).
ACTIVE_BENCHES: List[Bench] = [
    Bench("nangate45", "aes",            "watermarking-test1", "AES",    "NG45"),
    Bench("nangate45", "jpeg",           "watermarking-test1", "JPEG",   "NG45"),
    Bench("nangate45", "swerv_wrapper",  "base",               "SweRV",  "NG45"),
    Bench("nangate45", "ariane136",      "base_tcp3p5",        "Ariane", "NG45"),
    Bench("nangate45", "bp_quad",        "base",        "BP",     "NG45"),
    Bench("asap7",     "aes",            "base",               "AES",    "ASAP7"),
    Bench("asap7",     "jpeg",           "base_tcp540",        "JPEG",   "ASAP7"),
    Bench("asap7",     "swerv_wrapper",  "base_tcp1455",       "SweRV",  "ASAP7"),
    # Bench("asap7",     "ariane",  "base",       "Ariane",  "ASAP7"),
    Bench("asap7",     "cva6",  "base_tcp950",       "CVA6",  "ASAP7"),
]


# Paper layout (10 cells).  None means "no reference yet"; the harness emits an
# n/a row for these so the table structure stays consistent.
PAPER_BENCHES: List[Bench] = ACTIVE_BENCHES + [
    Bench("nangate45", "bp_quad",       "base", "BP",     "NG45"),   # active
    # Cells not yet implemented in this repo: leave as placeholders.
]

PAPER_PLACEHOLDERS: List[tuple] = [
    # (paper_design, paper_platform)
    ("BP",     "NG45"),     # has a base run, may upgrade later
    ("Ariane", "ASAP7"),
    ("BP",     "ASAP7"),
]


def get_active() -> List[Bench]:
    return list(ACTIVE_BENCHES)


def find_active(platform: str, design: str) -> Optional[Bench]:
    for b in ACTIVE_BENCHES:
        if b.platform == platform and b.design == design:
            return b
    return None


# Subset used for the parameter-sensitivity sweep (Section 3.2 of the plan).
SENSITIVITY_BENCHES: List[Bench] = [
    Bench("nangate45", "swerv_wrapper", "base",         "SweRV", "NG45"),
    Bench("asap7",     "swerv_wrapper", "base_tcp1455", "SweRV", "ASAP7"),
]
