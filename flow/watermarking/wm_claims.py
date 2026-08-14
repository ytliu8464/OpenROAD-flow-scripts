# SPDX-License-Identifier: BSD-3-Clause
r"""Where the claim-verifiers get their claims from: the embed CSV, or a certificate.

This is the one module the placement and CTS verifiers import to obtain their
claim rows.  It exists so that :mod:`wm_cert` never has to learn stage-specific
schema trivia (that a placement row has a ``skipped_reason`` column whose value
``"already_satisfied"`` is special, that a CTS row's ``final_bit`` gates
acceptance), and so that all four verifier edits point at one seam.

Backward compatibility is the hard requirement
----------------------------------------------
``WM_CERT_FILE`` being set is the *only* switch.  With it unset this module is a
one-line wrapper around ``csv.DictReader`` and the verifiers behave exactly as
they did before -- byte for byte, which
``certificate/tests/test_claims.py`` asserts directly.  :mod:`wm_cert` is
imported lazily, inside the certificate branch, so nothing in the new crypto
code can affect the legacy path even if it were broken or absent.  This matters
because roughly a dozen analysis scripts (``experiments/lib/keyless_verify.py``,
``phase1_*.py``, ``attacks/targeted/dump_features.py``, ...) still read the
plaintext CSVs directly and must keep working.

Reconstructed rows carry the **full** CSV header key set, with unknown columns
as ``""``, so ``row.get(col, "")`` behaves identically whichever source was
used.  Fields recovered from the certificate are the exact CSV cell text, which
is why comparing the two sources is a pure equality test.

Environment
-----------
Every variable is ``WM_``-prefixed, and it has to be: ``place_wm.sh`` and
``cts_wm.sh`` forward variables into the OpenROAD child with
``compgen -v | grep -E '^WM_'``, so anything else is silently dropped.
``certificate/tests/test_claims.py`` pins that invariant.

===============================  ==============================================
``WM_CERT_FILE``                 path to ``wm_cert.bin``; presence = use it
``WM_CERT_MASTER_SEED_HEX``      K-hat: hex literal, ``*.hex`` file, or bundle
``WM_CERT_COMMIT_JSON``          ``wm_commit.json``; enables the Eq. 19 check
``WM_CERT_REQUIRE``              default ``1``: never fall back to the CSV
``WM_CERT_STAGE``                ``placement`` | ``cts``; optional cross-check
``WM_CERT_FORCE_PURE_AES``       test-only, consumed by :mod:`wm_cert`
===============================  ==============================================
"""

from __future__ import annotations

import csv
import os
import sys
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

__all__ = [
    "ALL_CERT_ENV_VARS",
    "ClaimSourceError", "CommitmentMismatch",
    "cert_requested", "loaded_source", "cert_meta",
    "load_placement_rows", "load_cts_rows",
    "placement_claims_from_rows", "cts_claims_from_rows",
    "read_placement_csv", "read_cts_csv",
    "PLACEMENT_CSV_HEADER", "CTS_CSV_HEADER",
]

#: Kept in sync with the writers; pinned by a test so a new variable cannot be
#: added without a ``WM_`` prefix (it would never reach the OpenROAD child).
ALL_CERT_ENV_VARS = (
    "WM_CERT_FILE",
    "WM_CERT_MASTER_SEED_HEX",
    "WM_CERT_COMMIT_JSON",
    "WM_CERT_REQUIRE",
    "WM_CERT_STAGE",
    "WM_CERT_FORCE_PURE_AES",
)

#: Mirrors ``placement_wm/watermark_embed.py`` (the ``header`` local in ``main``).
PLACEMENT_CSV_HEADER = (
    "kind", "id", "tile_tx", "tile_ty", "row_y_dbu",
    "A_name", "B_name", "C_name",
    "target_bit", "target_perm",
    "orig_order", "wm_order",
    "hpwl_delta_dbu", "disp_max_dbu",
    "satisfied", "skipped_reason",
)

