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


def _fmt_sci(v, prec=3, na="--"):
    if v is None or v == "":
        return na
    try:
        f = float(v)
    except Exception:
        return str(v)
    if f == 0:
        return "0"
    if abs(f) < 1e-3 or abs(f) >= 1e4:
        return f"{f:.{prec}e}"
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
                    _fmt_sci(r.get("Pc")),
                ]
                f.write(" & ".join(cells) + " \\\\\n")


def render_survival(csv_path: Path, out_path: Path) -> None:
    rows = list(csv.DictReader(open(csv_path)))
    by_key = {(r["platform"], r["design"], r["evidence"], r["stage"]): r
              for r in rows}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        for b in ACTIVE_BENCHES:
            evidences = ["r_P", "r_C"]
            if b.platform == "nangate45":
                evidences.append("Z_R,p_R")
            evidences.append("r_all")
            for evidence in evidences:
                cells = [b.paper_label, evidence]
                for stg in ("post_place", "post_cts", "post_grt", "post_drt"):
                    r = by_key.get((b.platform, b.design, evidence, stg), {})
                    cells.append(_fmt(r.get("value")))
                f.write(" & ".join(cells) + " \\\\\n")


def render_wrong_key(csv_path: Path, out_path: Path) -> None:
    rows = list(csv.DictReader(open(csv_path)))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        # Preferred schema for the current paper table:
        # evidence, wrong_key_mean, wrong_key_max, correct_key_mean, threshold.
        if rows and "evidence" in rows[0]:
            for r in rows:
                evidence = r.get("evidence", "")
                formatter = _fmt_sci if evidence == "p_R" else _fmt
                cells = [
                    evidence,
                    formatter(r.get("wrong_key_mean")),
                    formatter(r.get("wrong_key_max")),
                    formatter(r.get("correct_key_mean")),
                    r.get("threshold", ""),
                ]
                f.write(" & ".join(cells) + " \\\\\n")
            return

        # Legacy per-design schema retained for older CSVs.
        by_key = {(r["platform"], r["design"]): r for r in rows}
        for b in ACTIVE_BENCHES:
            r = by_key.get((b.platform, b.design), {})
            cells = [b.paper_label,
                     _fmt(r.get("true_r_P")), _fmt(r.get("true_r_C")),
                     _fmt(r.get("true_p_R")),
                     _fmt(r.get("true_Pc")),
                     _fmt(r.get("false_positive_rate"))]
            f.write(" & ".join(cells) + " \\\\\n")


def render_sensitivity(csv_path: Path, out_path: Path) -> None:
    """One row per (platform, stage, knob, value) -> LaTeX cells.

    Reads results/phase2/sensitivity.csv (produced by
    sensitivity/aggregate_sensitivity.py) and emits a flat ``& ... \\\\``
    fragment for paper tab:sensitivity.  Rows are grouped first by stage,
    then by knob, then by value, then by platform -- this matches the
    expected paper layout (one row per sweep point; both platforms share
    a knob block).

    Columns: stage, knob, value, platform, eligible, selected,
             r_P/r_C/Z_R, r_all, dWNS_vs_ref, dTNS_vs_ref,
             dRWL_vs_ref, dPower_vs_ref.

    For routing rows, ``r_X`` is reported as ``Z_R``; for placement /
    CTS, the corresponding r_P or r_C.  Other r columns blank.
    """
    rows = list(csv.DictReader(open(csv_path)))
    # Group key: (stage, knob, value, platform); sorted so the paper
    # layout is deterministic.
    def _val(r):
        try:
            return float(r["value"])
        except (KeyError, ValueError, TypeError):
            return r.get("value", "")
    rows.sort(key=lambda r: (r.get("stage", ""), r.get("knob", ""),
                              _val(r), r.get("platform", "")))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        for r in rows:
            stage = r.get("stage", "")
            if stage == "placement":
                r_x = _fmt(r.get("r_P"))
            elif stage == "cts":
                r_x = _fmt(r.get("r_C"))
            elif stage == "routing":
                r_x = _fmt(r.get("Z_R"))
            else:
                r_x = "--"
            cells = [
                stage, r.get("knob", ""), r.get("value", ""),
                r.get("platform", ""),
                _fmt(r.get("eligible")),
                _fmt(r.get("selected")),
                r_x,
                _fmt(r.get("r_all")),
                _fmt(r.get("dWNS_vs_ref")),
                _fmt(r.get("dTNS_vs_ref")),
                _fmt(r.get("dRWL_vs_ref")),
                _fmt(r.get("dPower_vs_ref")),
            ]
            f.write(" & ".join(str(c) for c in cells) + " \\\\\n")


