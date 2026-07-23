# SPDX-License-Identifier: BSD-3-Clause
"""Helpers for loading ORFS reports.

We deliberately read post-finish PPA metrics from ``6_report.json`` (not
``6_finish.rpt``).  Per-stage runtime/cell-count metrics are read from
``logs/<platform>/<design>/<variant>/{1_synth,3_5_place_dp,4_1_cts,5_2_route,
6_report}.json``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional


# ---------------------------------------------------------------------------
# Root path resolution
# ---------------------------------------------------------------------------
# Override the reference-flow root at runtime:
#   export ORFS_FLOW_HOME=/my/other/orfs/flow
# This propagates automatically to every helper below.

_DEFAULT_FLOW_HOME = Path(
    "/home/fetzfs_projects/MISC-ytliu/watermarking/OR0415/OpenROAD-flow-scripts/flow"
)

FLOW_HOME: Path = Path(os.environ.get("ORFS_FLOW_HOME", str(_DEFAULT_FLOW_HOME)))

# Root of all per-module watermarking results
WM_HOME: Path = FLOW_HOME / "watermarking"
EXPERIMENTS_HOME: Path = WM_HOME / "experiments"


def flow_results(platform: str, design: str, variant: str) -> Path:
    return FLOW_HOME / "results" / platform / design / variant


def flow_logs(platform: str, design: str, variant: str) -> Path:
    return FLOW_HOME / "logs" / platform / design / variant


def flow_reports(platform: str, design: str, variant: str) -> Path:
    return FLOW_HOME / "reports" / platform / design / variant


def experiment_results(platform: str, design: str, variant: str) -> Path:
    return EXPERIMENTS_HOME / "results" / platform / design / variant


def experiment_logs(platform: str, design: str, variant: str) -> Path:
    return EXPERIMENTS_HOME / "logs" / platform / design / variant


def experiment_reports(platform: str, design: str, variant: str) -> Path:
    return EXPERIMENTS_HOME / "reports" / platform / design / variant


# ---------------------------------------------------------------------------
# Watermarking-module-specific path helpers
# ---------------------------------------------------------------------------

def wm_module_logs(module: str, platform: str, design: str, variant: str) -> Path:
    """Return the logs directory for a specific watermarking module PPA run.

    Layout: flow/watermarking/<module>/logs/<platform>/<design>/<variant>/
    This is where make writes *.json stage logs when called from the module dir.
    """
    return WM_HOME / module / "logs" / platform / design / variant


def wm_module_results(module: str, platform: str, design: str, variant: str) -> Path:
    """Return the results directory for a watermarking module PPA run."""
    return WM_HOME / module / "results" / platform / design / variant


def find_latest_wm_variant(module: str, platform: str, design: str,
                            require_6report: bool = True) -> Optional[str]:
    """Return the variant name to use for *module* on (platform, design).

    Priority:
    1. ``WM_VARIANT_<MODULE_UPPER>`` environment variable, e.g.::

           export WM_VARIANT_PLACE_ORDERING=pdmarks-p-only-20240512_090000
           export WM_VARIANT_ROUTING_WRONG_WAY=pdmarks-r-only-20240512_090000
           export WM_VARIANT_CTS_V2=pdmarks-c-only-20240512_090000

       The env-var name is derived from the module name by uppercasing and
       replacing hyphens/slashes with underscores.

    2. Auto-discover: scan ``flow/watermarking/<module>/logs/<platform>/<design>/``
       and return the sub-directory whose ``6_report.json`` has the highest mtime
       (or newest directory when ``require_6report=False``).

    Returns None when no variants exist.
    """
    # Check for an env-var pin first
    env_key = "WM_VARIANT_" + module.upper().replace("-", "_").replace("/", "_")
    pinned = os.environ.get(env_key)
    if pinned:
        return pinned

    base = WM_HOME / module / "logs" / platform / design
    if not base.is_dir():
        return None
    candidates = []
    for d in base.iterdir():
        if not d.is_dir():
            continue
        marker = d / "6_report.json"
        if require_6report:
            if not marker.exists():
                continue
            candidates.append((marker.stat().st_mtime, d.name))
        else:
            candidates.append((d.stat().st_mtime, d.name))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def list_wm_variants(module: str, platform: str, design: str) -> list:
    """Return all variant names that have a completed 6_report.json, newest first."""
    base = WM_HOME / module / "logs" / platform / design
    if not base.is_dir():
        return []
    result = []
    for d in base.iterdir():
        if not d.is_dir():
            continue
        if (d / "6_report.json").exists():
            result.append((d.stat().st_mtime, d.name))
    result.sort(reverse=True)
    return [name for _, name in result]


def find_latest_experiment_variant(platform: str, design: str,
                                   prefix: Optional[str] = None,
                                   require_6report: bool = True) -> Optional[str]:
    """Return the newest completed variant under watermarking/experiments.

    The experiment drivers are normally launched from ``watermarking/experiments``
    and therefore write their PPA logs/results there, not under each watermarking
    module directory.
    """
    base = EXPERIMENTS_HOME / "logs" / platform / design
    if not base.is_dir():
        return None
    candidates = []
    for d in base.iterdir():
        if not d.is_dir():
            continue
        if prefix and not d.name.startswith(prefix):
            continue
        marker = d / "6_report.json"
        if require_6report:
            if not marker.exists():
                continue
            candidates.append((marker.stat().st_mtime, d.name))
        else:
            candidates.append((d.stat().st_mtime, d.name))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


@dataclass
class RefMetrics:
    """Reference PPA + design-summary metrics parsed from 6_report.json."""

    platform: str
    design: str
    variant: str
    n_std_cells: Optional[int] = None
    n_macros: Optional[int] = None
    n_nets: Optional[int] = None
    tcp_ns: Optional[float] = None
    wns_ns: Optional[float] = None         # finish__timing__setup__ws
    tns_ns: Optional[float] = None         # finish__timing__setup__tns
    power_w: Optional[float] = None        # finish__power__total
    rwl_um: Optional[float] = None         # detailedroute__route__wirelength
    runtime_s: Optional[float] = None      # sum of per-stage runtimes
    raw: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d.pop("raw", None)
        return d


def _load_json(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _normalize_timing_to_ns(rm: "RefMetrics") -> None:
    """Convert ASAP7's picosecond timing fields to nanoseconds *in place*.

    ORFS reports ``finish__timing__setup__ws`` / ``__tns`` and the
    ``clock_period.txt`` value in whatever unit the platform's SDC uses.
    NanGate45 uses nanoseconds; ASAP7 uses picoseconds.  Without this fix
    the same column name ``wns_ns`` would mean ns on NG45 and ps on ASAP7,
    which produced misleading ASAP7 ΔWNS/ΔTNS values in tab:ppa_asap7
    (e.g. ``34.738`` for SweRV/AutoMarks read as 34.7 ns of slack drop
    on a 1.46 ns clock — actually 0.035 ns once converted).

    Call this from every metric-loader before returning the ``RefMetrics``
    so every downstream consumer sees consistent nanosecond units.
    """
    if rm.platform != "asap7":
        return
    if rm.wns_ns is not None:
        rm.wns_ns *= 1e-3
    if rm.tns_ns is not None:
        rm.tns_ns *= 1e-3
    if rm.tcp_ns is not None:
        rm.tcp_ns *= 1e-3


def _read_clock_period(results_dir: Path) -> Optional[float]:
    cp = results_dir / "clock_period.txt"
    if cp.exists():
        try:
            return float(cp.read_text().strip().split()[0])
        except Exception:
            pass
    # Fall back to first create_clock period in any *.sdc.
    for cand in ("3_place.sdc", "4_cts.sdc", "1_synth.sdc"):
        sdc = results_dir / cand
        if not sdc.exists():
            continue
        m = re.search(r"create_clock[^\n]*-period\s+([0-9.eE+-]+)", sdc.read_text())
        if m:
            return float(m.group(1))
    return None


_ELAPSED_RX = re.compile(
    r"Elapsed time:\s*"
    r"(?:(?P<h>\d+):)?(?P<m>\d+):(?P<s>[0-9.]+)"
)


def _stage_runtime_seconds(stage_log: Path) -> Optional[float]:
    """Best-effort per-stage runtime in seconds, parsed from the stage *.log*.

    OpenROAD prints ``Elapsed time: H:MM:SS.ss[h:]min:sec.`` at the end of every
    stage; Yosys uses a similar pattern.  We pick the last occurrence so that
    multi-stage logs report the actual last-stage wall time.
    """
    if not stage_log.exists():
        return None
    try:
        text = stage_log.read_text(errors="replace")
    except Exception:
        return None
    last = None
    for m in _ELAPSED_RX.finditer(text):
        last = m
    if last is None:
        return None
    h = int(last.group("h") or 0)
    mins = int(last.group("m"))
    secs = float(last.group("s"))
    return h * 3600 + mins * 60 + secs


def _route_wirelength(rdir: Path) -> Optional[float]:
    """Detailed-route wirelength in microns (best-effort)."""
    j = _load_json(rdir.parent / rdir.name)  # placeholder; overwritten below
    rep = FLOW_HOME / "logs"  # not used
    # Try logs/*/5_2_route.json keys
    return None


def load_wm_metrics(module: str, platform: str, design: str,
                    variant: Optional[str] = None) -> RefMetrics:
    """Load PPA metrics for a watermarked flow run stored under a watermarking module dir.

    If *variant* is None, the most recently completed variant is used automatically.
    The module layout is::

        flow/watermarking/<module>/logs/<platform>/<design>/<variant>/6_report.json
        flow/watermarking/<module>/results/<platform>/<design>/<variant>/

    The reference-flow ``results`` dir (for clock_period.txt etc.) is NOT under
    the module dir; we fall back to the standard ``flow/results/<plat>/<design>/``
    tree.
    """
    if variant is None:
        variant = find_latest_wm_variant(module, platform, design)
    if variant is None:
        raise FileNotFoundError(
            f"No completed watermark runs found for module={module} "
            f"{platform}/{design}"
        )
    logs = wm_module_logs(module, platform, design, variant)
    results_for_sdc = flow_results(platform, design,
                                   variant)  # may not exist; fallback below

    rm = RefMetrics(platform=platform, design=design, variant=variant)
    finish = _load_json(logs / "6_report.json")
    rm.raw = finish

    if finish:
        if "finish__design__instance__count__stdcell" in finish:
            rm.n_std_cells = int(finish["finish__design__instance__count__stdcell"])
        if "finish__design__instance__count__macros" in finish:
            rm.n_macros = int(finish["finish__design__instance__count__macros"])
        if "finish__design__nets" in finish:
            rm.n_nets = int(finish["finish__design__nets"])
        if "finish__timing__setup__ws" in finish:
            rm.wns_ns = float(finish["finish__timing__setup__ws"])
        if "finish__timing__setup__tns" in finish:
            rm.tns_ns = float(finish["finish__timing__setup__tns"])
        if "finish__power__total" in finish:
            rm.power_w = float(finish["finish__power__total"])

    # Routed wirelength
    for cand in ("5_2_route.json", "5_route.json", "5_3_fillcell.json"):
        j = _load_json(logs / cand)
        for k in (
            "detailedroute__route__wirelength",
            "globalroute__route__wirelength",
            "route__wirelength",
        ):
            if k in j:
                try:
                    rm.rwl_um = float(j[k])
                except Exception:
                    pass
                break
        if rm.rwl_um is not None:
            break

    # Clock period: try module results dir first, fall back to flow results
    wm_rdir = wm_module_results(module, platform, design, variant)
    rm.tcp_ns = _read_clock_period(wm_rdir) or _read_clock_period(results_for_sdc)

    # Runtime from module logs
    total = 0.0
    have_any = False
    for stage in (
        "4_1_cts.log",
        "5_1_grt.log",
        "5_2_route.log",
        "5_3_fillcell.log",
        "6_1_fill.log",
        "6_report.log",
    ):
        t = _stage_runtime_seconds(logs / stage)
        if t is not None:
            total += t
            have_any = True
    if have_any:
        rm.runtime_s = total

    _normalize_timing_to_ns(rm)
    return rm


def load_experiment_metrics(platform: str, design: str,
                            variant: str) -> RefMetrics:
    """Load PPA metrics for a PDMarks run stored by the experiments harness."""
    logs = experiment_logs(platform, design, variant)
    results = experiment_results(platform, design, variant)

    rm = RefMetrics(platform=platform, design=design, variant=variant)
    finish = _load_json(logs / "6_report.json")
    rm.raw = finish

    if finish:
        if "finish__design__instance__count__stdcell" in finish:
            rm.n_std_cells = int(finish["finish__design__instance__count__stdcell"])
        if "finish__design__instance__count__macros" in finish:
            rm.n_macros = int(finish["finish__design__instance__count__macros"])
        if "finish__design__nets" in finish:
            rm.n_nets = int(finish["finish__design__nets"])
        if "finish__timing__setup__ws" in finish:
            rm.wns_ns = float(finish["finish__timing__setup__ws"])
        if "finish__timing__setup__tns" in finish:
            rm.tns_ns = float(finish["finish__timing__setup__tns"])
        if "finish__power__total" in finish:
            rm.power_w = float(finish["finish__power__total"])

    for cand in ("5_2_route.json", "5_route.json", "5_3_fillcell.json"):
        j = _load_json(logs / cand)
        for k in (
            "detailedroute__route__wirelength",
            "globalroute__route__wirelength",
            "route__wirelength",
        ):
            if k in j:
                try:
                    rm.rwl_um = float(j[k])
                except Exception:
                    pass
                break
        if rm.rwl_um is not None:
            break

    rm.tcp_ns = _read_clock_period(results) or _read_clock_period(
        flow_results(platform, design, variant)
    )

    total = 0.0
    have_any = False
    for stage in (
        "1_2_yosys.log",
        "2_1_floorplan.log",
        "2_2_floorplan_macro.log",
        "2_3_floorplan_tapcell.log",
        "2_4_floorplan_pdn.log",
        "3_1_place_gp_skip_io.log",
        "3_2_place_iop.log",
        "3_3_place_gp.log",
        "3_4_place_resized.log",
        "3_5_place_dp.log",
        "4_1_cts.log",
        "5_1_grt.log",
        "5_2_route.log",
        "5_3_fillcell.log",
        "6_1_fill.log",
        "6_report.log",
    ):
        t = _stage_runtime_seconds(logs / stage)
        if t is not None:
            total += t
            have_any = True
    if have_any:
        rm.runtime_s = total

    _normalize_timing_to_ns(rm)
    return rm


def load_reference(platform: str, design: str, variant: str) -> RefMetrics:
    """Parse the on-disk reference run into a RefMetrics record.

    Returns a RefMetrics with all known fields filled; unknown fields stay None.
    """
    results = flow_results(platform, design, variant)
    logs = flow_logs(platform, design, variant)

    rm = RefMetrics(platform=platform, design=design, variant=variant)
    finish = _load_json(logs / "6_report.json")
    rm.raw = finish

    if finish:
        # Counts
        if "finish__design__instance__count__stdcell" in finish:
            rm.n_std_cells = int(finish["finish__design__instance__count__stdcell"])
        if "finish__design__instance__count__macros" in finish:
            rm.n_macros = int(finish["finish__design__instance__count__macros"])
        if "finish__design__nets" in finish:
            rm.n_nets = int(finish["finish__design__nets"])
        # Timing (ns)
        if "finish__timing__setup__ws" in finish:
            rm.wns_ns = float(finish["finish__timing__setup__ws"])
        if "finish__timing__setup__tns" in finish:
            rm.tns_ns = float(finish["finish__timing__setup__tns"])
        # Power (W)
        if "finish__power__total" in finish:
            rm.power_w = float(finish["finish__power__total"])

    # Routed wirelength: try 5_2_route.json or 5_route.json
    for cand in ("5_2_route.json", "5_route.json", "5_3_fillcell.json"):
        j = _load_json(logs / cand)
        for k in (
            "detailedroute__route__wirelength",
            "globalroute__route__wirelength",
            "route__wirelength",
        ):
            if k in j:
                try:
                    rm.rwl_um = float(j[k])
                except Exception:
                    pass
                break
        if rm.rwl_um is not None:
            break

    # TCP
    rm.tcp_ns = _read_clock_period(results)

    # Runtime: sum of per-stage runtimes parsed from *.log files
    total = 0.0
    have_any = False
    for stage in (
        "1_2_yosys.log",
        "2_1_floorplan.log",
        "2_2_floorplan_macro.log",
        "2_3_floorplan_tapcell.log",
        "2_4_floorplan_pdn.log",
        "3_1_place_gp_skip_io.log",
        "3_2_place_iop.log",
        "3_3_place_gp.log",
        "3_4_place_resized.log",
        "3_5_place_dp.log",
        "4_1_cts.log",
        "5_1_grt.log",
        "5_2_route.log",
        "5_3_fillcell.log",
        "6_1_fill.log",
        "6_report.log",
    ):
        t = _stage_runtime_seconds(logs / stage)
        if t is not None:
            total += t
            have_any = True
    if have_any:
        rm.runtime_s = total

    _normalize_timing_to_ns(rm)
    return rm


__all__ = [
    "FLOW_HOME",
    "WM_HOME",
    "EXPERIMENTS_HOME",
    "flow_results",
    "flow_logs",
    "flow_reports",
    "experiment_results",
    "experiment_logs",
    "experiment_reports",
    "wm_module_logs",
    "wm_module_results",
    "find_latest_wm_variant",
    "find_latest_experiment_variant",
    "list_wm_variants",
    "load_wm_metrics",
    "load_experiment_metrics",
    "RefMetrics",
    "load_reference",
]
