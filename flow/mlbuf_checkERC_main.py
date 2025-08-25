"""
MLBuf ERC Checker & Buffer Insertion Helper
==========================================

This script integrates with OpenROAD to:
  • load a placed design (.odb) and SDC,
  • optionally insert buffers based on MLBuf/RSZ results,
  • run incremental placement if desired,
  • check ERC-style limits (slew, capacitance, max-fanout),
  • write resulting DEF/ODB snapshots,
  • and log all output to a file.

Usage
-----
openroad -python mlbuf_erc_check.py --design ibex --tech nangate45

Environment
-----------
You can point the script at your data tree via environment variables or CLI flags:
  • MLBUF_INPUT_ROOT   (default: current working directory)
  • OPENROAD_PLATFORMS (default: ./platforms)

License
-------
SPDX-License-Identifier: Apache-2.0
Copyright (c) 2025
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# OpenROAD Python modules
import pdn  # type: ignore
import odb  # type: ignore
import utl  # type: ignore
from openroad import Tech, Design, Timing  # type: ignore
import openroad as ord  # type: ignore

# External helper provided by the user/repo
from mlbuf_buffer_insertion import run_mlbuf_insertion  # type: ignore

# --------------------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------------------

def setup_logger(log_path: Path, verbose: bool = True) -> logging.Logger:
    """Configure a root logger that writes to file (and optionally stdout)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("mlbuf_erc_check")
    logger.setLevel(logging.DEBUG)

    # File handler
    fh = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(fh)

    if verbose:
        sh = logging.StreamHandler(sys.stdout)
        sh.setLevel(logging.INFO)
        sh.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(sh)

    return logger

def slew_check(val: int) -> int:
    return val - 1500 if val > 1500 else val

# --------------------------------------------------------------------------------------
# Platform paths
# --------------------------------------------------------------------------------------

@dataclass
class PlatformPaths:
    tech: str
    root: Path
    lib_dir: Path
    lef_dir: Path
    rc_file: Path


def platform_paths(tech: str, root: Optional[Path] = None) -> PlatformPaths:
    base_root = root if root is not None else Path("./platforms")
    base = base_root / tech
    return PlatformPaths(
        tech=tech,
        root=base_root,
        lib_dir=base / "lib",
        lef_dir=base / "lef",
        rc_file=base / "setRC.tcl",
    )


# --------------------------------------------------------------------------------------
# OpenROAD loading
# --------------------------------------------------------------------------------------

def load_design(tech: str, floorplan_odb: Path, sdc_file: Path, logger: logging.Logger, platform_root: Optional[Path] = None) -> Tuple[Tech, Design]:
    logger.info("==> Loading platform for tech: %s", tech)
    p = platform_paths(tech, platform_root)
    logger.debug("libDir: %s", p.lib_dir)
    logger.debug("lefDir: %s", p.lef_dir)
    logger.debug("rcFile: %s", p.rc_file)

    if not p.lib_dir.exists():
        raise FileNotFoundError(f"Library dir not found: {p.lib_dir}")
    if not p.lef_dir.exists():
        raise FileNotFoundError(f"LEF dir not found: {p.lef_dir}")
    if not p.rc_file.exists():
        raise FileNotFoundError(f"RC script not found: {p.rc_file}")

    tech_db = Tech()

    # Liberty
    for lib_file in sorted(p.lib_dir.glob("*.lib")):
        logger.info("Reading Liberty: %s", lib_file)
        tech_db.readLiberty(lib_file.as_posix())

    # LEF (tech first)
    for tech_lef in sorted(p.lef_dir.glob("*tech*.lef")):
        logger.info("Reading tech LEF: %s", tech_lef)
        tech_db.readLef(tech_lef.as_posix())
    for lef_file in sorted(p.lef_dir.glob("*.lef")):
        logger.info("Reading LEF: %s", lef_file)
        tech_db.readLef(lef_file.as_posix())

    design = Design(tech_db)

    if not floorplan_odb.exists():
        raise FileNotFoundError(f"ODB not found: {floorplan_odb}")
    logger.info("Reading ODB: %s", floorplan_odb)
    design.readDb(floorplan_odb.as_posix())

    if not sdc_file.exists():
        raise FileNotFoundError(f"SDC not found: {sdc_file}")
    logger.info("Reading SDC: %s", sdc_file)
    design.evalTclString(f"read_sdc {sdc_file.as_posix()}")

    logger.info("Sourcing RC script: %s", p.rc_file)
    design.evalTclString(f"source {p.rc_file.as_posix()}")

    return tech_db, design


