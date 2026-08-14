# SPDX-License-Identifier: BSD-3-Clause
r"""The PDMarks watermark certificate: paper Section IV.D, Eqs. 17-20.

What this implements
--------------------
The certificate ``Gamma = (Gamma_P, Gamma_C)`` records the accepted placement
and CTS claims.  It is serialized with an unambiguous length-prefixed encoding,
sealed under AES-256-GCM, and bound to a design and a watermark instance by a
SHA-256 key commitment that the owner timestamps before releasing the layout::

    K_Gamma = HMAC-SHA256(K, LP(ID(D0)) || LP(nu) || LP("cert"))        (Eq. 17)
    C_Gamma = AES-256-GCM-Enc_{K_Gamma}(Gamma; aad = LP(ID(D0))||LP(nu),
                                               nonce = nu)             (Eq. 18)
    c       = SHA256(K || ID(D0) || nu || SHA256(C_Gamma))              (Eq. 19)
    R       = (ID(D0), nu, SHA256(C_Gamma), c)   -> RFC 3161 timestamp

``LP(x)`` is ``x`` prefixed with its big-endian uint32 byte length, the same
convention :func:`wm_prf.hmac_digest` already uses.  Eq. 19's four fields are
all fixed-length (32/32/12/32 bytes), so its plain concatenation is already
unambiguous and is written exactly as the paper states.

What "K" is here
----------------
``K`` is the 32-byte **master seed** that ``gen_key/`` derives, i.e.
``SHA-256(Ed25519_sig(sk, M))`` -- the same value that Eq. 2 feeds into the
per-stage KDF ``seed_s = SHA-256(master_seed || label_s)``.  The certificate key
and the stage keys are therefore siblings under one secret, separated by domain.
``nu`` and ``ID(D0)`` are fixed at *certification* time, after embedding, and are
deliberately **not** part of the signed binding message ``M``: adding a field to
``M`` would change the signature and hence every derived stage seed, invalidating
every watermark already embedded.  Their integrity comes from the AEAD's
associated data, from ``c``, and from the timestamp.

Dependencies
------------
Standard library only, plus :mod:`wm_aesgcm` from this directory.  That matters:
the placement and CTS verifiers open certificates from inside ``openroad
-python``, where ``cryptography`` is not importable.  When ``cryptography`` *is*
available (host tooling) it is preferred for the AEAD; both backends are pinned
to the same test vectors.  Set ``WM_CERT_FORCE_PURE_AES=1`` to force the
vendored path.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import struct
import subprocess
import sys
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import wm_aesgcm  # noqa: E402
from wm_prf import hmac_digest  # noqa: E402

__all__ = [
    # errors
    "CertificateError", "CertificateFormatError", "CertificateAuthError",
    # encoding
    "lp", "lps", "canonical_json",
    # claims + gamma
    "PlacementClaim", "CtsClaim", "Gamma",
    "serialize_gamma", "parse_gamma",
    # identity
    "ID_D0_SCHEMA", "ID_D0_ARTIFACTS_FULL", "ID_D0_ARTIFACTS_LIGHT",
    "build_id_d0_preimage", "id_d0", "sha256_file",
    # keys / commitment
    "CERT_LABEL", "derive_cert_key", "commitment", "check_commitment",
    "record_bytes", "record_sha256", "RECORD_ENCODING", "load_master_seed",
    # certificate file
    "CERT_MAGIC", "CERT_VERSION", "CERT_SUITE", "CERT_HEADER_LEN",
    "Certificate", "CertificateHeader",
    "seal_certificate", "parse_certificate_header", "open_certificate",
    "cert_sha256",
    # rfc3161
    "der_len", "der_tlv", "der_int", "der_oid",
    "build_timestamp_request", "record_timestamp_response",
    # backend
    "have_cryptography", "active_backend",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class CertificateError(Exception):
    """Base class for every certificate failure."""


class CertificateFormatError(CertificateError):
    """The bytes are not a well-formed certificate (magic/version/length)."""


class CertificateAuthError(CertificateError):
    """AEAD authentication failed: wrong key, or the ciphertext was altered."""


# ---------------------------------------------------------------------------
# Canonical encoding
# ---------------------------------------------------------------------------

def lp(b: bytes) -> bytes:
    """Length-prefix ``b`` with a big-endian uint32.

    Matches ``wm_prf.hmac_digest``'s framing so that "the identifier ``a|bc``"
    and "the identifier ``ab|c``" can never serialize to the same bytes.
    """
    return struct.pack(">I", len(b)) + b


def lps(*parts: bytes) -> bytes:
    """Concatenate length-prefixed ``parts``."""
    return b"".join(lp(p) for p in parts)


def canonical_json(obj: Any) -> bytes:
    """Deterministic JSON: sorted keys, compact separators, UTF-8.

    Intentionally duplicates ``gen_key/seed_common.canonicalize_message``
    instead of importing it -- that module pulls in the Ed25519 backend loaders
    and is not on the import path available inside ``openroad -python``.  Keep
    the two in step if either changes.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


