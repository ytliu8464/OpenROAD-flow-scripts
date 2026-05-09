#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Verify ordering watermark across multiple flow-stage ODB checkpoints."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from openroad import Design, Tech

import watermark_common as wc
import watermark_verify as wv


@dataclass
class WmConstraint:
    kind: str
    a: str
    b: str
    c: str
    target_bit: int
    target_perm: int


def _read_constraints_csv(path: str) -> List[WmConstraint]:
    rows = wv._read_csv(path)
    out: List[WmConstraint] = []
    for row in rows:
        if not wv._should_verify_row(row.get("skipped_reason", "")):
            continue
        kind = row.get("kind", "").strip()
        if kind == "pair":
            try:
                tb = int(row.get("target_bit", "0"))
            except ValueError:
                tb = 0
            out.append(WmConstraint(
                kind="pair",
                a=row.get("A_name", "").strip(),
                b=row.get("B_name", "").strip(),
                c="",
                target_bit=tb,
                target_perm=-1,
            ))
        elif kind == "triple":
            try:
                tp = int(row.get("target_perm", "0"))
            except ValueError:
                tp = 0
            out.append(WmConstraint(
                kind="triple",
                a=row.get("A_name", "").strip(),
                b=row.get("B_name", "").strip(),
                c=row.get("C_name", "").strip(),
                target_bit=-1,
                target_perm=tp,
            ))
    return out


def _parse_stages_from_env(s: str) -> List[Tuple[str, str]]:
    stages: List[Tuple[str, str]] = []
    for token in s.split(","):
        token = token.strip()
        if not token:
            continue
        if ":" not in token:
            raise ValueError(f"Invalid stage token: {token!r}")
        label, path = token.split(":", 1)
        label, path = label.strip(), path.strip()
        if label and path:
            stages.append((label, path))
    return stages


def _infer_artifact_dir(odb_path: str, leaf: str) -> str:
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
    for pat in (
        r"Total wirelength:\s*([-+0-9.eE]+)\s*um",
        r"Total wire length\s*=\s*([-+0-9.eE]+)\s*um",
    ):
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
    return {
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


def _write_ppa_csv(path: str, rows: Sequence[Dict[str, str]]) -> None:
    header = [
        "stage", "odb_path", "report_path", "json_path", "log_path",
        "wns", "tns", "wirelength_um",
        "power_internal_w", "power_switching_w", "power_leakage_w", "power_total_w",
    ]
    out_dir = os.path.dirname(os.path.abspath(path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for row in rows:
            w.writerow([row.get(k, "") for k in header])


def _verify_stage(constraints: Sequence[WmConstraint], odb_path: str) -> Tuple[int, int, List[str]]:
    tech = Tech()
    design = Design(tech)
    design.readDb(odb_path)
    block = design.getBlock()
    if block is None:
        raise RuntimeError(f"No block in {odb_path}")
    inst_map = {i.getName(): i for i in block.getInsts()}
    ok, total = 0, 0
    fails: List[str] = []
    for c in constraints:
        if c.kind == "pair":
            ia, ib = inst_map.get(c.a), inst_map.get(c.b)
            if ia is None or ib is None:
                fails.append(f"missing {c.a} or {c.b}")
                total += 1
                continue
            total += 1
            cur = wv.pair_bit_from_inst(ia, ib)
            if cur == c.target_bit:
                ok += 1
            else:
                fails.append(f"pair {c.a}|{c.b}: bit={cur} want={c.target_bit}")
        else:
            ia, ib, ic = inst_map.get(c.a), inst_map.get(c.b), inst_map.get(c.c)
            if None in (ia, ib, ic):
                fails.append(f"missing triple {c.a},{c.b},{c.c}")
                total += 1
                continue
            total += 1
            exp = wc.permuted_order_names(
                tuple(sorted((c.a, c.b, c.c))),
                c.target_perm,
            )
            obs = wc.order_names_left_to_right((ia, ib, ic))
            if obs == exp:
                ok += 1
            else:
                fails.append(f"triple: order={obs} want={exp}")
    return ok, total, fails


def main() -> int:
    p = argparse.ArgumentParser(description="Verify ordering watermark across stages")
    p.add_argument("--cell-list", default=os.environ.get("WM_CELL_LIST", ""))
    p.add_argument("--stage", action="append", default=None, metavar="LABEL:ODB")
    p.add_argument("--output-csv", default=os.environ.get("WM_STAGE_REPORT", ""))
    p.add_argument("--ppa-csv", default=os.environ.get("WM_STAGE_PPA_REPORT", ""))
    p.add_argument("--reports-dir", default=os.environ.get("WM_REPORTS_DIR", ""))
    p.add_argument("--logs-dir", default=os.environ.get("WM_LOGS_DIR", ""))
    args = p.parse_args(wc.argv_after_openroad_driver())

    if not args.cell_list:
        p.error("--cell-list / WM_CELL_LIST required")

    constraints = _read_constraints_csv(args.cell_list)
    n = len(constraints)
    if n == 0:
        print("[wm_verify_stages] no active constraints in CSV", file=sys.stderr)
        return 2

    stages: List[Tuple[str, str]] = []
    if args.stage:
        for tok in args.stage:
            if ":" not in tok:
                p.error(f"--stage must be LABEL:path, got {tok!r}")
            lab, path = tok.split(":", 1)
            stages.append((lab.strip(), path.strip()))
    else:
        env_st = os.environ.get("WM_VERIFY_STAGES", "").strip()
        if not env_st:
            p.error("No --stage and WM_VERIFY_STAGES empty")
        stages = _parse_stages_from_env(env_st)

    print(
        f"[wm_verify_stages] constraints={n} from {args.cell_list}\n"
        f"{'stage':<18} {'ok':>8} {'total':>8} {'fail':>8}"
    )
    any_fail = False
    ppa_rows: List[Dict[str, str]] = []
    summary_rows: List[Tuple[str, int, int]] = []
    for label, odb_path in stages:
        if args.ppa_csv:
            ppa_rows.append(
                _collect_stage_ppa(label, odb_path, args.reports_dir, args.logs_dir)
            )
        if not os.path.isfile(odb_path):
            print(f"{label:<18} ERROR missing {odb_path}", file=sys.stderr)
            any_fail = True
            continue
        try:
            ok, total, fails = _verify_stage(constraints, odb_path)
        except Exception as e:
            print(f"{label:<18} ERROR {e}", file=sys.stderr)
            any_fail = True
            continue
        fg = total - ok
        print(f"{label:<18} {ok:8d} {total:8d} {fg:8d}")
        summary_rows.append((label, ok, total))
        if fails and len(fails) <= 10:
            for x in fails:
                print(f"  {x}")
        elif fails:
            for x in fails[:10]:
                print(f"  {x}")
            print(f"  ... ({len(fails)} total)")
        if ok < total:
            any_fail = True

    if args.output_csv:
        out_path = os.path.abspath(args.output_csv)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["stage", "constraints_ok", "constraints_total"])
            for lab, ok, tot in summary_rows:
                w.writerow([lab, ok, tot])
        print(f"[wm_verify_stages] report -> {args.output_csv}")

    if args.ppa_csv:
        _write_ppa_csv(args.ppa_csv, ppa_rows)
        print(f"[wm_verify_stages] PPA -> {args.ppa_csv}")

    return 2 if any_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