# --------------------------------------------------------------------------------------
# Introspection helpers
# --------------------------------------------------------------------------------------

def get_registers(design: Design) -> List[str]:
    registers: List[str] = []
    regs_ptr = design.evalTclString("::sta::all_register").split()
    for reg in regs_ptr:
        reg_db_ptr = design.evalTclString("::sta::sta_to_db_inst " + reg)
        if reg_db_ptr != "NULL":
            reg_inst_name = design.evalTclString(reg_db_ptr + " getName")
            registers.append(reg_inst_name)
    return registers


def get_dbu() -> int:
    block = ord.get_db_block()
    return block.getDbUnitsPerMicron()


def get_driver_pin(net) -> Optional[odb.dbITerm]:  # type: ignore
    for iterm in net.getITerms():
        if iterm.getIoType() == "OUTPUT":
            return iterm
    return None


# --------------------------------------------------------------------------------------
# ERC checks
# --------------------------------------------------------------------------------------

def check_erc_violations(
    sta: Timing,
    design: Design,
    logger: logging.Logger,
    max_fanout: Optional[int] = None,
) -> Tuple[int, int, int]:
    """Count nets violating slew, capacitance, and (optional) max fanout.

    Returns: (slew_count, cap_count, fanout_count)
    """
    slew_viol = 0
    cap_viol = 0
    fan_viol = 0

    corner = sta.getCorners()[0]
    block = design.getBlock()

    for net in block.getNets():
        if net.getSigType() in ("POWER", "GROUND", "CLOCK"):
            continue

        drv = get_driver_pin(net)
        if drv is None:
            continue

        # Skip active-low resets/sets if desired
        name = drv.getName()
        if re.match(r".*/SETN$", name) or re.match(r".*/RESETN$", name):
            continue

        # Resolve Liberty cell pin (required for limits)
        lib_pin = None
        for mterm in drv.getInst().getMaster().getMTerms():
            if (drv.getInst().getName() + "/" + mterm.getName()) == name:
                lib_pin = mterm
                break
        if lib_pin is None:
            logger.debug("No Liberty pin for driver: %s", name)
            continue

        # Slew
        try:
            if sta.getMaxSlewLimit(lib_pin) < sta.getPinSlew(drv):
                slew_viol += 1
        except Exception as e:
            logger.debug("Slew check failed for %s: %s", name, e)

        # Capacitance (wireload/flute estimate pre-route)
        try:
            if sta.getMaxCapLimit(lib_pin) < sta.getNetCap(net, corner, sta.Max):
                cap_viol += 1
        except Exception as e:
            logger.debug("Cap check failed for %s: %s", name, e)

        # Max fanout
        if max_fanout is not None:
            try:
                sinks = len(net.getITerms())
                if sinks > max_fanout:
                    fan_viol += 1
                    logger.debug("Fanout violation on %s: %d > %d", name, sinks, max_fanout)
            except Exception as e:
                logger.debug("Fanout check failed for %s: %s", name, e)
    slew_viol = slew_check(slew_viol)
    logger.info("ERC summary — Slew: %d | Cap: %d | Fanout: %d", slew_viol, cap_viol, fan_viol)
    return slew_viol, cap_viol, fan_viol


# --------------------------------------------------------------------------------------
# Placement helpers
# --------------------------------------------------------------------------------------

def run_incremental_placement(design: Design, logger: logging.Logger) -> None:
    logger.info("Running incremental global placement (timing + routability)")
    design.evalTclString(
        "global_placement -routability_driven -timing_driven -skip_initial_place -incremental"
    )

    logger.info("Writing incremental GP snapshots: 3_3_place_gp.def/.odb")
    design.writeDef("3_3_place_gp.def")
    design.writeDb("3_3_place_gp.odb")

    # Light legalization (testing only)
    site = design.getBlock().getRows()[0].getSite()
    max_dx = int((design.getBlock().getBBox().xMax() - design.getBlock().getBBox().xMin()) / site.getWidth())
    max_dy = int((design.getBlock().getBBox().yMax() - design.getBlock().getBBox().yMin()) / site.getHeight())
    logger.info("Running detailed placement (test legalization)")
    design.getOpendp().detailedPlacement(max_dx, max_dy, "")

    logger.info("Writing detailed placement snapshots: 3_5_place_dp.def/.odb")
    design.writeDef("3_5_place_dp.def")
    design.writeDb("3_5_place_dp.odb")


