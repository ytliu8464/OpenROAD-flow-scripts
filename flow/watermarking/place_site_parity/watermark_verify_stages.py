#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Verify site-parity watermark cells across multiple post-flow ODB checkpoints.

Reads the embed CSV as ground truth (cell name, tile, target_residue). Do
NOT re-run tile selection against later ODBs -- CTS/routing mutate the cell
set and would select a different pool.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from openroad import Design, Tech

import watermark_common as wc


@dataclass
class WmCell:
    cell_name: str
    master: str
    target_residue: int
    tile_tx: int
    tile_ty: int


@dataclass
class CellStageResult:
    site_col: Optional[int]
    residue: Optional[int]
    satisfied: bool
    missing: bool
    x: Optional[int] = None
    y: Optional[int] = None


def _read_cell_list_csv(path: str) -> List[WmCell]:
    out: List[WmCell] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("cell_name", "").strip()
            if not name:
                continue
            try:
                tgt = int(row.get("target_residue", "0"))
            except ValueError:
                tgt = 0
            try:
                tx = int(row.get("tile_tx", "0"))
                ty = int(row.get("tile_ty", "0"))
            except ValueError:
                tx, ty = 0, 0
            out.append(WmCell(
                cell_name=name,
                master=row.get("master", "").strip(),
                target_residue=tgt,
                tile_tx=tx,
                tile_ty=ty,
            ))
    if not out:
        raise ValueError(f"No watermark cells found in {path}")
    return out


def _parse_stages_from_env(s: str) -> List[Tuple[str, str]]:
    stages: List[Tuple[str, str]] = []
    for token in s.split(","):
        token = token.strip()
        if not token:
            continue
        if ":" not in token:
            raise ValueError(f"Invalid stage token (expected label:path): {token!r}")
        label, path = token.split(":", 1)
        label, path = label.strip(), path.strip()
        if not label or not path:
            raise ValueError(f"Invalid stage token: {token!r}")
        stages.append((label, path))
    return stages


def _infer_artifact_dir(odb_path: str, leaf: str) -> str:
    """Infer sibling ORFS artifact dir (`reports` / `logs`) from an ODB path."""
    abs_path = os.path.abspath(odb_path)
    marker = f"{os.sep}results{os.sep}"
    if marker not in abs_path:
        return ""
    return abs_path.replace(marker, f"{os.sep}{leaf}{os.sep}").rsplit(os.sep, 1)[0]


def _stage_report_name(label: str) -> str:
    low = label.lower()
    if "cts" in low:
        return "4_cts_final.rpt"
    if "grt" in low or "global" in low:
        return "5_global_route.rpt"
    if "drt" in low or "route" in low:
        return "5_route.rpt"
    if "final" in low or "finish" in low:
        return "6_finish.rpt"
    return f"{label}.rpt"


def _stage_json_name(label: str) -> str:
    low = label.lower()
    if "cts" in low:
        return "4_1_cts.json"
    if "grt" in low or "global" in low:
        return "5_1_grt.json"
    if "drt" in low or "route" in low:
        return "5_2_route.json"
    if "final" in low or "finish" in low:
        return "6_report.json"
    return f"{label}.json"


def _stage_log_candidates(label: str) -> List[str]:
    low = label.lower()
    if "cts" in low:
        return ["4_1_cts.log"]
    if "grt" in low or "global" in low:
        return ["5_1_grt.log"]
    if "drt" in low or "route" in low:
        return ["5_2_route.log", "5_2_route.tmp.log"]
    if "final" in low or "finish" in low:
        return ["6_report.log", "6_finish.log"]
    return [f"{label}.log"]


def _first_existing(paths: Sequence[str]) -> str:
    for path in paths:
        if path and os.path.isfile(path):
            return path
    return ""


def _last_json_value(data: Dict[str, object], suffixes: Sequence[str]) -> str:
    for suffix in suffixes:
        for key in reversed(list(data.keys())):
            if key.endswith(suffix):
                return str(data[key])
    return ""


def _read_text(path: str) -> str:
    if not path or not os.path.isfile(path):
        return ""
    with open(path, errors="replace") as f:
        return f.read()


