# SPDX-License-Identifier: BSD-3-Clause
"""Tests for ``wm_cert``: Gamma serialization, seal/open, commitment, RFC 3161.

Stdlib-only.  The openssl cross-checks skip when ``openssl`` is absent; every
seal/open test runs twice, once forced onto the vendored AES backend, so the
suite exercises the code path the OpenROAD-side verifiers will actually take.
"""

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import wm_cert as wc  # noqa: E402
from wm_cert import CtsClaim, PlacementClaim  # noqa: E402


KAT_KEY = bytes(range(32))
KAT_ID_D0 = hashlib.sha256(b"pdmarks-kat-id-d0").digest()
KAT_NU = bytes.fromhex("0102030405060708090a0b0c")
KAT_META = {"design": "kat", "platform": "nangate45",
            "routing": {"f": 0.02, "lambda_wm": 100.0}}
KAT_PLACEMENT = [
    PlacementClaim("pair", "cellA", "cellB", "", "1", ""),
    PlacementClaim("triple", "x", "y", "z", "", "4"),
]
KAT_CTS = [
    CtsClaim("lcb1", "lcb2", "lcb1+lcb2", "lcb1", "lcb2", "pure", "0", "", "", "0"),
]


@contextlib.contextmanager
def pure_backend(force):
    """Temporarily pin (or unpin) the vendored AES-GCM backend."""
    old = os.environ.get("WM_CERT_FORCE_PURE_AES")
    if force:
        os.environ["WM_CERT_FORCE_PURE_AES"] = "1"
    else:
        os.environ.pop("WM_CERT_FORCE_PURE_AES", None)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("WM_CERT_FORCE_PURE_AES", None)
        else:
            os.environ["WM_CERT_FORCE_PURE_AES"] = old


BACKENDS = [("vendored", True)] + ([("cryptography", False)]
                                   if wc.have_cryptography() else [])


def _seal(**kw):
    params = dict(id_d0_bytes=KAT_ID_D0, nu=KAT_NU, meta=KAT_META,
                  placement=KAT_PLACEMENT, cts=KAT_CTS)
    params.update(kw)
    return wc.seal_certificate(KAT_KEY, **params)


# ---------------------------------------------------------------------------
# T3 -- Gamma serialization
# ---------------------------------------------------------------------------