class _Reader:
    """Cursor over a length-prefixed byte stream."""

    __slots__ = ("_buf", "_pos")

    def __init__(self, buf: bytes) -> None:
        self._buf = buf
        self._pos = 0

    def u32(self) -> int:
        if self._pos + 4 > len(self._buf):
            raise CertificateFormatError("truncated: expected a uint32")
        (n,) = struct.unpack_from(">I", self._buf, self._pos)
        self._pos += 4
        return n

    def lp(self) -> bytes:
        n = self.u32()
        if self._pos + n > len(self._buf):
            raise CertificateFormatError(
                f"truncated: field claims {n} bytes, "
                f"{len(self._buf) - self._pos} remain")
        out = self._buf[self._pos:self._pos + n]
        self._pos += n
        return out

    def expect_end(self, what: str) -> None:
        if self._pos != len(self._buf):
            raise CertificateFormatError(
                f"{what}: {len(self._buf) - self._pos} trailing bytes")


def _dec(b: bytes) -> str:
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError as e:
        raise CertificateFormatError(f"field is not valid UTF-8: {e}") from e


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------

class PlacementClaim(NamedTuple):
    """One accepted placement tuple.

    Every field is stored as the **exact text of the embed-CSV cell**, never as
    an int or a bool.  That is what lets a certificate-sourced row be compared
    byte-for-byte against a ``csv.DictReader`` row, which in turn is what makes
    "the certificate path behaves identically" a testable property rather than
    an assertion.  ``c_name`` is ``""`` for pairs; exactly one of
    ``target_bit`` / ``target_perm`` is populated, matching the CSV.
    """

    kind: str            # "pair" | "triple"
    a_name: str
    b_name: str
    c_name: str
    target_bit: str
    target_perm: str


class CtsClaim(NamedTuple):
    """One accepted CTS target LCB.

    ``repair_fanout_*`` keep the empty-vs-zero distinction, because
    ``cts_watermark_verify._parse_opt_int`` treats ``None`` and ``0``
    differently and that difference gates the tamper check.
    """

    target_lcb: str
    other_lcb: str
    pair_key: str
    l_a: str
    l_b: str
    channel: str
    target_bit: str
    repair_fanout_target: str
    repair_fanout_other: str
    pair_idx: str


class Gamma(NamedTuple):
    meta: Dict[str, Any]
    placement: Tuple[PlacementClaim, ...]
    cts: Tuple[CtsClaim, ...]


GAMMA_MAGIC = b"PDMKG1"


def _enc_fields(fields: Sequence[str]) -> bytes:
    return lps(*(f.encode("utf-8") for f in fields))


def _enc_placement(c: PlacementClaim) -> bytes:
    ident = _enc_fields((c.kind, c.a_name, c.b_name, c.c_name))
    target = _enc_fields((c.target_bit, c.target_perm))
    return lps(ident, target)


def _enc_cts(c: CtsClaim) -> bytes:
    ident = _enc_fields((c.target_lcb, c.other_lcb, c.pair_key,
                         c.l_a, c.l_b, c.channel))
    target = _enc_fields((c.target_bit, c.repair_fanout_target,
                          c.repair_fanout_other, c.pair_idx))
    return lps(ident, target)


