# SPDX-License-Identifier: BSD-3-Clause
"""Dependency-free AES-GCM, vendored for the PDMarks watermark certificate.

Why this file exists
--------------------
The placement and CTS claim-verifiers run inside OpenROAD's embedded Python
(``openroad -python``).  That interpreter is reached through
``wm_env.sh:wm_exec``, which invokes ``singularity exec -e`` (``--cleanenv``),
and ``place_wm.sh`` / ``cts_wm.sh`` then reset ``PYTHONPATH`` to the stage
directory.  A host ``pip install cryptography`` is therefore invisible to it,
and the CPython standard library has HMAC and SHA-2 but **no AEAD**.  So a
verifier that must open an AES-256-GCM certificate in-process has nothing to
call -- unless the implementation ships with it.

``wm_cert`` prefers :mod:`cryptography` whenever it imports cleanly and only
falls back to this module otherwise, mirroring the dual-backend pattern already
used for Ed25519 in ``gen_key/seed_common.py``.  Both backends are held to the
same NIST CAVP vectors, and ``certificate/tests/test_aesgcm.py`` additionally
cross-checks them against each other on randomized inputs.

Scope and caveats
-----------------
* Implements AES-128/192/256 in GCM, per FIPS-197 and NIST SP 800-38D.
* Pure Python, measured at ~0.19 MB/s on CPython 3.9 (key setup ~3 ms).  A
  watermark certificate is ~17 KB for the largest evaluated design, i.e. ~90 ms
  to seal and ~90 ms to open.  That is fine for a handful of calls per
  verification; do **not** use this for megabyte payloads.  The block cipher is
  written for auditability rather than speed -- a T-table rewrite would be a few
  times faster, which is not worth the extra surface at these sizes.
* **Not constant-time.**  S-box and GHASH table lookups are data-dependent, and
  CPython offers no way to avoid that.  This is acceptable here because the
  certificate is opened on the owner's own machine from a key the owner already
  holds; it would not be acceptable in a service that decrypts attacker-supplied
  ciphertexts under a long-lived key.  ``cryptography``'s OpenSSL backend is
  preferred wherever it is available.

Public API (a subset of ``cryptography.hazmat.primitives.ciphers.aead.AESGCM``)::

    AESGCM(key).encrypt(nonce, data, associated_data) -> ciphertext || tag
    AESGCM(key).decrypt(nonce, data, associated_data) -> plaintext
    InvalidTag                                          raised on auth failure
"""

from __future__ import annotations

import hmac as _hmac
from typing import List, Optional

__all__ = ["AESGCM", "InvalidTag", "TAG_SIZE", "NONCE_SIZE"]

TAG_SIZE = 16
NONCE_SIZE = 12          # the 96-bit nonce GCM is specified for, and what the
                         # PDMarks certificate uses (paper's nu)


class InvalidTag(Exception):
    """Raised when GCM authentication fails.

    Deliberately mirrors ``cryptography.exceptions.InvalidTag`` so callers can
    catch either backend's failure with one ``except`` clause.
    """


# ---------------------------------------------------------------------------
# AES (FIPS-197)
# ---------------------------------------------------------------------------

# The FIPS-197 Figure 7 substitution box, verbatim.  Shipped as a literal rather
# than generated from the GF(2^8) inverse + affine map: a typo in a generator
# loop is silent, whereas this table is checkable by eye against the standard
# and is pinned by the FIPS-197 known-answer test.
_SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76"
    "ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d83115"
    "04c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f84"
    "53d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa8"
    "51a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d1973"
    "60814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479"
    "e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a"
    "703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df"
    "8ca1890dbfe6426841992d0fb054bb16"
)
assert len(_SBOX) == 256


def _xtime(a: int) -> int:
    """Multiply by x in GF(2^8) modulo x^8 + x^4 + x^3 + x + 1."""
    a <<= 1
    if a & 0x100:
        a ^= 0x11B
    return a & 0xFF


# MixColumns needs multiplication by 2 and 3 only.
_M2 = bytes(_xtime(i) for i in range(256))
_M3 = bytes(_M2[i] ^ i for i in range(256))