class TestGammaSerialization(unittest.TestCase):
    def test_round_trip(self):
        blob = wc.serialize_gamma(KAT_META, KAT_PLACEMENT, KAT_CTS)
        g = wc.parse_gamma(blob)
        self.assertEqual(g.meta, KAT_META)
        self.assertEqual(list(g.placement), KAT_PLACEMENT)
        self.assertEqual(list(g.cts), KAT_CTS)

    def test_round_trip_is_stable(self):
        blob = wc.serialize_gamma(KAT_META, KAT_PLACEMENT, KAT_CTS)
        g = wc.parse_gamma(blob)
        self.assertEqual(
            wc.serialize_gamma(g.meta, g.placement, g.cts), blob)

    def test_empty_claim_sets(self):
        g = wc.parse_gamma(wc.serialize_gamma({}, [], []))
        self.assertEqual(g.placement, ())
        self.assertEqual(g.cts, ())

    def test_length_prefixing_removes_ambiguity(self):
        """The whole reason the encoding is length-prefixed.

        A naive separator-joined encoding would render ("ab","c") and
        ("a","bc") identically, letting one certified tuple masquerade as
        another.  These must differ.
        """
        a = wc.serialize_gamma({}, [PlacementClaim("pair", "ab", "c", "", "1", "")], [])
        b = wc.serialize_gamma({}, [PlacementClaim("pair", "a", "bc", "", "1", "")], [])
        self.assertNotEqual(a, b)

        # Same test where the shifting boundary is inside the CTS identifier.
        c = wc.serialize_gamma({}, [], [
            CtsClaim("lcb", "1+x", "", "", "", "pure", "0", "", "", "0")])
        d = wc.serialize_gamma({}, [], [
            CtsClaim("lcb1", "+x", "", "", "", "pure", "0", "", "", "0")])
        self.assertNotEqual(c, d)

    def test_hostile_names_survive_exactly(self):
        """Names that would need quoting in CSV must round-trip untouched."""
        nasty = [
            PlacementClaim("pair", 'a,b', 'c"d', "", "0", ""),
            PlacementClaim("pair", "line\nbreak", "tab\tsep", "", "1", ""),
            PlacementClaim("triple", "unicodeé中", "sp ace", "semi;colon", "", "5"),
        ]
        g = wc.parse_gamma(wc.serialize_gamma({}, nasty, []))
        self.assertEqual(list(g.placement), nasty)

    def test_empty_vs_absent_repair_fanout_is_preserved(self):
        """'' and '0' must stay distinct: they gate the CTS tamper check."""
        claims = [
            CtsClaim("a", "b", "a+b", "a", "b", "quasi_leaf", "1", "", "", "0"),
            CtsClaim("c", "d", "c+d", "c", "d", "quasi_leaf", "1", "0", "0", "1"),
        ]
        g = wc.parse_gamma(wc.serialize_gamma({}, [], claims))
        self.assertEqual(g.cts[0].repair_fanout_target, "")
        self.assertEqual(g.cts[1].repair_fanout_target, "0")

    def test_rejects_bad_magic(self):
        blob = bytearray(wc.serialize_gamma({}, [], []))
        blob[4] ^= 0xFF
        with self.assertRaises(wc.CertificateFormatError):
            wc.parse_gamma(bytes(blob))

    def test_rejects_truncation(self):
        blob = wc.serialize_gamma(KAT_META, KAT_PLACEMENT, KAT_CTS)
        for cut in (1, len(blob) // 2, len(blob) - 1):
            with self.subTest(cut=cut):
                with self.assertRaises(wc.CertificateFormatError):
                    wc.parse_gamma(blob[:cut])

    def test_rejects_trailing_bytes(self):
        blob = wc.serialize_gamma(KAT_META, KAT_PLACEMENT, KAT_CTS)
        with self.assertRaises(wc.CertificateFormatError):
            wc.parse_gamma(blob + b"\x00")


# ---------------------------------------------------------------------------
# T4 -- seal / open, and the negative controls
# ---------------------------------------------------------------------------

class TestSealOpen(unittest.TestCase):
    def test_round_trip_each_backend(self):
        for name, force in BACKENDS:
            with self.subTest(backend=name), pure_backend(force):
                cert = wc.open_certificate(KAT_KEY, _seal())
                self.assertEqual(list(cert.gamma.placement), KAT_PLACEMENT)
                self.assertEqual(list(cert.gamma.cts), KAT_CTS)
                self.assertEqual(cert.header.id_d0, KAT_ID_D0)
                self.assertEqual(cert.header.nu, KAT_NU)

    def test_backends_agree_byte_for_byte(self):
        if len(BACKENDS) < 2:
            self.skipTest("only one AEAD backend available")
        with pure_backend(True):
            a = _seal()
        with pure_backend(False):
            b = _seal()
        self.assertEqual(a, b)

    def test_meta_carries_version_and_suite(self):
        cert = wc.open_certificate(KAT_KEY, _seal())
        self.assertEqual(cert.gamma.meta["cert_version"], wc.CERT_VERSION)
        self.assertEqual(cert.gamma.meta["suite"], wc.CERT_SUITE)
        # ...without mutating the caller's dict.
        self.assertNotIn("cert_version", KAT_META)

    def test_wrong_key_fails_authentication(self):
        wrong = bytes(32)
        for name, force in BACKENDS:
            with self.subTest(backend=name), pure_backend(force):
                with self.assertRaises(wc.CertificateAuthError):
                    wc.open_certificate(wrong, _seal())

    def test_tampered_ciphertext_fails(self):
        """Flip one bit in each of the four mutable regions.

        The nu and id_d0 cases live in the header, which is *outside* the AEAD
        associated data on the wire -- but the AAD is rebuilt from the header at
        open time, so altering either yields a different AAD and the tag check
        fails.  Testing them here (rather than only through the commitment)
        is what proves the AEAD actually binds them.
        """
        blob = _seal()
        regions = {
            "ciphertext": wc.CERT_HEADER_LEN + 1,
            "tag": len(blob) - 1,
            "nu": 44,
            "id_d0": 12,
        }
        for name, force in BACKENDS:
            for region, offset in regions.items():
                with self.subTest(backend=name, region=region), pure_backend(force):
                    bad = bytearray(blob)
                    bad[offset] ^= 0x01
                    with self.assertRaises(wc.CertificateAuthError):
                        wc.open_certificate(KAT_KEY, bytes(bad))

    def test_rejects_bad_magic(self):
        bad = bytearray(_seal())
        bad[0] ^= 0xFF
        with self.assertRaises(wc.CertificateFormatError):
            wc.parse_certificate_header(bytes(bad))

    def test_rejects_unknown_version_and_suite(self):
        for offset, label in ((9, "version"), (10, "suite")):
            with self.subTest(label):
                bad = bytearray(_seal())
                bad[offset] = 0x7F
                with self.assertRaises(wc.CertificateFormatError):
                    wc.parse_certificate_header(bytes(bad))

    def test_rejects_truncated_file(self):
        blob = _seal()
        for cut in (0, 10, wc.CERT_HEADER_LEN, len(blob) - 1):
            with self.subTest(cut=cut):
                with self.assertRaises(wc.CertificateFormatError):
                    wc.parse_certificate_header(blob[:cut])

    def test_rejects_inconsistent_ct_len(self):
        import struct
        bad = bytearray(_seal())
        struct.pack_into(">I", bad, 56, 999999)
        with self.assertRaises(wc.CertificateFormatError):
            wc.parse_certificate_header(bytes(bad))

    def test_cert_sha256_is_the_bytes_after_the_header(self):
        blob = _seal()
        self.assertEqual(
            wc.cert_sha256(blob),
            hashlib.sha256(blob[wc.CERT_HEADER_LEN:]).digest())


# ---------------------------------------------------------------------------
# Commitment (Eq. 19) and the record
# ---------------------------------------------------------------------------

class TestCommitment(unittest.TestCase):
    def test_accepts_the_right_key(self):
        blob = _seal()
        cs = wc.cert_sha256(blob)
        c = wc.commitment(KAT_KEY, KAT_ID_D0, KAT_NU, cs)
        self.assertTrue(wc.check_commitment(KAT_KEY, KAT_ID_D0, KAT_NU, cs, c))

    def test_rejects_every_perturbed_input(self):
        blob = _seal()
        cs = wc.cert_sha256(blob)
        c = wc.commitment(KAT_KEY, KAT_ID_D0, KAT_NU, cs)
        other32 = bytes(32)
        other12 = bytes(12)
        cases = {
            "key": (other32, KAT_ID_D0, KAT_NU, cs),
            "id_d0": (KAT_KEY, other32, KAT_NU, cs),
            "nu": (KAT_KEY, KAT_ID_D0, other12, cs),
            "cert_sha256": (KAT_KEY, KAT_ID_D0, KAT_NU, other32),
        }
        for label, args in cases.items():
            with self.subTest(label):
                self.assertFalse(wc.check_commitment(*args, c))

    def test_rejects_wrong_field_lengths(self):
        with self.assertRaises(ValueError):
            wc.commitment(b"short", KAT_ID_D0, KAT_NU, bytes(32))
        with self.assertRaises(ValueError):
            wc.derive_cert_key(KAT_KEY, KAT_ID_D0, b"tooshort")

    def test_record_is_length_prefixed(self):
        cs, c = bytes(32), bytes(32)
        r = wc.record_bytes(KAT_ID_D0, KAT_NU, cs, c)
        self.assertEqual(len(r), 4 * 4 + 32 + 12 + 32 + 32)
        self.assertEqual(wc.record_sha256(KAT_ID_D0, KAT_NU, cs, c),
                         hashlib.sha256(r).digest())

    def test_cert_key_is_domain_separated_from_stage_seeds(self):
        """K_Gamma must not collide with any stage seed derived from the same K."""
        from wm_prf import derive_stage_seeds
        k_gamma = wc.derive_cert_key(KAT_KEY, KAT_ID_D0, KAT_NU)
        seeds = set(derive_stage_seeds(KAT_KEY).values())
        self.assertNotIn(k_gamma, seeds)
        self.assertEqual(len(k_gamma), 32)


class TestLoadMasterSeed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_hex_literal(self):
        self.assertEqual(wc.load_master_seed(KAT_KEY.hex()), KAT_KEY)

    def test_hex_file_with_trailing_newline(self):
        p = os.path.join(self.tmp, "master_seed.hex")
        with open(p, "w") as f:
            f.write(KAT_KEY.hex() + "\n")
        self.assertEqual(wc.load_master_seed(p), KAT_KEY)

    def test_bundle_json(self):
        p = os.path.join(self.tmp, "bundle.json")
        with open(p, "w") as f:
            json.dump({"master_seed_hex": KAT_KEY.hex(), "M": {}}, f)
        self.assertEqual(wc.load_master_seed(p), KAT_KEY)

    def test_rejects_wrong_length(self):
        with self.assertRaises(ValueError):
            wc.load_master_seed("00" * 31)

    def test_rejects_garbage(self):
        for bad in ("", "not-hex-and-not-a-path"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    wc.load_master_seed(bad)


# ---------------------------------------------------------------------------
# T5 -- frozen wire-format KAT
# ---------------------------------------------------------------------------

class TestFrozenWireFormat(unittest.TestCase):
    """Pins the on-disk format.

    Without this, a refactor could silently change ID(D0), the AAD framing or
    the commitment preimage while every round-trip test still passed -- and
    certificates issued before the change would stop verifying.
    """

    CERT_SHA256 = "16f936b0ee429d22fc7edc02a4ce9db657aa607693876366dfd2a293f5436781"
    COMMITMENT = "190275bf651d13201b61d2034461d2909d042e59370a665c34401eea09e72eed"
    RECORD_SHA256 = "4973a9f8420eef39e098a740d297b6fac977ea07464cef8f7375b1b9359d5b0b"
    TSQ = ("30390201013031300d060960864801650304020105000420"
           "4973a9f8420eef39e098a740d297b6fac977ea07464cef8f7375b1b9359d5b0b"
           "0101ff")
    CERT_LEN = 391

    def test_frozen(self):
        for name, force in BACKENDS:
            with self.subTest(backend=name), pure_backend(force):
                blob = _seal()
                self.assertEqual(len(blob), self.CERT_LEN)
                cs = wc.cert_sha256(blob)
                self.assertEqual(cs.hex(), self.CERT_SHA256)
                c = wc.commitment(KAT_KEY, KAT_ID_D0, KAT_NU, cs)
                self.assertEqual(c.hex(), self.COMMITMENT)
                rs = wc.record_sha256(KAT_ID_D0, KAT_NU, cs, c)
                self.assertEqual(rs.hex(), self.RECORD_SHA256)
                tsq = wc.build_timestamp_request(rs, nonce=None, cert_req=True)
                self.assertEqual(tsq.hex(), self.TSQ)

    def test_header_layout(self):
        blob = _seal()
        self.assertEqual(blob[:8], wc.CERT_MAGIC)
        self.assertEqual(blob[8:10], b"\x00\x01")     # version 1, big-endian
        self.assertEqual(blob[10], wc.CERT_SUITE)
        self.assertEqual(blob[11], 0)                 # reserved
        self.assertEqual(blob[12:44], KAT_ID_D0)
        self.assertEqual(blob[44:56], KAT_NU)


# ---------------------------------------------------------------------------
# ID(D_0)
# ---------------------------------------------------------------------------

class TestIdD0(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.results = os.path.join(self.tmp, "results", "nangate45", "jpeg", "base")
        os.makedirs(self.results)
        os.makedirs(os.path.join(self.tmp, "designs", "nangate45", "jpeg"))
        with open(os.path.join(self.tmp, "designs", "nangate45", "jpeg",
                               "config.mk"), "w") as f:
            f.write("export DESIGN_NAME = jpeg_encoder\n")

    def _write(self, name, content):
        with open(os.path.join(self.results, name), "w") as f:
            f.write(content)

    def _preimage(self, **kw):
        params = dict(design="jpeg", design_nickname="jpeg", platform="nangate45",
                      ref_flow_variant="base", flow_home=self.tmp,
                      wm_config={"WM_PAIR_DIST_UM": "2.0"},
                      tool={"pdmarks_commit": "abc"})
        params.update(kw)
        return wc.build_id_d0_preimage(**params)

    def test_partial_results_dir_is_well_defined(self):
        self._write("1_2_yosys.v", "module top(); endmodule\n")
        self._write("clock_period.txt", "1.0\n")
        pre = self._preimage()
        names = [a["name"] for a in pre["artifacts"]]
        self.assertEqual(names, ["1_2_yosys.v", "clock_period.txt"])
        # Everything absent is named explicitly rather than silently skipped.
        self.assertIn("3_place.odb", pre["missing"])
        self.assertEqual(pre["missing"], sorted(pre["missing"]))

    def test_deterministic(self):
        self._write("1_2_yosys.v", "x")
        self.assertEqual(wc.id_d0(self._preimage()), wc.id_d0(self._preimage()))

    def test_changes_when_an_artifact_changes(self):
        self._write("1_2_yosys.v", "version A")
        before = wc.id_d0(self._preimage())
        self._write("1_2_yosys.v", "version B")
        self.assertNotEqual(wc.id_d0(self._preimage()), before)

    def test_changes_when_wm_config_changes(self):
        self._write("1_2_yosys.v", "x")
        before = wc.id_d0(self._preimage())
        after = wc.id_d0(self._preimage(wm_config={"WM_PAIR_DIST_UM": "3.0"}))
        self.assertNotEqual(after, before)

    def test_profiles_are_not_conflatable(self):
        """A light preimage must never collide with a full one."""
        self._write("1_2_yosys.v", "x")
        self._write("clock_period.txt", "1.0")
        full = self._preimage(profile="full")
        light = self._preimage(profile="light")
        self.assertNotEqual(wc.id_d0(full), wc.id_d0(light))
        self.assertEqual(light["artifact_profile"], "light")

    def test_light_profile_skips_the_big_odbs(self):
        for name in wc.ID_D0_ARTIFACTS_FULL:
            self._write(name, name)
        light = self._preimage(profile="light")
        self.assertEqual([a["name"] for a in light["artifacts"]],
                         list(wc.ID_D0_ARTIFACTS_LIGHT))
        self.assertNotIn("3_place.odb", [a["name"] for a in light["artifacts"]])

    def test_unknown_profile_rejected(self):
        with self.assertRaises(ValueError):
            self._preimage(profile="medium")

    def test_config_mk_hashed_when_present(self):
        pre = self._preimage()
        self.assertIsNotNone(pre["config_mk"]["sha256"])
        self.assertEqual(pre["config_mk"]["path"],
                         os.path.join("designs", "nangate45", "jpeg", "config.mk"))

    def test_missing_config_mk_is_null_not_an_error(self):
        pre = self._preimage(design="nonexistent")
        self.assertIsNone(pre["config_mk"]["sha256"])

    def test_sha256_file_matches_hashlib(self):
        self._write("1_2_yosys.v", "abc" * 1000)
        path = os.path.join(self.results, "1_2_yosys.v")
        with open(path, "rb") as f:
            expected = hashlib.sha256(f.read()).hexdigest()
        digest, size = wc.sha256_file(path, chunk=7)   # tiny chunk: force looping
        self.assertEqual(digest, expected)
        self.assertEqual(size, 3000)


# ---------------------------------------------------------------------------
# T6 -- RFC 3161 DER
# ---------------------------------------------------------------------------

def _openssl():
    return shutil.which("openssl")


class TestDER(unittest.TestCase):
    def test_der_len_short_and_long_form(self):
        self.assertEqual(wc.der_len(0), b"\x00")
        self.assertEqual(wc.der_len(127), b"\x7f")
        self.assertEqual(wc.der_len(128), b"\x81\x80")
        self.assertEqual(wc.der_len(255), b"\x81\xff")
        self.assertEqual(wc.der_len(256), b"\x82\x01\x00")
        self.assertEqual(wc.der_len(65535), b"\x82\xff\xff")

    def test_der_int_minimal_and_sign_safe(self):
        self.assertEqual(wc.der_int(0), b"\x02\x01\x00")
        self.assertEqual(wc.der_int(1), b"\x02\x01\x01")
        self.assertEqual(wc.der_int(127), b"\x02\x01\x7f")
        # Top bit set: a leading 0x00 is mandatory or it reads back negative.
        self.assertEqual(wc.der_int(128), b"\x02\x02\x00\x80")
        self.assertEqual(wc.der_int(255), b"\x02\x02\x00\xff")
        self.assertEqual(wc.der_int(256), b"\x02\x02\x01\x00")

    def test_der_int_rejects_negative(self):
        with self.assertRaises(ValueError):
            wc.der_int(-1)

    def test_der_oid_sha256(self):
        self.assertEqual(wc.der_oid(wc.OID_SHA256),
                         bytes.fromhex("0609608648016503040201"))

    def test_der_oid_multibyte_arc(self):
        # 1.2.840.113549 -- 113549 needs three base-128 groups.
        self.assertEqual(wc.der_oid("1.2.840.113549"),
                         bytes.fromhex("06062a864886f70d"))

    def test_der_oid_rejects_malformed(self):
        for bad in ("1", "3.0.1", "0.40.1"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    wc.der_oid(bad)

    def test_request_rejects_wrong_digest_length(self):
        with self.assertRaises(ValueError):
            wc.build_timestamp_request(b"\x00" * 20)

    def test_cert_req_omitted_when_false(self):
        """DER forbids encoding a DEFAULT FALSE boolean when it is false."""
        digest = bytes(32)
        with_flag = wc.build_timestamp_request(digest, nonce=None, cert_req=True)
        without = wc.build_timestamp_request(digest, nonce=None, cert_req=False)
        self.assertTrue(with_flag.endswith(b"\x01\x01\xff"))
        self.assertFalse(without.endswith(b"\x01\x01\xff"))
        self.assertEqual(len(with_flag), len(without) + 3)

    @unittest.skipUnless(_openssl(), "openssl not on PATH")
    def test_parses_as_valid_asn1(self):
        digest = hashlib.sha256(b"parse-me").digest()
        for nonce, cert_req in ((None, True), (None, False),
                                (0x0102030405060708, True),
                                (0x8000000000000000, True)):
            with self.subTest(nonce=nonce, cert_req=cert_req):
                req = wc.build_timestamp_request(digest, nonce=nonce,
                                                 cert_req=cert_req)
                proc = subprocess.run(
                    [_openssl(), "asn1parse", "-inform", "DER"],
                    input=req, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                self.assertEqual(proc.returncode, 0,
                                 proc.stdout.decode("utf-8", "replace"))

    @unittest.skipUnless(_openssl(), "openssl not on PATH")
    def test_byte_identical_to_openssl_ts_query(self):
        """The definitive check on the hand-rolled DER encoder.

        ``-no_nonce`` removes the only nondeterministic field, so OpenSSL's
        output is a fixed target we can compare against exactly.
        """
        for payload in (b"", b"pdmarks", b"\xff" * 64):
            digest = hashlib.sha256(payload).digest()
            with self.subTest(payload=payload[:8]):
                with tempfile.NamedTemporaryFile(suffix=".tsq") as out:
                    proc = subprocess.run(
                        [_openssl(), "ts", "-query", "-digest", digest.hex(),
                         "-sha256", "-cert", "-no_nonce", "-out", out.name],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                    self.assertEqual(proc.returncode, 0,
                                     proc.stdout.decode("utf-8", "replace"))
                    with open(out.name, "rb") as f:
                        reference = f.read()
                ours = wc.build_timestamp_request(digest, nonce=None,
                                                  cert_req=True)
                self.assertEqual(ours, reference)


class TestTimestampResponse(unittest.TestCase):
    """Every failure mode must degrade to 'unverified', never raise."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_missing_token(self):
        r = wc.record_timestamp_response(os.path.join(self.tmp, "nope.tsr"))
        self.assertFalse(r["verified"])
        self.assertIn("no timestamp token", r["reason"])
        self.assertIsNone(r["tsr_sha256"])

    def test_records_digest_of_a_bogus_token(self):
        p = os.path.join(self.tmp, "fake.tsr")
        with open(p, "wb") as f:
            f.write(b"not really a token")
        r = wc.record_timestamp_response(p)
        self.assertEqual(r["tsr_sha256"],
                         hashlib.sha256(b"not really a token").hexdigest())
        self.assertFalse(r["verified"])
        self.assertTrue(r["reason"])

    def test_no_cafile_is_reported_not_raised(self):
        p = os.path.join(self.tmp, "fake.tsr")
        with open(p, "wb") as f:
            f.write(b"\x30\x03\x02\x01\x00")
        r = wc.record_timestamp_response(p, cafile=None)
        self.assertFalse(r["verified"])
        self.assertTrue(r["reason"])


if __name__ == "__main__":
    unittest.main()