# --------------------------------------------------------------------------------------
# Buffer insertion (net break + new instance wiring)
# --------------------------------------------------------------------------------------

def insert_buffer(
    sta: Timing,
    net_name: str,
    buf_cell_type: str,
    inserted_buffer_count: int,
    logger: logging.Logger,
    lx: int = -1,
    ly: int = -1,
) -> int:
    """Insert a buffer cell on the given net.

    Returns 1 if a buffer was inserted, 0 otherwise.
    """
    db = ord.get_db()
    block = ord.get_db_block()
    source_net = block.findNet(net_name)
    if source_net is None:
        logger.warning("Net not found: %s", net_name)
        return 0

    # Find driver
    driver_pin = None
    power_net = None
    gnd_net = None

    for pin in source_net.getITerms():
        if pin.isOutputSignal():
            driver_pin = pin
            break

    if driver_pin is not None:
        driver_inst = driver_pin.getInst()
        for pin in driver_inst.getITerms():
            if pin.getSigType() == "POWER":
                power_net = pin.getNet()
            elif pin.getSigType() == "GROUND":
                gnd_net = pin.getNet()
        if lx == -1 or ly == -1:
            bbox = driver_inst.getBBox()
            lx = int((bbox.xMin() + bbox.xMax()) / 2)
            ly = int((bbox.yMin() + bbox.yMax()) / 2)

    if driver_pin is None:
        # Try BTerms (I/O)
        for io_pin in source_net.getBTerms():
            if io_pin.getIoType() == "INPUT":
                driver_pin = io_pin
                break

    if power_net is None and gnd_net is None and source_net.getITerms():
        sink_inst = source_net.getITerms()[0].getInst()
        for pin in sink_inst.getITerms():
            if pin.getSigType() == "POWER":
                power_net = pin.getNet()
            elif pin.getSigType() == "GROUND":
                gnd_net = pin.getNet()
        if lx == -1 or ly == -1:
            bbox = sink_inst.getBBox()
            lx = int((bbox.xMin() + bbox.xMax()) / 2)
            ly = int((bbox.yMin() + bbox.yMax()) / 2)

    if driver_pin is None:
        logger.warning("No driver pin for net: %s", net_name)
        return 0
    if power_net is None and gnd_net is None:
        logger.warning("No power/ground nets for net: %s", net_name)
        return 0

    master = db.findMaster(buf_cell_type)
    if master is None:
        logger.warning("Buffer master not found: %s", buf_cell_type)
        return 0

    inst_name = f"inserted_buffer_{inserted_buffer_count}"
    new_inst = odb.dbInst_create(block, master, inst_name)
    new_inst.setOrient("R0")
    new_inst.setPlacementStatus("PLACED")
    new_inst.setLocation(lx, ly)
    logger.info("Insert %s @ (%d, %d) on net %s", buf_cell_type, lx, ly, net_name)

    new_net = odb.dbNet_create(block, f"inserted_buffer_net_{inserted_buffer_count}")
    driver_pin.disconnect()
    driver_pin.connect(new_net)

    for pin in new_inst.getITerms():
        if pin.isInputSignal():
            pin.connect(new_net)
        elif pin.isOutputSignal():
            pin.connect(source_net)
        elif pin.getSigType() == "POWER":
            pin.connect(power_net)
        elif pin.getSigType() == "GROUND":
            pin.connect(gnd_net)

    return 1


# --------------------------------------------------------------------------------------
# Batch runner
# --------------------------------------------------------------------------------------

@dataclass
class Args:
    design: str
    tech: str
    clusters: int
    cap_margin: int
    tcp: int
    large_net_threshold: int
    start_idx: int
    end_idx: int
    mlbuf: bool
    insert_buffers: bool
    out_dir: Path
    log: Path
    input_root: Path
    platform_root: Path