def render_attack(csv_path: Path, out_path: Path,
                  fields: tuple = ("r_P", "r_C", "Z_R", "p_R"),
                  stages: tuple = ("placement", "cts", "routing"),
                  ppa_csv: Optional[Path] = None,
                  ppa_stage_map: Optional[dict] = None,
                  ppa_fields: tuple = ("dWNS_vs_wm", "dTNS_vs_wm",
                                       "dRWL_vs_wm", "dPower_vs_wm")) -> None:
    """Render one row per (design, stage, q_s) into a LaTeX fragment.

    ``fields`` are pulled straight from ``csv_path``.  When ``ppa_csv`` is
    given, the matching ΔPPA columns are joined in: each row's
    ``(platform, design, stage, q_s)`` is looked up in the PPA CSV after
    mapping ``stage`` via ``ppa_stage_map`` (e.g. ``placement -> targeted_placement``)
    so the targeted-attack fragment can carry the §7 PPA columns from
    ``blind_ppa.csv``.
    """
    rows = list(csv.DictReader(open(csv_path)))
    by_key = {(r["platform"], r["design"], r["stage"], r["q_s"]): r
              for r in rows}
    qs_list = sorted({r["q_s"] for r in rows}, key=lambda x: float(x or 0))

    ppa_by_key: dict = {}
    if ppa_csv is not None and ppa_csv.exists():
        for pr in csv.DictReader(open(ppa_csv)):
            ppa_by_key[(pr["platform"], pr["design"], pr["stage"], pr["q_s"])] = pr
    stage_map = ppa_stage_map or {}

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        for b in ACTIVE_BENCHES:
            for stg in stages:
                for q in qs_list:
                    r = by_key.get((b.platform, b.design, stg, q), {})
                    cells = [b.paper_label, stg, q]
                    for fk in fields:
                        cells.append(_fmt(r.get(fk)))
                    if ppa_csv is not None:
                        ppa_stage = stage_map.get(stg, stg)
                        pr = ppa_by_key.get(
                            (b.platform, b.design, ppa_stage, q), {})
                        for fk in ppa_fields:
                            cells.append(_fmt(pr.get(fk)))
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
    wrong_key_table = root / "phase2" / "wrong_key_table.csv"
    wrong_key_csv = root / "phase2" / "wrong_key.csv"
    if wrong_key_table.exists():
        render_wrong_key(wrong_key_table,
                         root / "phase2" / "tab_wrong_key.tex")
    elif wrong_key_csv.exists():
        render_wrong_key(wrong_key_csv,
                         root / "phase2" / "tab_wrong_key.tex")
    sens_csv = root / "phase2" / "sensitivity.csv"
    if sens_csv.exists():
        render_sensitivity(sens_csv,
                            root / "phase2" / "tab_sensitivity.tex")
    blind_ppa_csv = root / "phase3" / "blind_ppa.csv"
    if (root / "phase3" / "blind.csv").exists():
        # Blind ΔPPA columns are also in blind_ppa.csv -- stage names line up
        # 1:1 (placement / cts / routing) so no remap is needed.
        render_attack(root / "phase3" / "blind.csv",
                      root / "phase3" / "tab_blind_attack.tex",
                      ppa_csv=blind_ppa_csv)
    if (root / "phase3" / "targeted.csv").exists():
        # Paper-aligned tab:targeted_attack (§7.2): per-stage r-rates + r_all
        # + accept decision joined with the matching ΔPPA columns from
        # blind_ppa.csv (which carries atk-{tp,tc,tr}-* entries under stages
        # targeted_{placement,cts,routing}).
        render_attack(root / "phase3" / "targeted.csv",
                      root / "phase3" / "tab_targeted_attack.tex",
                      fields=("r_P", "r_C", "Z_R", "p_R", "r_all", "accept"),
                      ppa_csv=blind_ppa_csv,
                      ppa_stage_map={"placement": "targeted_placement",
                                     "cts":       "targeted_cts",
                                     "routing":   "targeted_routing"})

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
        "| tab:sensitivity      | phase2/tab_sensitivity.tex                        |\n"
        "| tab:blind_attack     | phase3/tab_blind_attack.tex                       |\n"
        "| tab:targeted_attack  | phase3/tab_targeted_attack.tex                    |\n"
        "| fig:wrong_key_analysis | plots/wrong_key_analysis.png                   |\n"
    )
    print(f"[render_tex] wrote LaTeX cell fragments + index under {root}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