def _dec_fields(blob: bytes, count: int, what: str) -> List[str]:
    r = _Reader(blob)
    out = [_dec(r.lp()) for _ in range(count)]
    r.expect_end(what)
    return out


def _enc_claim_list(claims: Sequence[bytes]) -> bytes:
    return struct.pack(">I", len(claims)) + b"".join(lp(c) for c in claims)


def serialize_gamma(meta: Dict[str, Any],
                    placement: Sequence[PlacementClaim],
                    cts: Sequence[CtsClaim]) -> bytes:
    """Serialize Gamma to its canonical, length-prefixed form."""
    gp = _enc_claim_list([_enc_placement(c) for c in placement])
    gc = _enc_claim_list([_enc_cts(c) for c in cts])
    return lps(GAMMA_MAGIC, canonical_json(meta), gp, gc)


def parse_gamma(blob: bytes) -> Gamma:
    """Inverse of :func:`serialize_gamma`; raises :class:`CertificateFormatError`."""
    r = _Reader(blob)
    magic = r.lp()
    if magic != GAMMA_MAGIC:
        raise CertificateFormatError(
            f"bad Gamma magic {magic!r}, expected {GAMMA_MAGIC!r}")
    try:
        meta = json.loads(_dec(r.lp()))
    except ValueError as e:
        raise CertificateFormatError(f"Gamma metadata is not valid JSON: {e}") from e
    if not isinstance(meta, dict):
        raise CertificateFormatError("Gamma metadata is not a JSON object")

    gp_blob = r.lp()
    gc_blob = r.lp()
    r.expect_end("Gamma")

    rp = _Reader(gp_blob)
    placement: List[PlacementClaim] = []
    for _ in range(rp.u32()):
        cr = _Reader(rp.lp())
        ident = cr.lp()
        target = cr.lp()
        cr.expect_end("placement claim")
        kind, a, b, c = _dec_fields(ident, 4, "placement identifier")
        tb, tp = _dec_fields(target, 2, "placement target")
        placement.append(PlacementClaim(kind, a, b, c, tb, tp))
    rp.expect_end("Gamma_P")

    rc = _Reader(gc_blob)
    cts: List[CtsClaim] = []
    for _ in range(rc.u32()):
        cr = _Reader(rc.lp())
        ident = cr.lp()
        target = cr.lp()
        cr.expect_end("cts claim")
        tl, ol, pk, la, lb, ch = _dec_fields(ident, 6, "cts identifier")
        tb, rt, ro, pi = _dec_fields(target, 4, "cts target")
        cts.append(CtsClaim(tl, ol, pk, la, lb, ch, tb, rt, ro, pi))
    rc.expect_end("Gamma_C")

    return Gamma(meta=meta, placement=tuple(placement), cts=tuple(cts))


# ---------------------------------------------------------------------------
# ID(D_0)
# ---------------------------------------------------------------------------

ID_D0_SCHEMA = "pdmarks-id-d0/1"

#: Artifacts hashed to pin the pre-physical-design state.  Ordered and fixed:
#: an artifact that is absent contributes its name to ``missing`` instead of a
#: digest, so ID(D0) stays well-defined -- and explicitly so -- for a partially
#: populated results directory.  Note this ORFS emits ``1_2_yosys.v``; there is
#: no ``1_synth.v``.
ID_D0_ARTIFACTS_FULL: Tuple[str, ...] = (
    "1_2_yosys.v", "1_synth.odb", "2_floorplan.odb", "3_place.odb",
    "3_place.sdc", "4_cts.odb", "4_cts.sdc", "clock_period.txt",
)

#: Text-only subset (~50 KB instead of hundreds of MB) for CI and smoke runs.
#: The profile name is part of the preimage, so the two can never be conflated.
ID_D0_ARTIFACTS_LIGHT: Tuple[str, ...] = (
    "1_2_yosys.v", "3_place.sdc", "4_cts.sdc", "clock_period.txt",
)

_ID_D0_PROFILES = {
    "full": ID_D0_ARTIFACTS_FULL,
    "light": ID_D0_ARTIFACTS_LIGHT,
}