#: Mirrors ``cts_wm/cts_watermark_embed.py:CSV_HEADER``.
CTS_CSV_HEADER = (
    "pair_idx", "pair_key", "channel", "L_A", "L_B",
    "target_lcb", "other_lcb", "target_bit", "final_bit",
    "fanout_target_before", "fanout_target_after",
    "fanout_other_before", "fanout_other_after",
    "seq_fanout_target_before", "seq_fanout_target_after",
    "seq_fanout_other_before", "seq_fanout_other_after",
    "repair_fanout_target", "repair_fanout_other",
    "cap_target", "cap_max_target",
    "num_boundary_ffs", "num_reassigned", "attempts", "skipped_reason",
)


class ClaimSourceError(Exception):
    """The certificate was requested but could not be turned into claim rows."""


class CommitmentMismatch(ClaimSourceError):
    """Eq. 19 failed: the claimed key does not open the registered commitment."""


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def cert_requested() -> bool:
    """True when ``WM_CERT_FILE`` names a certificate to read claims from."""
    return bool(_env("WM_CERT_FILE"))


def loaded_source() -> str:
    """``"certificate"`` or ``"csv"`` -- for the verifiers' log lines."""
    return "certificate" if cert_requested() else "csv"


def _require() -> bool:
    return _env("WM_CERT_REQUIRE") not in ("0", "no", "false")


# ---------------------------------------------------------------------------
# Certificate access (lazy, cached per process)
# ---------------------------------------------------------------------------

_CACHE: Dict[str, Any] = {}


def _open_cert():
    """Open the certificate named by the environment; cache it per path.

    A verifier process handles one stage, but ``verify_stages`` walks several
    checkpoint ODBs against the same claim set, so caching saves repeated
    decryptions -- which are ~90 ms each on the vendored AES backend.
    """
    path = _env("WM_CERT_FILE")
    if path in _CACHE:
        return _CACHE[path]

    import wm_cert  # noqa: E402  (deliberately lazy; see the module docstring)

    if not os.path.isfile(path):
        raise ClaimSourceError(f"WM_CERT_FILE does not exist: {path!r}")

    seed_src = _env("WM_CERT_MASTER_SEED_HEX")
    if not seed_src:
        raise ClaimSourceError(
            "WM_CERT_FILE is set but WM_CERT_MASTER_SEED_HEX is not; "
            "the certificate cannot be opened without the key")
    try:
        master_seed = wm_cert.load_master_seed(seed_src)
    except ValueError as e:
        raise ClaimSourceError(f"could not load the master seed: {e}") from e

    with open(path, "rb") as f:
        blob = f.read()

    commit_path = _env("WM_CERT_COMMIT_JSON")
    if commit_path:
        _check_commitment(wm_cert, master_seed, blob, commit_path)

    cert = wm_cert.open_certificate(master_seed, blob)
    _CACHE[path] = cert
    return cert


def _check_commitment(wm_cert, master_seed: bytes, blob: bytes,
                      commit_path: str) -> None:
    """Enforce Eq. 19 before opening, when a commitment record is supplied."""
    import json

    if not os.path.isfile(commit_path):
        raise ClaimSourceError(
            f"WM_CERT_COMMIT_JSON does not exist: {commit_path!r}")
    try:
        with open(commit_path) as f:
            rec = json.load(f)
    except ValueError as e:
        raise ClaimSourceError(f"{commit_path} is not valid JSON: {e}") from e

    header = wm_cert.parse_certificate_header(blob)
    actual = wm_cert.cert_sha256(blob)

    declared = rec.get("cert_sha256")
    if declared and declared != actual.hex():
        # Report this separately from a commitment mismatch: it means the
        # certificate file does not belong to this commitment record at all.
        raise ClaimSourceError(
            f"certificate hash {actual.hex()} does not match the "
            f"{declared} recorded in {os.path.basename(commit_path)}")

    expected = rec.get("commitment_sha256")
    if not expected:
        raise ClaimSourceError(
            f"{commit_path} has no 'commitment_sha256' field")
    try:
        expected_bytes = bytes.fromhex(expected)
    except ValueError as e:
        raise ClaimSourceError(f"commitment_sha256 is not hex: {e}") from e

    if not wm_cert.check_commitment(master_seed, header.id_d0, header.nu,
                                    actual, expected_bytes):
        raise CommitmentMismatch(
            "the claimed key does not open the registered commitment "
            "(paper Eq. 19); the ownership claim is inadmissible")