def _parse_report_metrics(report_path: str) -> Dict[str, str]:
    text = _read_text(report_path)
    out = {
        "wns": "",
        "tns": "",
        "power_internal_w": "",
        "power_switching_w": "",
        "power_leakage_w": "",
        "power_total_w": "",
    }
    if not text:
        return out

    m = re.search(r"(?m)^\s*wns\s+max\s+([-+0-9.eE]+)\s*$", text)
    if m:
        out["wns"] = m.group(1)
    m = re.search(r"(?m)^\s*tns\s+max\s+([-+0-9.eE]+)\s*$", text)
    if m:
        out["tns"] = m.group(1)
    m = re.search(
        r"(?m)^\s*Total\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+"
        r"([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+100\.0%",
        text,
    )
    if m:
        out["power_internal_w"] = m.group(1)
        out["power_switching_w"] = m.group(2)
        out["power_leakage_w"] = m.group(3)
        out["power_total_w"] = m.group(4)
    return out


def _parse_json_metrics(json_path: str) -> Dict[str, str]:
    out = {
        "wns": "",
        "tns": "",
        "wirelength_um": "",
        "power_internal_w": "",
        "power_switching_w": "",
        "power_leakage_w": "",
        "power_total_w": "",
    }
    if not json_path or not os.path.isfile(json_path):
        return out
    try:
        with open(json_path) as f:
            data = json.load(f)
    except Exception:
        return out
    out["wns"] = _last_json_value(data, ["__timing__setup__ws"])
    out["tns"] = _last_json_value(data, ["__timing__setup__tns"])
    out["wirelength_um"] = _last_json_value(
        data,
        [
            "__global_route__wirelength",
            "__route__wirelength",
            "__route__wirelength__estimated",
        ],
    )
    out["power_internal_w"] = _last_json_value(data, ["__power__internal__total"])
    out["power_switching_w"] = _last_json_value(data, ["__power__switching__total"])
    out["power_leakage_w"] = _last_json_value(data, ["__power__leakage__total"])
    out["power_total_w"] = _last_json_value(data, ["__power__total"])
    return out


def _parse_log_wirelength(log_path: str) -> str:
    text = _read_text(log_path)
    if not text:
        return ""
    patterns = [
        r"Total wirelength:\s*([-+0-9.eE]+)\s*um",
        r"Total wire length\s*=\s*([-+0-9.eE]+)\s*um",
    ]
    for pat in patterns:
        matches = re.findall(pat, text, flags=re.IGNORECASE)
        if matches:
            return matches[-1]
    return ""


def _collect_stage_ppa(
    label: str,
    odb_path: str,
    reports_dir_override: str,
    logs_dir_override: str,
) -> Dict[str, str]:
    reports_dir = reports_dir_override or _infer_artifact_dir(odb_path, "reports")
    logs_dir = logs_dir_override or _infer_artifact_dir(odb_path, "logs")
    report_path = (
        os.path.join(reports_dir, _stage_report_name(label)) if reports_dir else ""
    )
    json_path = os.path.join(logs_dir, _stage_json_name(label)) if logs_dir else ""
    log_path = _first_existing(
        [os.path.join(logs_dir, name) for name in _stage_log_candidates(label)]
    ) if logs_dir else ""

    rpt = _parse_report_metrics(report_path)
    js = _parse_json_metrics(json_path)
    wirelength = js.get("wirelength_um", "") or _parse_log_wirelength(log_path)
    row = {
        "stage": label,
        "odb_path": odb_path,
        "report_path": report_path if os.path.isfile(report_path) else "",
        "json_path": json_path if os.path.isfile(json_path) else "",
        "log_path": log_path,
        "wns": rpt.get("wns", "") or js.get("wns", ""),
        "tns": rpt.get("tns", "") or js.get("tns", ""),
        "wirelength_um": wirelength,
        "power_internal_w": rpt.get("power_internal_w", "") or js.get("power_internal_w", ""),
        "power_switching_w": rpt.get("power_switching_w", "") or js.get("power_switching_w", ""),
        "power_leakage_w": rpt.get("power_leakage_w", "") or js.get("power_leakage_w", ""),
        "power_total_w": rpt.get("power_total_w", "") or js.get("power_total_w", ""),
    }
    return row