def sha256_file(path: str, chunk: int = 1 << 20) -> Tuple[str, int]:
    """Return ``(hexdigest, size_in_bytes)``, streaming so large ODBs are cheap."""
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            size += len(block)
            h.update(block)
    return h.hexdigest(), size


def build_id_d0_preimage(*,
                         design: str,
                         design_nickname: str,
                         platform: str,
                         ref_flow_variant: str,
                         flow_home: str,
                         wm_config: Dict[str, Any],
                         tool: Dict[str, Any],
                         profile: str = "full",
                         results_dir: Optional[str] = None) -> Dict[str, Any]:
    """Assemble the JSON object whose SHA-256 is ``ID(D0)``.

    ``results_dir`` defaults to ``<flow_home>/results/<platform>/<nickname>/
    <ref_flow_variant>``, mirroring ``experiments/lib/orfs.flow_results`` (not
    imported: that module is not reachable from inside ``openroad -python``).
    """
    try:
        artifacts_wanted = _ID_D0_PROFILES[profile]
    except KeyError:
        raise ValueError(
            f"unknown ID(D0) artifact profile {profile!r}; "
            f"expected one of {sorted(_ID_D0_PROFILES)}") from None

    if results_dir is None:
        results_dir = os.path.join(flow_home, "results", platform,
                                   design_nickname, ref_flow_variant)

    artifacts: List[Dict[str, Any]] = []
    missing: List[str] = []
    for name in artifacts_wanted:
        path = os.path.join(results_dir, name)
        if os.path.isfile(path):
            digest, size = sha256_file(path)
            artifacts.append({"name": name, "sha256": digest, "bytes": size})
        else:
            missing.append(name)

    config_mk_path = os.path.join(flow_home, "designs", platform, design,
                                  "config.mk")
    config_mk: Dict[str, Any] = {
        "path": os.path.relpath(config_mk_path, flow_home),
    }
    if os.path.isfile(config_mk_path):
        config_mk["sha256"] = sha256_file(config_mk_path)[0]
    else:
        config_mk["sha256"] = None

    return {
        "schema": ID_D0_SCHEMA,
        "artifact_profile": profile,
        "design": design,
        "design_nickname": design_nickname,
        "platform": platform,
        "ref_flow_variant": ref_flow_variant,
        "artifacts": artifacts,
        "missing": sorted(missing),
        "config_mk": config_mk,
        "wm_config": wm_config,
        "tool": tool,
    }


def id_d0(preimage: Dict[str, Any]) -> bytes:
    """``ID(D0)`` = SHA-256 over the canonical JSON of the preimage."""
    return hashlib.sha256(canonical_json(preimage)).digest()


# ---------------------------------------------------------------------------
# Key derivation, commitment, record
# ---------------------------------------------------------------------------

CERT_LABEL = b"cert"

#: Self-describing note stored in wm_commit.json so the timestamped preimage is
#: reconstructible from the JSON alone.
RECORD_ENCODING = "lp32(id_d0)||lp32(nu)||lp32(cert_sha256)||lp32(commitment)"


def derive_cert_key(master_seed: bytes, id_d0_bytes: bytes, nu: bytes) -> bytes:
    """Eq. 17: the AES-256 key that seals Gamma."""
    _require_len("master_seed", master_seed, 32)
    _require_len("id_d0", id_d0_bytes, 32)
    _require_len("nu", nu, 12)
    return hmac_digest(master_seed, id_d0_bytes, nu, CERT_LABEL)


def commitment(master_seed: bytes, id_d0_bytes: bytes, nu: bytes,
               cert_sha256_bytes: bytes) -> bytes:
    """Eq. 19: ``c = SHA256(K || ID(D0) || nu || SHA256(C_Gamma))``.

    All four fields are fixed-length, so plain concatenation is unambiguous;
    this is written exactly as the paper states rather than length-prefixed.
    """
    _require_len("master_seed", master_seed, 32)
    _require_len("id_d0", id_d0_bytes, 32)
    _require_len("nu", nu, 12)
    _require_len("cert_sha256", cert_sha256_bytes, 32)
    return hashlib.sha256(
        master_seed + id_d0_bytes + nu + cert_sha256_bytes).digest()


