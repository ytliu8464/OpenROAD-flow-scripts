# SPDX-License-Identifier: BSD-3-Clause
"""Fake ``openroad`` / ``odb`` modules so the verifiers can be imported off-line.

The four claim-verifiers normally run inside ``openroad -python``.  None of them
*calls* anything from those modules at import time -- ``watermark_verify.py``
only does ``from openroad import Design, Tech`` at module scope, and
``cts_watermark_common.py`` only ``import odb`` -- so stubbing the names is
enough to exercise every pure-Python code path (CSV parsing, row filtering,
claim comparison) on a host with no OpenROAD at all.

Anything that would need real database access raises immediately rather than
returning a plausible-looking value, so a test can never silently pass by
exercising the stub instead of the real thing.

Also provides the minimal fake block that ``watermark_verify.verify_from_csv``
needs: instances with a name and a bounding box.
"""

import sys
import types


class _Unavailable:
    """Placeholder that fails loudly if a test actually tries to use OpenROAD."""

    def __init__(self, *args, **kwargs):
        raise RuntimeError(
            "this test touched a real OpenROAD object; it is supposed to run "
            "entirely off-line")


def install():
    """Idempotently install the stub modules."""
    if "openroad" not in sys.modules:
        mod = types.ModuleType("openroad")
        mod.Design = _Unavailable
        mod.Tech = _Unavailable
        mod.Timing = _Unavailable
        sys.modules["openroad"] = mod

    if "odb" not in sys.modules:
        odb = types.ModuleType("odb")
        # Referenced only from inside functions; present so `import odb`
        # succeeds and attribute access fails visibly if ever reached.
        odb.dbSigType = _Unavailable
        odb.dbPlacementStatus = _Unavailable
        odb.dbDatabase = _Unavailable
        sys.modules["odb"] = odb


# ---------------------------------------------------------------------------
# Minimal layout doubles
# ---------------------------------------------------------------------------

class FakeBBox:
    __slots__ = ("_x", "_y")

    def __init__(self, x, y):
        self._x, self._y = x, y

    def xMin(self):
        return self._x

    def yMin(self):
        return self._y

    def xMax(self):
        return self._x + 100

    def yMax(self):
        return self._y + 100


class FakeInst:
    """Enough of ``odb::dbInst`` for ``inst_bottom_left`` and name lookup."""

    __slots__ = ("_name", "_bbox")

    def __init__(self, name, x, y=0):
        self._name = name
        self._bbox = FakeBBox(x, y)

    def getName(self):
        return self._name

    def getBBox(self):
        return self._bbox

    def __repr__(self):
        return f"FakeInst({self._name!r}, x={self._bbox.xMin()})"


class FakeBlock:
    __slots__ = ("_insts",)

    def __init__(self, insts):
        self._insts = list(insts)

    def getInsts(self):
        return self._insts