def _write_ppa_csv(path: str, rows: Sequence[Dict[str, str]]) -> None:
    header = [
        "stage",
        "odb_path",
        "report_path",
        "json_path",
        "log_path",
        "wns",
        "tns",
        "wirelength_um",
        "power_internal_w",
        "power_switching_w",
        "power_leakage_w",
        "power_total_w",
    ]
    out_dir = os.path.dirname(os.path.abspath(path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for row in rows:
            w.writerow([row.get(k, "") for k in header])


def _build_row_xmin_by_y(block) -> Dict[int, int]:
    out: Dict[int, int] = {}
    for r in block.getRows():
        y = r.getBBox().yMin()
        x = r.getBBox().xMin()
        if y not in out or x < out[y]:
            out[y] = x
    return out


def _snap_row_y(y: int, row_bottoms: Sequence[int]) -> int:
    best = row_bottoms[0]
    bd = abs(y - best)
    for rb in row_bottoms[1:]:
        d = abs(y - rb)
        if d < bd:
            best = rb
            bd = d
    return best


def _verify_one_stage(
    cells: Sequence[WmCell], odb_path: str, label: str, dbu_per_micron: int,
) -> Tuple[List[CellStageResult], List[str]]:
    tech = Tech()
    design = Design(tech)
    design.readDb(odb_path)
    block = design.getBlock()
    if block is None:
        raise RuntimeError(f"No block in {odb_path}")
    rows = list(block.getRows())
    if not rows:
        raise RuntimeError(f"No rows in {odb_path}")

    site = rows[0].getSite()
    sw = site.getWidth()
    row_bottoms = wc.sorted_row_bottoms(block)
    row_xmin_by_y = _build_row_xmin_by_y(block)
    inst_map: Dict[str, object] = {inst.getName(): inst for inst in block.getInsts()}

    results: List[CellStageResult] = []
    failures: List[str] = []
    for c in cells:
        inst = inst_map.get(c.cell_name)
        if inst is None:
            results.append(CellStageResult(
                site_col=None, residue=None, satisfied=False, missing=True,
            ))
            failures.append(f"{c.cell_name}: instance not found in {label}")
            continue
        x, y = wc.inst_bottom_left(inst)
        ry = _snap_row_y(y, row_bottoms)
        rxmin = row_xmin_by_y.get(ry, 0)
        col = int(round((x - rxmin) / float(sw)))
        res = col % wc.MODULUS
        ok = res == c.target_residue
        results.append(CellStageResult(
            site_col=col, residue=res, satisfied=ok, missing=False, x=x, y=y,
        ))
        if not ok:
            y_um = y / float(dbu_per_micron)
            failures.append(
                f"{c.cell_name}: col={col} residue={res} want={c.target_residue} "
                f"y={y} DBU ({y_um:.3f} um)"
            )
    return results, failures


def _write_report_csv(
    path: str, cells: Sequence[WmCell], stage_labels: Sequence[str],
    all_results: Sequence[Sequence[CellStageResult]],
) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        header = [
            "cell_name", "master", "tile_tx", "tile_ty", "target_residue",
        ]
        for lab in stage_labels:
            header.extend([f"{lab}_site_col", f"{lab}_residue", f"{lab}_satisfied"])
        w.writerow(header)
        for i, c in enumerate(cells):
            row: List[object] = [
                c.cell_name, c.master, c.tile_tx, c.tile_ty, c.target_residue,
            ]
            for si in range(len(stage_labels)):
                r = all_results[si][i]
                if r.missing:
                    row.extend(["", "", "False"])
                else:
                    row.extend([
                        r.site_col if r.site_col is not None else "",
                        r.residue if r.residue is not None else "",
                        r.satisfied,
                    ])
            w.writerow(row)


def main() -> int:
    p = argparse.ArgumentParser(
        description="Verify site-parity watermark across flow-stage ODBs"
    )
    p.add_argument(
        "--cell-list", default=os.environ.get("WM_CELL_LIST", ""),
    )
    p.add_argument(
        "--stage", action="append", default=None, metavar="LABEL:ODB",
    )
    p.add_argument(
        "--output-csv", default=os.environ.get("WM_STAGE_REPORT", ""),
    )
    p.add_argument(
        "--ppa-csv", default=os.environ.get("WM_STAGE_PPA_REPORT", ""),
        help="Optional per-stage PPA CSV: WNS/TNS/wirelength/power.",
    )
    p.add_argument(
        "--reports-dir", default=os.environ.get("WM_REPORTS_DIR", ""),
        help="Optional ORFS reports dir override for PPA extraction.",
    )
    p.add_argument(
        "--logs-dir", default=os.environ.get("WM_LOGS_DIR", ""),
        help="Optional ORFS logs dir override for PPA extraction.",
    )
    p.add_argument(
        "--dbu-per-micron", type=int,
        default=int(os.environ.get("WM_DBU_PER_MICRON", "2000")),
    )
    args = p.parse_args(wc.argv_after_openroad_driver())

    if not args.cell_list:
        p.error("--cell-list (or WM_CELL_LIST) is required")

    stages: List[Tuple[str, str]] = []
    if args.stage:
        for tok in args.stage:
            tok = tok.strip()
            if ":" not in tok:
                p.error(f"--stage must be LABEL:path, got {tok!r}")
            lab, path = tok.split(":", 1)
            lab, path = lab.strip(), path.strip()
            if not lab or not path:
                p.error(f"Invalid --stage {tok!r}")
            stages.append((lab, path))
    else:
        env_st = os.environ.get("WM_VERIFY_STAGES", "").strip()
        if not env_st:
            p.error(
                "No --stage given and WM_VERIFY_STAGES is empty. "
                "Provide --stage LABEL:ODB ... or WM_VERIFY_STAGES"
            )
        stages = _parse_stages_from_env(env_st)

    cells = _read_cell_list_csv(args.cell_list)
    n = len(cells)

    print(
        f"[wm_verify_stages] cells={n} from {args.cell_list}\n"
        f"{'stage':<18} {'constraints':>11} {'satisfied':>10} {'failed':>8} "
        f"{'missing':>8}  Pc(p=1/{wc.MODULUS})"
    )
    completed: List[Tuple[str, List[CellStageResult]]] = []
    ppa_rows: List[Dict[str, str]] = []
    any_fail = False
    for label, odb_path in stages:
        if args.ppa_csv:
            ppa_rows.append(
                _collect_stage_ppa(
                    label, odb_path, args.reports_dir, args.logs_dir
                )
            )
        if not os.path.isfile(odb_path):
            print(f"{label:<18} ERROR: file not found: {odb_path}", file=sys.stderr)
            any_fail = True
            continue
        try:
            results, failures = _verify_one_stage(
                cells, odb_path, label, args.dbu_per_micron
            )
        except Exception as e:
            print(f"{label:<18} ERROR: {e}", file=sys.stderr)
            any_fail = True
            continue
        completed.append((label, results))
        sat = sum(1 for r in results if r.satisfied)
        miss = sum(1 for r in results if r.missing)
        fail = n - sat
        num_fail_parity = sum(1 for r in results if not r.missing and not r.satisfied)
        pc = wc.binomial_pc(n, fail)
        print(
            f"{label:<18} {n:11d} {sat:10d} {num_fail_parity:8d} {miss:8d}  "
            f"<= {pc:.6e}"
        )
        if failures and len(failures) <= 15:
            for line in failures:
                print(f"  FAIL: {line}")
        elif failures:
            print(f"  ({len(failures)} failing cells; first 15 shown)")
            for line in failures[:15]:
                print(f"  FAIL: {line}")
        if fail > 0:
            any_fail = True

    if args.output_csv and completed and len(completed) == len(stages):
        labels = [lab for lab, _ in completed]
        all_rows = [res for _, res in completed]
        _write_report_csv(args.output_csv, cells, labels, all_rows)
        print(f"[wm_verify_stages] report -> {args.output_csv}")
    elif args.output_csv:
        print(
            "[wm_verify_stages] skipping report: not all stages completed",
            file=sys.stderr,
        )
    if args.ppa_csv:
        _write_ppa_csv(args.ppa_csv, ppa_rows)
        print(f"[wm_verify_stages] PPA report -> {args.ppa_csv}")
    return 0 if not any_fail else 2


if __name__ == "__main__":
    raise SystemExit(main())