def check_commitment(master_seed: bytes, id_d0_bytes: bytes, nu: bytes,
                     cert_sha256_bytes: bytes, expected: bytes) -> bool:
    """Constant-time check of Eq. 19."""
    return hmac.compare_digest(
        commitment(master_seed, id_d0_bytes, nu, cert_sha256_bytes), expected)


def record_bytes(id_d0_bytes: bytes, nu: bytes, cert_sha256_bytes: bytes,
                 c: bytes) -> bytes:
    """``R``, the tuple that gets timestamped.

    Heterogeneous, so unlike Eq. 19 it *is* length-prefixed; see
    :data:`RECORD_ENCODING`.
    """
    return lps(id_d0_bytes, nu, cert_sha256_bytes, c)


def record_sha256(id_d0_bytes: bytes, nu: bytes, cert_sha256_bytes: bytes,
                  c: bytes) -> bytes:
    """SHA-256 of ``R`` -- the digest placed in the RFC 3161 request."""
    return hashlib.sha256(
        record_bytes(id_d0_bytes, nu, cert_sha256_bytes, c)).digest()


def _require_len(what: str, value: bytes, n: int) -> None:
    if not isinstance(value, (bytes, bytearray)):
        raise TypeError(f"{what} must be bytes, got {type(value).__name__}")
    if len(value) != n:
        raise ValueError(f"{what} must be {n} bytes, got {len(value)}")


def load_master_seed(source: str) -> bytes:
    """Resolve K from a hex literal, a ``*.hex`` file, or a ``bundle.json``.

    Accepting ``bundle.json`` matters for backward compatibility: bundles
    generated before ``sign_and_derive.py`` learned to write ``master_seed.hex``
    still carry ``master_seed_hex`` inside the JSON.
    """
    if not source:
        raise ValueError("no master seed source given")

    text = source.strip()
    if not os.path.exists(text):
        try:
            raw = bytes.fromhex(text)
        except ValueError:
            raise ValueError(
                f"master seed source is neither an existing path nor hex: "
                f"{source!r}") from None
        _require_len("master seed", raw, 32)
        return raw

    if text.endswith(".json"):
        with open(text) as f:
            data = json.load(f)
        hexed = data.get("master_seed_hex")
        if not hexed:
            raise ValueError(f"{text} has no 'master_seed_hex' field")
    else:
        with open(text) as f:
            fields = f.read().split()
        if not fields:
            raise ValueError(f"{text} is empty")
        hexed = fields[0]

    try:
        raw = bytes.fromhex(hexed)
    except ValueError as e:
        raise ValueError(f"{text} does not contain valid hex: {e}") from None
    _require_len("master seed", raw, 32)
    return raw


# ---------------------------------------------------------------------------
# AEAD backend
# ---------------------------------------------------------------------------

try:  # pragma: no cover - depends on the host
    from cryptography.hazmat.primitives.ciphers.aead import (  # type: ignore
        AESGCM as _RefAESGCM,
    )
    from cryptography.exceptions import InvalidTag as _RefInvalidTag  # type: ignore
    _HAVE_CRYPTOGRAPHY = True
except Exception:  # pragma: no cover
    _RefAESGCM = None
    _RefInvalidTag = None
    _HAVE_CRYPTOGRAPHY = False

_INVALID_TAG = (
    (wm_aesgcm.InvalidTag, _RefInvalidTag) if _RefInvalidTag is not None
    else (wm_aesgcm.InvalidTag,)
)


def have_cryptography() -> bool:
    """True when the OpenSSL-backed AEAD is importable in this interpreter."""
    return _HAVE_CRYPTOGRAPHY


def _force_pure() -> bool:
    return os.environ.get("WM_CERT_FORCE_PURE_AES", "") not in ("", "0")


def active_backend() -> str:
    """``"cryptography"`` or ``"vendored"`` -- recorded in logs and verdicts."""
    return "cryptography" if (_HAVE_CRYPTOGRAPHY and not _force_pure()) else "vendored"