def _expand_key(key: bytes) -> List[bytes]:
    """FIPS-197 key expansion -> one 16-byte round key per round, Nr+1 of them."""
    # Validate the byte length, not len(key)//4: a 17-byte key floors to nk=4
    # and would otherwise be silently truncated to its first 16 bytes.
    if len(key) not in (16, 24, 32):
        raise ValueError(f"AES key must be 16, 24 or 32 bytes, got {len(key)}")
    nk = len(key) // 4
    nr = nk + 6
    words: List[List[int]] = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    rcon = 1
    for i in range(nk, 4 * (nr + 1)):
        t = list(words[i - 1])
        if i % nk == 0:
            t = t[1:] + t[:1]                       # RotWord
            t = [_SBOX[b] for b in t]               # SubWord
            t[0] ^= rcon
            rcon = _xtime(rcon)
        elif nk > 6 and i % nk == 4:
            # AES-256 only: the extra SubWord on the middle word.  This branch
            # is the single most commonly omitted line in hand-written AES, and
            # is exactly what the FIPS-197 Appendix C.3 vector pins down.
            t = [_SBOX[b] for b in t]
        prev = words[i - nk]
        words.append([prev[j] ^ t[j] for j in range(4)])
    return [
        bytes(b for word in words[4 * r:4 * r + 4] for b in word)
        for r in range(nr + 1)
    ]


def _encrypt_block(round_keys: List[bytes], block: bytes) -> bytes:
    """One AES block encryption.  ``block`` is 16 bytes, column-major per FIPS-197."""
    sbox = _SBOX
    m2 = _M2
    m3 = _M3

    s = bytearray(16)
    rk0 = round_keys[0]
    for i in range(16):
        s[i] = block[i] ^ rk0[i]

    t = bytearray(16)
    last = len(round_keys) - 1
    for rnd in range(1, last):
        rk = round_keys[rnd]
        for c in range(4):
            # SubBytes composed with ShiftRows: output column c draws row r from
            # input column (c + r) % 4.
            a0 = sbox[s[(c << 2)]]
            a1 = sbox[s[1 + (((c + 1) & 3) << 2)]]
            a2 = sbox[s[2 + (((c + 2) & 3) << 2)]]
            a3 = sbox[s[3 + (((c + 3) & 3) << 2)]]
            j = c << 2
            t[j] = m2[a0] ^ m3[a1] ^ a2 ^ a3 ^ rk[j]
            t[j + 1] = a0 ^ m2[a1] ^ m3[a2] ^ a3 ^ rk[j + 1]
            t[j + 2] = a0 ^ a1 ^ m2[a2] ^ m3[a3] ^ rk[j + 2]
            t[j + 3] = m3[a0] ^ a1 ^ a2 ^ m2[a3] ^ rk[j + 3]
        s, t = t, s

    # Final round: no MixColumns.
    rk = round_keys[last]
    out = bytearray(16)
    for c in range(4):
        j = c << 2
        out[j] = sbox[s[(c << 2)]] ^ rk[j]
        out[j + 1] = sbox[s[1 + (((c + 1) & 3) << 2)]] ^ rk[j + 1]
        out[j + 2] = sbox[s[2 + (((c + 2) & 3) << 2)]] ^ rk[j + 2]
        out[j + 3] = sbox[s[3 + (((c + 3) & 3) << 2)]] ^ rk[j + 3]
    return bytes(out)


# ---------------------------------------------------------------------------
# GHASH (NIST SP 800-38D section 6.4)
# ---------------------------------------------------------------------------

# The GCM reduction polynomial, in the "bit-reversed" representation the spec
# uses: a 16-byte block read as a big-endian integer has the coefficient of x^0
# in its most significant bit, so multiplying by x is a *right* shift.
_R = 0xE1 << 120


def _mul_x(v: int) -> int:
    """Multiply by x in GF(2^128) under GCM's bit ordering."""
    return (v >> 1) ^ _R if v & 1 else v >> 1


def _gmul_slow(x: int, y: int) -> int:
    """Bit-serial GF(2^128) multiply.

    Kept only as a test oracle: ``certificate/tests/test_aesgcm.py`` asserts the
    table-driven :class:`_GHash` agrees with it on randomized inputs.  Roughly
    an order of magnitude slower, and never used on the hot path.
    """
    z = 0
    v = y
    for i in range(128):
        if (x >> (127 - i)) & 1:
            z ^= v
        v = _mul_x(v)
    return z


class _GHash:
    """Table-driven multiplication by a fixed H, one table per byte position.

    Setup builds 16 x 256 field elements from ``POW[i] = H * x^i`` (~35k integer
    XORs, a few milliseconds); each subsequent multiply is 16 lookups and 16
    XORs instead of 128 conditional shifts.
    """

    __slots__ = ("_tab",)

    def __init__(self, h: bytes) -> None:
        hv = int.from_bytes(h, "big")
        powers = [0] * 128
        v = hv
        for i in range(128):
            powers[i] = v
            v = _mul_x(v)

        tab: List[List[int]] = []
        for j in range(16):
            base = 8 * j
            col = [0] * 256
            for k in range(8):
                # Byte position j spans coefficients x^(8j)..x^(8j+7); within the
                # byte, the most significant bit is the lowest power.
                p = powers[base + k]
                bit = 1 << (7 - k)
                for b in range(256):
                    if b & bit:
                        col[b] ^= p
            tab.append(col)
        self._tab = tab

    def mul_h(self, x: int) -> int:
        tab = self._tab
        r = 0
        for j in range(16):
            r ^= tab[j][(x >> (8 * (15 - j))) & 0xFF]
        return r

    def digest_int(self, data: bytes) -> int:
        """GHASH over ``data``, whose length must be a multiple of 16."""
        y = 0
        mul_h = self.mul_h
        frombytes = int.from_bytes
        for off in range(0, len(data), 16):
            y = mul_h(y ^ frombytes(data[off:off + 16], "big"))
        return y