DEFAULT_RANGE = (1, 13)  # inclusive


def parse_args(argv: Optional[List[str]] = None) -> Args:
    p = argparse.ArgumentParser(description="MLBuf ERC checker and buffer applier")

    p.add_argument("--design", "-d", default="ibex", help="Design name")
    p.add_argument("--tech", "-t", default="nangate45", help="Technology name (platform folder)")
    p.add_argument("--clusters", "-cn", type=int, default=20, help="MLBuf clusters count")
    p.add_argument("--cap-margin", type=int, default=0, help="Cap margin used in path patterns")
    p.add_argument("--tcp", type=int, default=-1, help="TCP sweep value; -1 means default TCP")
    p.add_argument("--large-net-threshold", type=int, default=1000, help="Ignore nets >= threshold (for writers)")
    p.add_argument("--start", type=int, default=DEFAULT_RANGE[0], help="Start index (inclusive), zero-padded to 2 digits")
    p.add_argument("--end", type=int, default=DEFAULT_RANGE[1], help="End index (inclusive)")
    p.add_argument("--no-mlbuf", dest="mlbuf", action="store_false", help="Use RSZ results instead of MLBuf")
    p.add_argument("--no-insert", dest="insert_buffers", action="store_false", help="Do not insert buffers; write snapshots only")
    p.add_argument("--out-dir", default="erc_mlbuf/outputs", type=Path, help="Output base directory")
    p.add_argument("--log", default="erc_mlbuf/run.log", type=Path, help="Log file path")
    p.add_argument("--input-root", type=Path, default=Path(os.getenv("MLBUF_INPUT_ROOT", ".")), help="Root of MLBuf data tree (env MLBUF_INPUT_ROOT)")
    p.add_argument("--platform-root", type=Path, default=Path(os.getenv("OPENROAD_PLATFORMS", "./platforms")), help="OpenROAD platforms root (env OPENROAD_PLATFORMS)")

    ns = p.parse_args(argv)
    return Args(
        design=ns.design,
        tech=ns.tech,
        clusters=ns.clusters,
        cap_margin=ns.cap_margin,
        tcp=ns.tcp,
        large_net_threshold=ns.large_net_threshold,
        start_idx=ns.start,
        end_idx=ns.end,
        mlbuf=ns.mlbuf,
        insert_buffers=ns.insert_buffers,
        out_dir=ns.out_dir,
        log=ns.log,
        input_root=ns.input_root,
        platform_root=ns.platform_root,
    )


# --------------------------------------------------------------------------------------
# Paths inferred from args (mirrors original script structure)
# --------------------------------------------------------------------------------------

def _first_existing(candidates: List[Path]) -> Path:
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def infer_input_paths(
    design: str,
    tcp: int,
    num_str: str,
    clusters: int,
    cap_margin: int,
    input_root: Path,
) -> Tuple[Path, Path, Path, Path]:
    """Return (sdc, gp_odb, mlbuf_csv, rsz_post_csv)."""
    flows_root = input_root / "flows" / "OR_inputs" / design
    scripts_root = input_root / "MLBuf_eval" / "OR_integration_for_erc" / "scripts"

    if tcp == -1:
        sdc = flows_root / "2_floorplan.sdc"
        design_root = scripts_root / design / "erc_check_inputs"
    else:
        sdc = flows_root / f"1_synth_{tcp}.sdc"
        design_root = scripts_root / f"{design}_TCP_sweep" / "erc_check_inputs" / f"TCP{tcp}"

    gp_odb = design_root / "odb" / f"gp_{num_str}.odb"

    # Accept either MLBuf_cn* or MLBuf_cap_cn* directory conventions
    mlbuf_candidates = [
        design_root / "mlbuf_results" / f"MLBuf_cn{clusters}_margin{cap_margin}" / f"prob_net_BufferTree_{num_str}.csv",
        design_root / "mlbuf_results" / f"MLBuf_cap_cn{clusters}_margin{cap_margin}" / f"prob_net_BufferTree_{num_str}.csv",
    ]
    mlbuf_csv = _first_existing(mlbuf_candidates)

    rsz_post_csv = design_root / "vrsz_results" / f"buffered_tree_post_{num_str}.csv"
    return sdc, gp_odb, mlbuf_csv, rsz_post_csv