def _aead(key: bytes):
    if _HAVE_CRYPTOGRAPHY and not _force_pure():
        return _RefAESGCM(key)
    return wm_aesgcm.AESGCM(key)


# ---------------------------------------------------------------------------
# Certificate file
# ---------------------------------------------------------------------------

CERT_MAGIC = b"PDMKCERT"
CERT_VERSION = 1
#: Suite 1 = HMAC-SHA256 KDF | AES-256-GCM | SHA-256 commitment.
CERT_SUITE = 1
CERT_HEADER_LEN = 60
_TAG_LEN = 16


class CertificateHeader(NamedTuple):
    version: int
    suite: int
    id_d0: bytes
    nu: bytes
    ct_len: int
    body: bytes          # C_Gamma == ciphertext || tag == bytes 60..EOF


class Certificate(NamedTuple):
    header: CertificateHeader
    gamma: Gamma
    backend: str


def _aad(id_d0_bytes: bytes, nu: bytes) -> bytes:
    """Eq. 18's associated data: the pair (ID(D0), nu), length-prefixed."""
    return lps(id_d0_bytes, nu)


def cert_sha256(cert_blob: bytes) -> bytes:
    """``SHA256(C_Gamma)`` where ``C_Gamma`` is the file from offset 60 to EOF.

    Defined on the raw bytes so a third party can reproduce it with
    ``tail -c +61 wm_cert.bin | sha256sum`` and no parser at all.
    """
    if len(cert_blob) < CERT_HEADER_LEN:
        raise CertificateFormatError("certificate shorter than its header")
    return hashlib.sha256(cert_blob[CERT_HEADER_LEN:]).digest()


def seal_certificate(master_seed: bytes, *,
                     id_d0_bytes: bytes,
                     nu: bytes,
                     meta: Dict[str, Any],
                     placement: Sequence[PlacementClaim],
                     cts: Sequence[CtsClaim]) -> bytes:
    """Build, encrypt and frame a certificate.  Returns the ``wm_cert.bin`` bytes.

    ``cert_version`` and ``suite`` are written into ``meta`` as well as the
    plaintext header: the header sits outside the AEAD's associated data and is
    therefore malleable, so :func:`open_certificate` cross-checks the two and
    refuses a mismatch.  That closes the downgrade path without departing from
    the AAD the paper specifies.
    """
    _require_len("id_d0", id_d0_bytes, 32)
    _require_len("nu", nu, 12)

    sealed_meta = dict(meta)
    sealed_meta["cert_version"] = CERT_VERSION
    sealed_meta["suite"] = CERT_SUITE

    plaintext = serialize_gamma(sealed_meta, placement, cts)
    key = derive_cert_key(master_seed, id_d0_bytes, nu)
    body = _aead(key).encrypt(nu, plaintext, _aad(id_d0_bytes, nu))

    ct_len = len(body) - _TAG_LEN
    header = (CERT_MAGIC
              + struct.pack(">H", CERT_VERSION)
              + bytes([CERT_SUITE, 0])
              + id_d0_bytes
              + nu
              + struct.pack(">I", ct_len))
    assert len(header) == CERT_HEADER_LEN, len(header)
    return header + body


def parse_certificate_header(cert_blob: bytes) -> CertificateHeader:
    """Validate framing and split off ``C_Gamma``.  No key required."""
    if len(cert_blob) < CERT_HEADER_LEN + _TAG_LEN:
        raise CertificateFormatError(
            f"certificate is {len(cert_blob)} bytes, "
            f"need at least {CERT_HEADER_LEN + _TAG_LEN}")
    if cert_blob[:8] != CERT_MAGIC:
        raise CertificateFormatError(
            f"bad magic {cert_blob[:8]!r}, expected {CERT_MAGIC!r}")

    (version,) = struct.unpack_from(">H", cert_blob, 8)
    suite = cert_blob[10]
    if version != CERT_VERSION:
        raise CertificateFormatError(
            f"unsupported certificate version {version} "
            f"(this build understands {CERT_VERSION})")
    if suite != CERT_SUITE:
        raise CertificateFormatError(
            f"unsupported cipher suite {suite} "
            f"(this build understands {CERT_SUITE})")

    id_d0_bytes = cert_blob[12:44]
    nu = cert_blob[44:56]
    (ct_len,) = struct.unpack_from(">I", cert_blob, 56)
    body = cert_blob[CERT_HEADER_LEN:]
    if len(body) != ct_len + _TAG_LEN:
        raise CertificateFormatError(
            f"header declares {ct_len} ciphertext bytes (+{_TAG_LEN} tag) "
            f"but {len(body)} bytes follow the header")
    return CertificateHeader(version=version, suite=suite, id_d0=id_d0_bytes,
                             nu=nu, ct_len=ct_len, body=body)