def _pad16(b: bytes) -> bytes:
    r = len(b) & 15
    return b if r == 0 else b + b"\x00" * (16 - r)


# ---------------------------------------------------------------------------
# GCM
# ---------------------------------------------------------------------------

def _inc32(block: bytes) -> bytes:
    """Increment the rightmost 32 bits of a 16-byte counter block, mod 2^32."""
    ctr = (int.from_bytes(block[12:], "big") + 1) & 0xFFFFFFFF
    return block[:12] + ctr.to_bytes(4, "big")


def _gctr(round_keys: List[bytes], icb: bytes, data: bytes) -> bytes:
    if not data:
        return b""
    out = bytearray(len(data))
    cb = icb
    n = len(data)
    pos = 0
    while pos < n:
        ks = _encrypt_block(round_keys, cb)
        chunk = data[pos:pos + 16]
        for i, byte in enumerate(chunk):
            out[pos + i] = byte ^ ks[i]
        pos += 16
        if pos < n:
            cb = _inc32(cb)
    return bytes(out)


class AESGCM:
    """AES-GCM with the same surface as ``cryptography``'s ``AESGCM``.

    The key schedule and the GHASH table are computed once per instance, so
    reuse the instance when sealing or opening several blobs under one key.
    """

    __slots__ = ("_rk", "_gh")

    def __init__(self, key: bytes) -> None:
        if not isinstance(key, (bytes, bytearray, memoryview)):
            raise TypeError("key must be bytes")
        key = bytes(key)
        self._rk = _expand_key(key)
        # H = E_K(0^128)
        self._gh = _GHash(_encrypt_block(self._rk, b"\x00" * 16))

    # -- internals ----------------------------------------------------------

    def _j0(self, nonce: bytes) -> bytes:
        if len(nonce) == 12:
            return nonce + b"\x00\x00\x00\x01"
        # SP 800-38D allows other nonce lengths; supported so the NIST vectors
        # covering them can be run, though PDMarks always uses 96 bits.
        padded = _pad16(nonce) + b"\x00" * 8 + (len(nonce) * 8).to_bytes(8, "big")
        return self._gh.digest_int(padded).to_bytes(16, "big")

    def _tag(self, j0: bytes, aad: bytes, ct: bytes) -> bytes:
        lens = (len(aad) * 8).to_bytes(8, "big") + (len(ct) * 8).to_bytes(8, "big")
        s = self._gh.digest_int(_pad16(aad) + _pad16(ct) + lens)
        return _gctr(self._rk, j0, s.to_bytes(16, "big"))[:TAG_SIZE]

    @staticmethod
    def _check_nonce(nonce: bytes) -> bytes:
        if not isinstance(nonce, (bytes, bytearray, memoryview)):
            raise TypeError("nonce must be bytes")
        nonce = bytes(nonce)
        if len(nonce) == 0:
            raise ValueError("nonce must not be empty")
        return nonce

    # -- public API ---------------------------------------------------------

    def encrypt(self, nonce: bytes, data: bytes,
                associated_data: Optional[bytes]) -> bytes:
        """Return ``ciphertext || tag``."""
        nonce = self._check_nonce(nonce)
        data = bytes(data)
        aad = b"" if associated_data is None else bytes(associated_data)
        j0 = self._j0(nonce)
        ct = _gctr(self._rk, _inc32(j0), data)
        return ct + self._tag(j0, aad, ct)

    def decrypt(self, nonce: bytes, data: bytes,
                associated_data: Optional[bytes]) -> bytes:
        """Authenticate and decrypt ``ciphertext || tag``; raise :class:`InvalidTag`."""
        nonce = self._check_nonce(nonce)
        data = bytes(data)
        if len(data) < TAG_SIZE:
            raise InvalidTag("ciphertext shorter than the GCM tag")
        aad = b"" if associated_data is None else bytes(associated_data)
        ct, tag = data[:-TAG_SIZE], data[-TAG_SIZE:]
        j0 = self._j0(nonce)
        expected = self._tag(j0, aad, ct)
        # Verify before releasing any plaintext (SP 800-38D section 7.2 step 6).
        if not _hmac.compare_digest(expected, tag):
            raise InvalidTag("GCM authentication failed")
        return _gctr(self._rk, _inc32(j0), ct)