def cert_meta() -> Optional[Dict[str, Any]]:
    """The sealed metadata block, or ``None`` when running off the CSV.

    Carries the routing parameters (``f``, ``lambda_wm``) that Eq. 20's step 4
    needs in order to reconstruct ``WM_R`` from the key alone -- they are
    recorded nowhere else on disk.
    """
    if not cert_requested():
        return None
    return _open_cert().gamma.meta


def _check_stage(expected: str) -> None:
    want = _env("WM_CERT_STAGE")
    if want and want != expected:
        raise ClaimSourceError(
            f"WM_CERT_STAGE={want!r} but {expected} claims were requested; "
            "the driver is mis-wired")


# ---------------------------------------------------------------------------
# Row construction
# ---------------------------------------------------------------------------

def _blank(header) -> Dict[str, str]:
    return {col: "" for col in header}


def _read_csv_rows(path: str) -> List[Dict[str, str]]:
    with open(path, newline="") as f:
        return [dict(row) for row in csv.DictReader(f)]


def load_placement_rows(cell_list: str) -> List[Dict[str, str]]:
    """Placement claim rows in the embed-CSV schema.

    Certificate-sourced rows set ``skipped_reason=""`` and ``satisfied="True"``
    so that ``watermark_verify._should_verify_row`` is an exact no-op on them:
    every certified claim is, by construction, one the embedder accepted and
    kept.
    """
    if not cert_requested():
        return _read_csv_rows(cell_list)

    _check_stage("placement")
    cert = _open_cert()
    claims = cert.gamma.placement
    rows: List[Dict[str, str]] = []
    for claim in claims:
        row = _blank(PLACEMENT_CSV_HEADER)
        row.update({
            "kind": claim.kind,
            "A_name": claim.a_name,
            "B_name": claim.b_name,
            "C_name": claim.c_name,
            "target_bit": claim.target_bit,
            "target_perm": claim.target_perm,
            "satisfied": "True",
            "skipped_reason": "",
            # A stable stand-in for the CSV's own id column; nothing reads it,
            # but leaving it blank would make debug output useless.
            "id": "|".join(sorted(n for n in (claim.a_name, claim.b_name,
                                              claim.c_name) if n)),
        })
        rows.append(row)

    _assert_survivable(
        rows, len(claims), "placement",
        lambda r: r["skipped_reason"] in ("", "already_satisfied")
        and bool(r["A_name"]) and bool(r["B_name"])
        and (r["kind"] != "triple" or bool(r["C_name"])))
    return rows


def load_cts_rows(cell_list: str) -> List[Dict[str, str]]:
    """CTS claim rows in the embed-CSV schema.

    ``final_bit`` is emitted as ``""`` on purpose: ``read_pairs_csv`` drops rows
    whose ``final_bit`` disagrees with ``target_bit``, and an empty cell makes
    ``_parse_opt_int`` return ``None``, which skips that comparison.  The
    certificate only ever holds claims that already agreed.
    """
    if not cert_requested():
        return _read_csv_rows(cell_list)

    _check_stage("cts")
    cert = _open_cert()
    claims = cert.gamma.cts
    rows: List[Dict[str, str]] = []
    for claim in claims:
        row = _blank(CTS_CSV_HEADER)
        row.update({
            "pair_idx": claim.pair_idx,
            "pair_key": claim.pair_key,
            "channel": claim.channel,
            "L_A": claim.l_a,
            "L_B": claim.l_b,
            "target_lcb": claim.target_lcb,
            "other_lcb": claim.other_lcb,
            "target_bit": claim.target_bit,
            "final_bit": "",
            "repair_fanout_target": claim.repair_fanout_target,
            "repair_fanout_other": claim.repair_fanout_other,
            "skipped_reason": "",
        })
        rows.append(row)

    _assert_survivable(
        rows, len(claims), "cts",
        lambda r: not r["skipped_reason"] and bool(r["target_lcb"]))
    return rows


# ---------------------------------------------------------------------------
# The other direction: embed CSV -> claims, for certify.py
# ---------------------------------------------------------------------------

def read_placement_csv(path: str) -> List[Dict[str, str]]:
    """Raw rows of a placement embed CSV (no filtering)."""
    return _read_csv_rows(path)