def open_certificate(master_seed: bytes, cert_blob: bytes) -> Certificate:
    """Authenticate and decrypt (Eqs. 17-18).

    Raises :class:`CertificateAuthError` on a wrong key or altered bytes, and
    :class:`CertificateFormatError` on malformed framing.
    """
    header = parse_certificate_header(cert_blob)
    key = derive_cert_key(master_seed, header.id_d0, header.nu)
    try:
        plaintext = _aead(key).decrypt(
            header.nu, header.body, _aad(header.id_d0, header.nu))
    except _INVALID_TAG as e:
        raise CertificateAuthError(
            "certificate failed authentication: the claimed key is wrong or "
            "the ciphertext was modified") from e

    gamma = parse_gamma(plaintext)

    # The header is outside the AAD, so trust the sealed copy over it.
    sealed_version = gamma.meta.get("cert_version")
    sealed_suite = gamma.meta.get("suite")
    if sealed_version != header.version or sealed_suite != header.suite:
        raise CertificateFormatError(
            f"header (version={header.version}, suite={header.suite}) "
            f"disagrees with the sealed metadata "
            f"(version={sealed_version}, suite={sealed_suite}); "
            "the header may have been tampered with")

    return Certificate(header=header, gamma=gamma, backend=active_backend())


# ---------------------------------------------------------------------------
# RFC 3161 time-stamp request
# ---------------------------------------------------------------------------

OID_SHA256 = "2.16.840.1.101.3.4.2.1"


def der_len(n: int) -> bytes:
    """DER length octets, short form below 128 and long form above."""
    if n < 0:
        raise ValueError("length must be non-negative")
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    if len(body) > 0x7E:
        raise ValueError("length too large for DER long form")
    return bytes([0x80 | len(body)]) + body


def der_tlv(tag: int, payload: bytes) -> bytes:
    return bytes([tag]) + der_len(len(payload)) + payload