# --------------------------------------------------------------------------------------
# Main execution
# --------------------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logger = setup_logger(args.log, verbose=True)
    logger.info(
        "MLBuf ERC — design=%s tech=%s tcp=%s clusters=%d | input_root=%s | platforms=%s",
        args.design,
        args.tech,
        args.tcp,
        args.clusters,
        args.input_root,
        args.platform_root,
    )

    inserted_counts: List[int] = []
    slew_viol_list: List[int] = []
    cap_viol_list: List[int] = []
    buffer_area_list: List[float] = []
    buffer_type_dist_list: List[Dict[str, int]] = []

    # Process batch [start..end]
    for n in range(args.start_idx, args.end_idx + 1):
        num_str = f"{n:02d}"
        logger.info("\n===== Case %s =====", num_str)

        sdc_file, gp_place_odb_file, mlbuf_infer_csv, rsz_post_csv = infer_input_paths(
            args.design, args.tcp, num_str, args.clusters, args.cap_margin, args.input_root
        )

        try:
            tech_db, design = load_design(args.tech, gp_place_odb_file, sdc_file, logger, platform_root=args.platform_root)
            sta = Timing(design)
        except Exception as e:
            logger.exception("Failed to load design for %s: %s", num_str, e)
            continue

        inserted_buffer_count = 0
        mlbuf_flag = args.mlbuf
        insert_flag = args.insert_buffers

        try:
            if insert_flag:
                post_tree_csv = rsz_post_csv  # use RSZ post-tree for both modes if provided
                inserted_buffer_count, buf_area, buf_dist = run_mlbuf_insertion(
                    design, sta, mlbuf_infer_csv, post_tree_csv, mlbuf_flag, start_cnt=inserted_buffer_count
                )

                # Output path mirrors original layout
                if mlbuf_flag:
                    save_path = args.out_dir / (
                        f"MLBuf_cn{args.clusters}_margin{args.cap_margin}" + (f"_tcp{args.tcp}" if args.tcp != -1 else "")
                    )
                    label = "after_mlbuf"
                else:
                    save_path = args.out_dir / (
                        f"rsz_margin{args.cap_margin}" + (f"_tcp{args.tcp}" if args.tcp != -1 else "")
                    )
                    label = "after_rsz"
                save_path.mkdir(parents=True, exist_ok=True)

                out_base = save_path / f"{args.design}_{label}_{num_str}"
                design.writeDef(out_base.as_posix() + ".def")
                design.writeDb(out_base.as_posix() + ".odb")

                inserted_counts.append(inserted_buffer_count)
                buffer_area_list.append(buf_area)
                buffer_type_dist_list.append(buf_dist)

                logger.info(
                    "Inserted buffers: %d | Buffer area: %.3f | Types: %s",
                    inserted_buffer_count,
                    buf_area,
                    buf_dist,
                )
            else:
                # Snapshot without buffer insertion
                save_path = args.out_dir / (f"noBuf_margin{args.cap_margin}" + (f"_tcp{args.tcp}" if args.tcp != -1 else ""))
                save_path.mkdir(parents=True, exist_ok=True)
                out_base = save_path / f"{args.design}_no_buf_{num_str}"
                design.writeDef(out_base.as_posix() + ".def")
                design.writeDb(out_base.as_posix() + ".odb")
                logger.info("Wrote snapshots without buffer insertion: %s", out_base)

            # Run ERC checks
            s_cnt, c_cnt, f_cnt = check_erc_violations(sta, design, logger)
            slew_viol_list.append(s_cnt)
            cap_viol_list.append(c_cnt)
        except Exception as e:
            logger.exception("Processing failed for %s: %s", num_str, e)
            continue

    # Final summary
    logger.info("\n===== Run summary =====")
    logger.info("Inserted buffer counts: %s", inserted_counts)
    logger.info("Slew violations per case: %s", slew_viol_list)
    logger.info("Cap violations per case: %s", cap_viol_list)
    logger.info("Buffer area per case: %s", buffer_area_list)
    logger.info("Buffer type distributions: %s", buffer_type_dist_list)

    return 0


if __name__ == "__main__":
    sys.exit(main())
