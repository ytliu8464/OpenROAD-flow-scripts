# SPDX-License-Identifier: BSD-3-Clause
"""Emit copy-pasteable LaTeX cell strings for each paper table.

Reads the aggregated CSVs produced by ``aggregate.py`` and writes
``results/<phase>/<table>.tex`` containing one row per benchmark in the
expected paper order.  We never edit main.tex; the user copies the cell
content into the appropriate table by hand.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Dict, List, Optional

from bench_matrix import ACTIVE_BENCHES, PAPER_PLACEHOLDERS


def _fmt(v, prec=3, na="--"):
    if v is None or v == "":
        return na
    try:
        f = float(v)
    except Exception:
        return str(v)
    if prec == 0:
        return f"{int(round(f)):,}"
    if f == 0:
        return f"{0.0:.{prec}f}"
    return f"{f:.{prec}f}"


def render_exp_setup(csv_path: Path, out_path: Path) -> None:
    rows = list(csv.DictReader(open(csv_path)))
    by_key = {(r["platform"], r["design"], r["variant"]): r for r in rows}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        for b in ACTIVE_BENCHES:
            r = by_key.get((b.platform, b.design, b.wm_flow_variant), {})
            cells = [
                b.paper_label,
                _fmt(r.get("n_std_cells"), 0),
                _fmt(r.get("n_macros"), 0),
                _fmt(r.get("n_nets"), 0),
                _fmt(r.get("tcp_ns")),
                _fmt(r.get("wns_ns")),
                _fmt(r.get("tns_ns")),
                _fmt(r.get("rwl_um"), 0),
                _fmt(r.get("power_w")),
                _fmt(r.get("runtime_s"), 0),
            ]
            f.write(" & ".join(cells) + " \\\\\n")


def render_capacity(csv_path: Path, out_path: Path) -> None:
    rows = list(csv.DictReader(open(csv_path)))
    by_key = {(r["stage"], r["platform"], r["design"]): r for r in rows}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        for stage in ("placement", "cts", "routing"):
            for b in ACTIVE_BENCHES:
                r = by_key.get((stage, b.platform, b.design), {})
                cells = [
                    stage.upper(),
                    b.paper_label,
                    _fmt(r.get("eligible"), 0),
                    _fmt(r.get("selected"), 0),
                    _fmt(r.get("detail_a"), 0),
                    _fmt(r.get("detail_b"), 0),
                ]
                f.write(" & ".join(cells) + " \\\\\n")


def render_ppa(csv_path: Path, out_path: Path) -> None:
    """Render Table V (PPA overhead) rows in paper order.

    Paper row order:
      Kahng        -- left blank (not implemented)
      Cell-scattering
      Buffer-insertion
      ICMarks      -- left blank (not implemented)
      AutoMarks    -- left blank (not implemented)
      P-only  }
      C-only  }  PDMarks variants
      R-only  }
      All-stage }
    """
    METHODS = (
        "Kahng",             # blank -- not implemented
        "Cell-scattering",
        "Buffer-insertion",
        "ICMarks",           # blank -- not implemented
        "AutoMarks",         # blank -- not implemented
        "P-only",
        "C-only",
        "R-only",
        "All-stage",
    )
    rows = list(csv.DictReader(open(csv_path)))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        for b in ACTIVE_BENCHES:
            for method in METHODS:
                r = next(
                    (rw for rw in rows
                     if rw["platform"] == b.platform
                     and rw["design"] == b.design
                     and rw["method"] == method),
                    {},
                )
                cells = [
                    b.paper_label,
                    method,
                    _fmt(r.get("dWNS")),
                    _fmt(r.get("dTNS")),
                    _fmt(r.get("dRWL")),
                    _fmt(r.get("dPower")),
                    _fmt(r.get("dRuntime")),
                    _fmt(r.get("Pc")),
                ]
                f.write(" & ".join(cells) + " \\\\\n")


def render_survival(csv_path: Path, out_path: Path) -> None:
    rows = list(csv.DictReader(open(csv_path)))
    by_key = {(r["platform"], r["design"], r["evidence"], r["stage"]): r
              for r in rows}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        for b in ACTIVE_BENCHES:
            for evidence in ("r_P", "r_C", "Z_R,p_R", "r_all"):
                cells = [b.paper_label, evidence]
                for stg in ("post_place", "post_cts", "post_grt", "post_drt"):
                    r = by_key.get((b.platform, b.design, evidence, stg), {})
                    cells.append(_fmt(r.get("value")))
                f.write(" & ".join(cells) + " \\\\\n")


def render_wrong_key(csv_path: Path, out_path: Path) -> None:
    rows = list(csv.DictReader(open(csv_path)))
    by_key = {(r["platform"], r["design"]): r for r in rows}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        for b in ACTIVE_BENCHES:
            r = by_key.get((b.platform, b.design), {})
            cells = [b.paper_label,
                     _fmt(r.get("true_r_P")), _fmt(r.get("true_r_C")),
                     _fmt(r.get("true_p_R")),
                     _fmt(r.get("true_Pc")),
                     _fmt(r.get("false_positive_rate"))]
            f.write(" & ".join(cells) + " \\\\\n")


def render_attack(csv_path: Path, out_path: Path,
                  fields: tuple = ("r_P", "r_C", "Z_R", "p_R")) -> None:
    rows = list(csv.DictReader(open(csv_path)))
    by_key = {(r["platform"], r["design"], r["stage"], r["q_s"]): r
              for r in rows}
    qs_list = sorted({r["q_s"] for r in rows}, key=lambda x: float(x or 0))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        for b in ACTIVE_BENCHES:
            for stg in ("placement", "cts", "routing"):
                for q in qs_list:
                    r = by_key.get((b.platform, b.design, stg, q), {})
                    cells = [b.paper_label, stg, q]
                    for fk in fields:
                        cells.append(_fmt(r.get(fk)))
                    f.write(" & ".join(str(c) for c in cells) + " \\\\\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-root",
                    default=str(Path(__file__).resolve().parent / "results"))
    args = ap.parse_args()
    root = Path(args.results_root)

    if (root / "phase1" / "summary_ref.csv").exists():
        render_exp_setup(root / "phase1" / "summary_ref.csv",
                         root / "phase1" / "tab_exp_setup.tex")
    if (root / "phase1" / "capacity.csv").exists():
        render_capacity(root / "phase1" / "capacity.csv",
                        root / "phase1" / "tab_capacity.tex")
    for plat in ("nangate45", "asap7"):
        p = root / "phase1" / f"ppa_{plat}.csv"
        if p.exists():
            render_ppa(p, root / "phase1" / f"tab_ppa_{plat}.tex")
    if (root / "phase1" / "survival.csv").exists():
        render_survival(root / "phase1" / "survival.csv",
                        root / "phase1" / "tab_survival.tex")
    if (root / "phase2" / "wrong_key.csv").exists():
        render_wrong_key(root / "phase2" / "wrong_key.csv",
                         root / "phase2" / "tab_wrong_key.tex")
    if (root / "phase3" / "blind.csv").exists():
        render_attack(root / "phase3" / "blind.csv",
                      root / "phase3" / "tab_blind_attack.tex")
    if (root / "phase3" / "targeted.csv").exists():
        render_attack(root / "phase3" / "targeted.csv",
                      root / "phase3" / "tab_targeted_attack.tex",
                      fields=("Z_R", "p_R", "precision_topK"))

    # Index document so the user knows which file feeds which paper label.
    idx = root / "tables_index.md"
    idx.write_text(
        "# PDMarks LaTeX cell fragments\n\n"
        "Each row in these files corresponds to one row in the cited paper\n"
        "table.  Copy the cell content into `main.tex` by hand.\n\n"
        "| Paper table          | File                                              |\n"
        "|----------------------|---------------------------------------------------|\n"
        "| tab:exp_setup        | phase1/tab_exp_setup.tex                          |\n"
        "| tab:capacity         | phase1/tab_capacity.tex                           |\n"
        "| tab:ppa_ng45         | phase1/tab_ppa_nangate45.tex                      |\n"
        "| tab:ppa_asap7        | phase1/tab_ppa_asap7.tex                          |\n"
        "| tab:survival         | phase1/tab_survival.tex                           |\n"
        "| tab:wrong-key        | phase2/tab_wrong_key.tex                          |\n"
        "| tab:blind_attack     | phase3/tab_blind_attack.tex                       |\n"
        "| tab:targeted_attack  | phase3/tab_targeted_attack.tex                    |\n"
        "| fig:wrong_key_analysis | plots/wrong_key_analysis.png                   |\n"
    )
    print(f"[render_tex] wrote LaTeX cell fragments + index under {root}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