def der_int(value: int) -> bytes:
    """DER INTEGER, minimal and non-negative.

    ``int.to_bytes`` already yields a minimal big-endian encoding; the extra
    ``0x00`` is required whenever the top bit is set, or the value would be read
    back as negative.  A random 8-byte nonce trips that about half the time.
    """
    if value < 0:
        raise ValueError("negative INTEGERs are not needed here")
    if value == 0:
        return der_tlv(0x02, b"\x00")
    body = value.to_bytes((value.bit_length() + 7) // 8, "big")
    if body[0] & 0x80:
        body = b"\x00" + body
    return der_tlv(0x02, body)


def der_oid(dotted: str) -> bytes:
    parts = [int(p) for p in dotted.split(".")]
    if len(parts) < 2:
        raise ValueError(f"not an OID: {dotted!r}")
    if parts[0] > 2 or (parts[0] < 2 and parts[1] > 39):
        raise ValueError(f"OID arc out of range: {dotted!r}")
    body = bytearray([40 * parts[0] + parts[1]])
    for arc in parts[2:]:
        if arc < 0:
            raise ValueError(f"negative OID arc in {dotted!r}")
        chunk = [arc & 0x7F]
        arc >>= 7
        while arc:
            chunk.append((arc & 0x7F) | 0x80)
            arc >>= 7
        body.extend(reversed(chunk))
    return der_tlv(0x06, bytes(body))


def build_timestamp_request(digest: bytes, *,
                            hash_oid: str = OID_SHA256,
                            nonce: Optional[int] = None,
                            cert_req: bool = True,
                            policy_oid: Optional[str] = None,
                            algo_null: bool = True) -> bytes:
    """A DER-encoded RFC 3161 ``TimeStampReq`` over ``digest``.

    The explicit ``NULL`` algorithm parameters are emitted by default.  RFC 5754
    says SHA-2 identifiers SHOULD omit them, but OpenSSL's ``ts -query`` includes
    them -- and matching OpenSSL byte for byte is what lets the test suite
    cross-check this hand-rolled encoder.  Pass ``algo_null=False`` for a TSA
    that objects.

    ``certReq`` is ``DEFAULT FALSE``, so DER forbids encoding it when false; the
    field is emitted only when ``cert_req`` is true.
    """
    if len(digest) != 32 and hash_oid == OID_SHA256:
        raise ValueError(f"SHA-256 digest must be 32 bytes, got {len(digest)}")

    algo = der_oid(hash_oid) + (der_tlv(0x05, b"") if algo_null else b"")
    imprint = der_tlv(0x30, der_tlv(0x30, algo) + der_tlv(0x04, digest))

    body = der_int(1) + imprint
    if policy_oid:
        body += der_oid(policy_oid)
    if nonce is not None:
        body += der_int(nonce)
    if cert_req:
        body += der_tlv(0x01, b"\xff")
    return der_tlv(0x30, body)


def record_timestamp_response(tsr_path: str,
                              tsq_path: Optional[str] = None,
                              *,
                              cafile: Optional[str] = None,
                              untrusted: Optional[str] = None,
                              timeout: int = 20) -> Dict[str, Any]:
    """Describe a TSA response token, verifying it when that is possible.

    Verification is delegated to ``openssl ts -verify`` rather than
    reimplemented: checking a token means full CMS signature validation and
    chain building, which is not something to hand-roll next to the DER encoder.
    Every failure mode -- no openssl, no CA file, a bad token -- is reported as
    ``verified: False`` with a reason and is **never** fatal, so an offline or
    openssl-less host can still complete a verification run.
    """
    result: Dict[str, Any] = {
        "tsr_path": tsr_path,
        "tsr_sha256": None,
        "verified": False,
        "reason": "",
        "gen_time": None,
    }
    if not tsr_path or not os.path.isfile(tsr_path):
        result["reason"] = "no timestamp token supplied"
        return result

    with open(tsr_path, "rb") as f:
        token = f.read()
    result["tsr_sha256"] = hashlib.sha256(token).hexdigest()

    openssl = _which("openssl")
    if openssl is None:
        result["reason"] = "openssl not on PATH; token recorded but unverified"
        return result

    # genTime is informational and available without any trust anchor.
    try:
        text = subprocess.run(
            [openssl, "ts", "-reply", "-in", tsr_path, "-text"],
            check=False, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        ).stdout.decode("utf-8", "replace")
        for line in text.splitlines():
            if "Time stamp:" in line:
                result["gen_time"] = line.split("Time stamp:", 1)[1].strip()
                break
    except Exception as e:  # pragma: no cover - defensive
        result["reason"] = f"could not read the token: {e}"

    if not cafile:
        result["reason"] = (result["reason"] or
                            "no TSA CA certificate supplied; token unverified")
        return result
    if not tsq_path or not os.path.isfile(tsq_path):
        result["reason"] = "no request file to verify the token against"
        return result

    cmd = [openssl, "ts", "-verify", "-in", tsr_path,
           "-queryfile", tsq_path, "-CAfile", cafile]
    if untrusted:
        cmd += ["-untrusted", untrusted]
    try:
        proc = subprocess.run(cmd, check=False, timeout=timeout,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except Exception as e:  # pragma: no cover - defensive
        result["reason"] = f"openssl ts -verify could not run: {e}"
        return result

    out = proc.stdout.decode("utf-8", "replace").strip()
    if proc.returncode == 0 and "Verification: OK" in out:
        result["verified"] = True
        result["reason"] = "openssl ts -verify: OK"
    else:
        result["reason"] = f"openssl ts -verify failed: {out.splitlines()[-1] if out else proc.returncode}"
    return result


def _which(prog: str) -> Optional[str]:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = os.path.join(directory, prog)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None