def read_cts_csv(path: str) -> List[Dict[str, str]]:
    """Raw rows of a CTS embed CSV (no filtering)."""
    return _read_csv_rows(path)


def placement_claims_from_rows(rows):
    """Select the accepted placement rows and convert them to claims.

    The filter reproduces ``watermark_verify._should_verify_row`` (keep only
    ``skipped_reason`` in ``{"", "already_satisfied"}``) and additionally drops
    rows whose ``satisfied`` cell is false.  In practice the two agree -- the
    embedder writes a non-empty ``skipped_reason`` for everything it did not
    keep -- but certifying a claim the embedder recorded as unsatisfied would
    guarantee a verification failure later, so both are applied.

    Cell text is carried through verbatim; see :class:`wm_cert.PlacementClaim`.
    """
    from wm_cert import PlacementClaim

    out = []
    for row in rows:
        if (row.get("skipped_reason") or "").strip() not in ("", "already_satisfied"):
            continue
        if str(row.get("satisfied", "True")).strip().lower() in ("false", "0", "no"):
            continue
        kind = (row.get("kind") or "pair").strip()
        a = (row.get("A_name") or "").strip()
        b = (row.get("B_name") or "").strip()
        c = (row.get("C_name") or "").strip()
        if not a or not b:
            continue
        if kind == "triple" and not c:
            continue
        out.append(PlacementClaim(
            kind=kind, a_name=a, b_name=b, c_name=c,
            target_bit=(row.get("target_bit") or "").strip(),
            target_perm=(row.get("target_perm") or "").strip(),
        ))
    return out


def cts_claims_from_rows(rows):
    """Select the accepted CTS rows and convert them to claims.

    Reproduces ``cts_watermark_verify.read_pairs_csv``: a row needs a
    ``target_lcb``, an empty ``skipped_reason``, and -- when ``final_bit`` is
    present -- agreement between ``final_bit`` and ``target_bit``.  The embed
    CSV is an audit log containing failed trials and bookkeeping skips as well
    as accepted claims, so this filter is what separates them.
    """
    from wm_cert import CtsClaim

    out = []
    for row in rows:
        target_lcb = (row.get("target_lcb") or "").strip()
        if not target_lcb:
            continue
        if (row.get("skipped_reason") or "").strip():
            continue
        target_bit = (row.get("target_bit") or "").strip()
        final_bit = (row.get("final_bit") or "").strip()
        if final_bit:
            try:
                if int(final_bit) != int(target_bit):
                    continue
            except (TypeError, ValueError):
                continue
        channel = (row.get("channel") or "pure").strip().lower()
        if channel not in ("pure", "quasi_leaf", "pure_quasi"):
            # Match read_pairs_csv's normalization exactly, or the tamper check
            # would key off a different channel than the verifier sees.
            channel = "pure"
        out.append(CtsClaim(
            target_lcb=target_lcb,
            other_lcb=(row.get("other_lcb") or "").strip(),
            pair_key=(row.get("pair_key") or row.get("parent") or "").strip(),
            l_a=(row.get("L_A") or "").strip(),
            l_b=(row.get("L_B") or "").strip(),
            channel=channel,
            target_bit=target_bit,
            repair_fanout_target=(row.get("repair_fanout_target") or "").strip(),
            repair_fanout_other=(row.get("repair_fanout_other") or "").strip(),
            pair_idx=(row.get("pair_idx") or "").strip(),
        ))
    return out


def _assert_survivable(rows, expected: int, stage: str, predicate) -> None:
    """Guard against drift between Gamma construction and the verifier filters.

    The verifiers silently *skip* rows they consider inactive.  If a future
    change to certify.py produced a claim that those filters drop, the stage
    would quietly verify fewer claims than were certified -- which inflates the
    extraction rate instead of failing.  Catch it here, loudly.
    """
    survivors = sum(1 for r in rows if predicate(r))
    if survivors != expected:
        raise ClaimSourceError(
            f"{expected} {stage} claims were sealed in the certificate but "
            f"only {survivors} would be evaluated by the verifier; the "
            f"certificate and the verifier's row filter have diverged")
