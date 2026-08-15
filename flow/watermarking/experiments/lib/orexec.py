# SPDX-License-Identifier: BSD-3-Clause
"""How to invoke OpenROAD from the harness.

One place decides whether a command runs natively or inside a container, so no
call site hard-codes a site-specific path.

Environment:
    OPENROAD_EXE     OpenROAD binary built with the PDMarks routing commands.
                     Default: ``openroad`` from PATH.
    SINGULARITY_SIF  OPTIONAL container image.  When set (and we are not
                     already inside a container), commands are wrapped in
                     ``singularity exec``.  When unset, they run natively.
    WM_SINGULARITY_BINDS
                     Space-separated bind mounts for the container.
                     Default: the ORFS flow root.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import List, Sequence

OPENROAD_EXE: str = os.environ.get("OPENROAD_EXE", "openroad")
SIF: str = os.environ.get("SINGULARITY_SIF", "")


def _binds() -> List[str]:
    raw = os.environ.get("WM_SINGULARITY_BINDS", "")
    if raw:
        return raw.split()
    # Default to the ORFS flow root (this file is at
    # <flow>/watermarking/experiments/lib/orexec.py).
    return [str(Path(__file__).resolve().parents[3])]


def use_container() -> bool:
    """True when commands should be wrapped in ``singularity exec``."""
    return bool(SIF) and not os.environ.get("SINGULARITY_NAME")


def wrap(cmd: Sequence[str]) -> List[str]:
    """Return ``cmd``, wrapped in ``singularity exec`` if a container is set."""
    if not use_container():
        return list(cmd)
    singularity = shutil.which("singularity") or "singularity"
    prefix = [singularity, "exec"]
    for b in _binds():
        prefix += ["-B", b]
    prefix += ["-e", SIF]
    return prefix + list(cmd)


def openroad_python(script: Path, *args: str) -> List[str]:
    """Command line that runs ``script`` under OpenROAD's Python interpreter."""
    return wrap([OPENROAD_EXE, "-python", "-exit", str(script), *args])


def openroad_tcl(script: Path, *args: str) -> List[str]:
    """Command line that runs a Tcl ``script`` under OpenROAD."""
    return wrap([OPENROAD_EXE, "-exit", str(script), *args])


__all__ = [
    "OPENROAD_EXE", "SIF", "use_container", "wrap",
    "openroad_python", "openroad_tcl",
]
