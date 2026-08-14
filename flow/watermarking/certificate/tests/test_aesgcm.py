# SPDX-License-Identifier: BSD-3-Clause
"""Known-answer tests for the vendored AES-GCM (``wm_aesgcm``).

Everything here is stdlib-only, so it runs on a bare host with no OpenROAD, no
numpy and no ``cryptography``.  When ``cryptography`` *is* importable the
cross-backend equivalence test also runs; otherwise it skips.

Vector sources
--------------
* AES block cipher: FIPS-197 Appendix C.1 / C.2 / C.3.
* AES-GCM: the canonical test cases from McGrew & Viega, "The Galois/Counter
  Mode of Operation (GCM)", which are the same vectors NIST publishes in its
  CAVP ``gcmEncryptExtIV`` / ``gcmDecrypt`` sets.  Cases 1-4 are AES-128 and
  13-16 are AES-256; between them they cover empty plaintext with empty AAD, a
  single-block plaintext, a multi-block plaintext exercising the counter
  increment, and a plaintext whose length is not a multiple of 16 combined with
  a 20-byte (also not a multiple of 16) AAD.
"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import wm_aesgcm  # noqa: E402
from wm_aesgcm import AESGCM, InvalidTag  # noqa: E402


def _h(s):
    return bytes.fromhex(s)


# ---------------------------------------------------------------------------
# FIPS-197 block-cipher vectors
# ---------------------------------------------------------------------------

FIPS197 = [
    # (label, key, plaintext, ciphertext)
    ("C.1 AES-128",
     "000102030405060708090a0b0c0d0e0f",
     "00112233445566778899aabbccddeeff",
     "69c4e0d86a7b0430d8cdb78070b4c55a"),
    ("C.2 AES-192",
     "000102030405060708090a0b0c0d0e0f1011121314151617",
     "00112233445566778899aabbccddeeff",
     "dda97ca4864cdfe06eaf70a0ec0d7191"),
    ("C.3 AES-256",
     "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f",
     "00112233445566778899aabbccddeeff",
     "8ea2b7ca516745bfeafc49904b496089"),
]


class TestAESBlock(unittest.TestCase):
    def test_fips197_vectors(self):
        for label, key, pt, ct in FIPS197:
            with self.subTest(label):
                rk = wm_aesgcm._expand_key(_h(key))
                self.assertEqual(wm_aesgcm._encrypt_block(rk, _h(pt)), _h(ct))

    def test_round_key_count(self):
        for key_len, rounds in ((16, 11), (24, 13), (32, 15)):
            with self.subTest(key_len=key_len):
                rk = wm_aesgcm._expand_key(b"\x00" * key_len)
                self.assertEqual(len(rk), rounds)
                self.assertTrue(all(len(k) == 16 for k in rk))

    def test_rejects_bad_key_length(self):
        for bad in (0, 15, 17, 31, 33, 64):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    wm_aesgcm._expand_key(b"\x00" * bad)


# ---------------------------------------------------------------------------
# GHASH: table-driven vs. bit-serial oracle
# ---------------------------------------------------------------------------

class TestGHash(unittest.TestCase):
    def test_table_matches_bit_serial(self):
        rng = random.Random(20260812)
        for _ in range(200):
            h = rng.getrandbits(128)
            x = rng.getrandbits(128)
            gh = wm_aesgcm._GHash(h.to_bytes(16, "big"))
            self.assertEqual(gh.mul_h(x), wm_aesgcm._gmul_slow(x, h))

    def test_multiplicative_identity(self):
        # H * 1 == H, where "1" is x^0, i.e. the top bit under GCM's ordering.
        h = 0x66E94BD4EF8A2C3B884CFA59CA342B2E  # E_K(0) for the all-zero AES-128 key
        gh = wm_aesgcm._GHash(h.to_bytes(16, "big"))
        self.assertEqual(gh.mul_h(1 << 127), h)

    def test_zero_absorbs(self):
        gh = wm_aesgcm._GHash(os.urandom(16))
        self.assertEqual(gh.mul_h(0), 0)


class TestInc32(unittest.TestCase):
    def test_increment(self):
        self.assertEqual(
            wm_aesgcm._inc32(_h("00" * 12 + "00000001")),
            _h("00" * 12 + "00000002"))

    def test_wraps_without_touching_the_nonce(self):
        # The 32-bit counter wraps modulo 2^32; the leading 96 bits are fixed.
        block = _h("cafebabefacedbaddecaf888" + "ffffffff")
        self.assertEqual(wm_aesgcm._inc32(block),
                         _h("cafebabefacedbaddecaf888" + "00000000"))


# ---------------------------------------------------------------------------
# GCM known-answer tests
# ---------------------------------------------------------------------------

P60 = ("d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a72"
       "1c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657ba637b39")
P64 = P60 + "1aafd255"
A20 = "feedfacedeadbeeffeedfacedeadbeefabaddad2"

GCM_VECTORS = [
    # (label, key, iv, plaintext, aad, ciphertext, tag)
    ("TC1 AES-128 empty/empty",
     "00000000000000000000000000000000", "000000000000000000000000",
     "", "", "", "58e2fccefa7e3061367f1d57a4e7455a"),
    ("TC2 AES-128 one block",
     "00000000000000000000000000000000", "000000000000000000000000",
     "00000000000000000000000000000000", "",
     "0388dace60b6a392f328c2b971b2fe78", "ab6e47d42cec13bdf53a67b21257bddf"),
    ("TC3 AES-128 four blocks",
     "feffe9928665731c6d6a8f9467308308", "cafebabefacedbaddecaf888",
     P64, "",
     "42831ec2217774244b7221b784d0d49ce3aa212f2c02a4e035c17e2329aca12e"
     "21d514b25466931c7d8f6a5aac84aa051ba30b396a0aac973d58e091473f5985",
     "4d5c2af327cd64a62cf35abd2ba6fab4"),
    ("TC4 AES-128 60-byte pt, 20-byte aad",
     "feffe9928665731c6d6a8f9467308308", "cafebabefacedbaddecaf888",
     P60, A20,
     "42831ec2217774244b7221b784d0d49ce3aa212f2c02a4e035c17e2329aca12e"
     "21d514b25466931c7d8f6a5aac84aa051ba30b396a0aac973d58e091",
     "5bc94fbc3221a5db94fae95ae7121a47"),
    ("TC13 AES-256 empty/empty",
     "00" * 32, "000000000000000000000000",
     "", "", "", "530f8afbc74536b9a963b4f1c4cb738b"),
    ("TC14 AES-256 one block",
     "00" * 32, "000000000000000000000000",
     "00000000000000000000000000000000", "",
     "cea7403d4d606b6e074ec5d3baf39d18", "d0d1c8a799996bf0265b98b5d48ab919"),
    ("TC15 AES-256 four blocks",
     "feffe9928665731c6d6a8f9467308308feffe9928665731c6d6a8f9467308308",
     "cafebabefacedbaddecaf888",
     P64, "",
     "522dc1f099567d07f47f37a32a84427d643a8cdcbfe5c0c97598a2bd2555d1aa"
     "8cb08e48590dbb3da7b08b1056828838c5f61e6393ba7a0abcc9f662898015ad",
     "b094dac5d93471bdec1a502270e3cc6c"),
    ("TC16 AES-256 60-byte pt, 20-byte aad",
     "feffe9928665731c6d6a8f9467308308feffe9928665731c6d6a8f9467308308",
     "cafebabefacedbaddecaf888",
     P60, A20,
     "522dc1f099567d07f47f37a32a84427d643a8cdcbfe5c0c97598a2bd2555d1aa"
     "8cb08e48590dbb3da7b08b1056828838c5f61e6393ba7a0abcc9f662",
     "76fc6ece0f4e1768cddf8853bb2d551b"),
]


class TestGCMKnownAnswers(unittest.TestCase):
    def test_encrypt(self):
        for label, key, iv, pt, aad, ct, tag in GCM_VECTORS:
            with self.subTest(label):
                got = AESGCM(_h(key)).encrypt(_h(iv), _h(pt), _h(aad))
                self.assertEqual(got, _h(ct) + _h(tag))

    def test_decrypt(self):
        for label, key, iv, pt, aad, ct, tag in GCM_VECTORS:
            with self.subTest(label):
                got = AESGCM(_h(key)).decrypt(_h(iv), _h(ct) + _h(tag), _h(aad))
                self.assertEqual(got, _h(pt))

    def test_decrypt_rejects_tampering(self):
        """Every vector must FAIL to decrypt once a single bit is flipped."""
        for label, key, iv, pt, aad, ct, tag in GCM_VECTORS:
            blob = bytearray(_h(ct) + _h(tag))
            for pos in ({0, len(blob) - 1} if ct else {len(blob) - 1}):
                with self.subTest(label=label, pos=pos):
                    bad = bytearray(blob)
                    bad[pos] ^= 0x01
                    with self.assertRaises(InvalidTag):
                        AESGCM(_h(key)).decrypt(_h(iv), bytes(bad), _h(aad))

    def test_decrypt_rejects_wrong_aad(self):
        _, key, iv, pt, aad, ct, tag = GCM_VECTORS[-1]
        with self.assertRaises(InvalidTag):
            AESGCM(_h(key)).decrypt(_h(iv), _h(ct) + _h(tag), _h(aad) + b"\x00")

    def test_decrypt_rejects_wrong_key(self):
        _, key, iv, pt, aad, ct, tag = GCM_VECTORS[-1]
        other = bytearray(_h(key))
        other[0] ^= 0xFF
        with self.assertRaises(InvalidTag):
            AESGCM(bytes(other)).decrypt(_h(iv), _h(ct) + _h(tag), _h(aad))

    def test_decrypt_rejects_short_input(self):
        with self.assertRaises(InvalidTag):
            AESGCM(b"\x00" * 32).decrypt(b"\x00" * 12, b"short", b"")


class TestGCMRoundTrip(unittest.TestCase):
    """Shapes the published vectors do not cover, plus general round-tripping.

    Notably: non-empty AAD with empty plaintext.  There is no canonical KAT for
    that combination in the GCM paper, so it is covered here by round-trip and,
    when ``cryptography`` is installed, by the cross-backend test below.
    """

    def test_shapes(self):
        rng = random.Random(4242)
        key = bytes(rng.getrandbits(8) for _ in range(32))
        aead = AESGCM(key)
        for pt_len in (0, 1, 15, 16, 17, 31, 32, 33, 1000):
            for aad_len in (0, 1, 15, 16, 17, 20, 33):
                with self.subTest(pt=pt_len, aad=aad_len):
                    nonce = bytes(rng.getrandbits(8) for _ in range(12))
                    pt = bytes(rng.getrandbits(8) for _ in range(pt_len))
                    aad = bytes(rng.getrandbits(8) for _ in range(aad_len))
                    blob = aead.encrypt(nonce, pt, aad)
                    self.assertEqual(len(blob), pt_len + 16)
                    self.assertEqual(aead.decrypt(nonce, blob, aad), pt)

    def test_none_aad_equals_empty_aad(self):
        aead = AESGCM(b"\x11" * 32)
        nonce = b"\x22" * 12
        self.assertEqual(aead.encrypt(nonce, b"hello", None),
                         aead.encrypt(nonce, b"hello", b""))

    def test_nonce_reuse_is_deterministic(self):
        # Not a security property -- just pinning determinism, which the
        # certificate KAT in test_cert.py depends on.
        aead = AESGCM(b"\x33" * 32)
        a = aead.encrypt(b"\x44" * 12, b"payload", b"aad")
        b = aead.encrypt(b"\x44" * 12, b"payload", b"aad")
        self.assertEqual(a, b)

    def test_long_nonce_supported(self):
        aead = AESGCM(b"\x55" * 32)
        for nonce_len in (1, 8, 13, 16, 32):
            with self.subTest(nonce_len=nonce_len):
                nonce = bytes(range(nonce_len))
                blob = aead.encrypt(nonce, b"abc", b"")
                self.assertEqual(aead.decrypt(nonce, blob, b""), b"abc")

    def test_empty_nonce_rejected(self):
        with self.assertRaises(ValueError):
            AESGCM(b"\x66" * 32).encrypt(b"", b"x", b"")


def _have_cryptography():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        return True
    except Exception:
        return False


@unittest.skipUnless(_have_cryptography(),
                     "cryptography not installed; vendored backend is the only one")
class TestBackendEquivalence(unittest.TestCase):
    """The vendored backend must agree with OpenSSL byte for byte."""

    def test_randomized(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM as RefAESGCM
        from cryptography.exceptions import InvalidTag as RefInvalidTag

        rng = random.Random(1)
        lengths = (0, 1, 15, 16, 17, 31, 32, 33, 1000)
        for i in range(200):
            key = bytes(rng.getrandbits(8) for _ in range(32))
            nonce = bytes(rng.getrandbits(8) for _ in range(12))
            pt = bytes(rng.getrandbits(8) for _ in range(rng.choice(lengths)))
            aad = bytes(rng.getrandbits(8) for _ in range(rng.choice(lengths)))
            with self.subTest(i=i):
                ours = AESGCM(key).encrypt(nonce, pt, aad)
                theirs = RefAESGCM(key).encrypt(nonce, pt, aad)
                self.assertEqual(ours, theirs)
                self.assertEqual(AESGCM(key).decrypt(nonce, theirs, aad), pt)
                self.assertEqual(RefAESGCM(key).decrypt(nonce, ours, aad), pt)

        # Both backends must reject the same tampered blob.
        key = b"\x77" * 32
        nonce = b"\x88" * 12
        blob = bytearray(AESGCM(key).encrypt(nonce, b"data", b"aad"))
        blob[-1] ^= 1
        with self.assertRaises(InvalidTag):
            AESGCM(key).decrypt(nonce, bytes(blob), b"aad")
        with self.assertRaises(RefInvalidTag):
            RefAESGCM(key).decrypt(nonce, bytes(blob), b"aad")


if __name__ == "__main__":
    unittest.main()
