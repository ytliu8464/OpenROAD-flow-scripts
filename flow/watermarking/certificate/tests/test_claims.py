# SPDX-License-Identifier: BSD-3-Clause
"""T7 -- the certificate path must not change verifier behaviour.

Two halves:

1. **CSV equality.**  With ``WM_CERT_FILE`` unset, ``wm_claims`` must return
   exactly what the verifiers' own ``csv.DictReader`` loop returns -- same
   order, same keys, same string values.  This is the guarantee that ~12
   analysis scripts and the whole existing experiment corpus keep working.
2. **End-to-end equivalence.**  Sealing a certificate *from* an embed CSV and
   then verifying through the certificate must produce the same claim set, in
   the same order, and the same verification result as reading the CSV.

Both run off-line: ``_orstub`` fakes the ``openroad`` / ``odb`` modules, and the
fake block supplies just enough of ``dbInst`` for the placement verifier.
"""

import contextlib
import csv
import os
import shutil
import sys
import tempfile
import unittest

_TESTS = os.path.dirname(os.path.abspath(__file__))
_WM = os.path.dirname(os.path.dirname(_TESTS))
sys.path.insert(0, _TESTS)
sys.path.insert(0, _WM)

import _orstub  # noqa: E402

_orstub.install()
sys.path.insert(0, os.path.join(_WM, "placement_wm"))
sys.path.insert(0, os.path.join(_WM, "cts_wm"))

import wm_cert as wc      # noqa: E402
import wm_claims          # noqa: E402
import watermark_verify as wv          # noqa: E402
import cts_watermark_verify as cwv     # noqa: E402


# A placement CSV exercising every shape the embedder can emit: an untouched
# claim, a swapped claim, a triple, and each rejection reason.
PLACEMENT_ROWS = [
    # kind,   id,      tx, ty, row, A,      B,      C,     bit, perm, orig, wm, dhp, disp, sat,     reason
    ("pair", "a|b", "1", "2", "0", "cellA", "cellB", "", "0", "", "cellA|cellB", "cellA|cellB", "0", "0", "True", "already_satisfied"),
    ("pair", "c|d", "1", "2", "0", "cellC", "cellD", "", "1", "", "cellC|cellD", "cellD|cellC", "12", "40", "True", ""),
    ("pair", "e|f", "3", "0", "0", "cellE", "cellF", "", "1", "", "cellE|cellF", "", "0", "0", "False", "hpwl_precheck"),
    ("pair", "g|h", "3", "0", "0", "cellG", "cellH", "", "0", "", "cellG|cellH", "", "0", "0", "False", "balance_cap"),
    ("pair", "i|j", "4", "1", "0", "cellI", "cellJ", "", "1", "", "cellI|cellJ", "", "0", "0", "False", "reverted_post_guard"),
    ("pair", "k|l", "4", "1", "0", "cellK", "cellL", "", "0", "", "cellK|cellL", "", "0", "0", "False", "wrong_bit_after_swap"),
    ("triple", "m|n|o", "5", "5", "0", "cellM", "cellN", "cellO", "", "3", "cellM|cellN|cellO", "cellO|cellM|cellN", "5", "0", "True", ""),
    ("triple", "p|q|r", "5", "5", "0", "cellP", "cellQ", "cellR", "", "1", "cellP|cellQ|cellR", "", "0", "0", "False", "order_mismatch_after_apply"),
    # Degenerate rows: malformed target, and a missing name.  Both are things a
    # corrupt CSV could contain, and both must be handled identically.
    ("pair", "s|t", "6", "6", "0", "cellS", "cellT", "", "not-an-int", "", "", "", "0", "0", "True", ""),
    ("pair", "u|", "6", "6", "0", "cellU", "", "", "1", "", "", "", "0", "0", "True", ""),
]

CTS_ROWS = [
    # accepted, zero-edit
    dict(pair_idx="0", pair_key="lcbA+lcbB", channel="pure", L_A="lcbA", L_B="lcbB",
         target_lcb="lcbA", other_lcb="lcbB", target_bit="0", final_bit="0",
         repair_fanout_target="", repair_fanout_other="", skipped_reason=""),
    # accepted, quasi-leaf with repair counts (0 must stay distinct from "")
    dict(pair_idx="1", pair_key="lcbC+lcbD", channel="quasi_leaf", L_A="lcbC", L_B="lcbD",
         target_lcb="lcbD", other_lcb="lcbC", target_bit="1", final_bit="1",
         repair_fanout_target="0", repair_fanout_other="2", skipped_reason=""),
    # accepted, cross-channel
    dict(pair_idx="2", pair_key="lcbE+lcbF", channel="pure_quasi", L_A="lcbE", L_B="lcbF",
         target_lcb="lcbE", other_lcb="lcbF", target_bit="1", final_bit="1",
         repair_fanout_target="1", repair_fanout_other="", skipped_reason=""),
    # rejected: bookkeeping skip
    dict(pair_idx="3", pair_key="lcbG+lcbH", channel="pure", L_A="lcbG", L_B="lcbH",
         target_lcb="", other_lcb="", target_bit="", final_bit="",
         repair_fanout_target="", repair_fanout_other="",
         skipped_reason="lcb_already_used"),
    # rejected: attempt failed
    dict(pair_idx="4", pair_key="lcbI+lcbJ", channel="pure", L_A="lcbI", L_B="lcbJ",
         target_lcb="lcbI", other_lcb="lcbJ", target_bit="1", final_bit="0",
         repair_fanout_target="", repair_fanout_other="",
         skipped_reason="no_boundary_ff"),
    # parity landed wrong with no reason recorded -> final_bit != target_bit
    dict(pair_idx="5", pair_key="lcbK+lcbL", channel="pure", L_A="lcbK", L_B="lcbL",
         target_lcb="lcbK", other_lcb="lcbL", target_bit="1", final_bit="0",
         repair_fanout_target="", repair_fanout_other="", skipped_reason=""),
    # unknown channel -> must normalize to "pure", as read_pairs_csv does
    dict(pair_idx="6", pair_key="lcbM+lcbN", channel="weird", L_A="lcbM", L_B="lcbN",
         target_lcb="lcbM", other_lcb="lcbN", target_bit="0", final_bit="0",
         repair_fanout_target="", repair_fanout_other="", skipped_reason=""),
]

MASTER_SEED = bytes(range(32))
ID_D0 = bytes(range(32, 64))
NU = bytes(range(12))


@contextlib.contextmanager
def cert_env(**kw):
    """Set WM_CERT_* for the duration of a block and restore afterwards."""
    saved = {k: os.environ.get(k) for k in wm_claims.ALL_CERT_ENV_VARS}
    for k in wm_claims.ALL_CERT_ENV_VARS:
        os.environ.pop(k, None)
    os.environ.update({k: str(v) for k, v in kw.items() if v is not None})
    wm_claims._CACHE.clear()
    try:
        yield
    finally:
        for k in wm_claims.ALL_CERT_ENV_VARS:
            os.environ.pop(k, None)
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
        wm_claims._CACHE.clear()


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

        self.place_csv = os.path.join(self.tmp, "wm_place_order_embed.csv")
        with open(self.place_csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(wm_claims.PLACEMENT_CSV_HEADER)
            w.writerows(PLACEMENT_ROWS)

        self.cts_csv = os.path.join(self.tmp, "wm_cts_pairs_embed.csv")
        with open(self.cts_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=wm_claims.CTS_CSV_HEADER)
            w.writeheader()
            for row in CTS_ROWS:
                w.writerow({k: row.get(k, "") for k in wm_claims.CTS_CSV_HEADER})

        self.seed_hex = os.path.join(self.tmp, "master_seed.hex")
        with open(self.seed_hex, "w") as f:
            f.write(MASTER_SEED.hex() + "\n")

    def seal(self, meta=None):
        """Seal a certificate from the fixture CSVs and return its path."""
        p_claims = wm_claims.placement_claims_from_rows(
            wm_claims.read_placement_csv(self.place_csv))
        c_claims = wm_claims.cts_claims_from_rows(
            wm_claims.read_cts_csv(self.cts_csv))
        blob = wc.seal_certificate(
            MASTER_SEED, id_d0_bytes=ID_D0, nu=NU,
            meta=meta or {"design": "t", "routing": {"f": 0.02}},
            placement=p_claims, cts=c_claims)
        path = os.path.join(self.tmp, "wm_cert.bin")
        with open(path, "wb") as f:
            f.write(blob)
        return path, p_claims, c_claims


# ---------------------------------------------------------------------------
# Half 1: the legacy path is untouched
# ---------------------------------------------------------------------------

class TestCsvPathUnchanged(_Fixture):
    def test_placement_rows_identical_to_verifier_reader(self):
        with cert_env():
            self.assertEqual(wm_claims.load_placement_rows(self.place_csv),
                             wv._read_csv(self.place_csv))

    def test_cts_rows_identical_to_dictreader(self):
        with cert_env():
            with open(self.cts_csv, newline="") as f:
                expected = [dict(r) for r in csv.DictReader(f)]
            self.assertEqual(wm_claims.load_cts_rows(self.cts_csv), expected)

    def test_source_reported_as_csv(self):
        with cert_env():
            self.assertFalse(wm_claims.cert_requested())
            self.assertEqual(wm_claims.loaded_source(), "csv")
            self.assertIsNone(wm_claims.cert_meta())

    def test_environment_is_not_mutated(self):
        with cert_env():
            before = dict(os.environ)
            wm_claims.load_placement_rows(self.place_csv)
            wm_claims.load_cts_rows(self.cts_csv)
            self.assertEqual(dict(os.environ), before)

    def test_wm_cert_is_not_even_imported_on_the_legacy_path(self):
        """The strongest form of the backward-compatibility guarantee."""
        for mod in ("wm_cert", "wm_aesgcm"):
            sys.modules.pop(mod, None)
        with cert_env():
            wm_claims.load_placement_rows(self.place_csv)
            self.assertNotIn("wm_cert", sys.modules)
            self.assertNotIn("wm_aesgcm", sys.modules)

    def test_verifier_result_is_unchanged(self):
        rows = wv._read_csv(self.place_csv)
        block = _block_satisfying(rows)
        with cert_env():
            self.assertEqual(wv.verify_from_csv(block, wm_claims.load_placement_rows(self.place_csv)),
                             wv.verify_from_csv(block, rows))


# ---------------------------------------------------------------------------
# Half 2: certificate path == CSV path
# ---------------------------------------------------------------------------

def _active_placement_rows(rows):
    return [r for r in rows if wv._should_verify_row(r.get("skipped_reason", ""))]


def _tuple_of(row):
    return (row.get("kind"), row.get("A_name"), row.get("B_name"),
            row.get("C_name"), row.get("target_bit"), row.get("target_perm"))


def _block_satisfying(rows):
    """A fake block whose x-order satisfies every active claim in ``rows``."""
    insts = []
    x = 0
    for row in _active_placement_rows(rows):
        names = [n for n in (row.get("A_name"), row.get("B_name"),
                             row.get("C_name")) if n]
        if row.get("kind") == "triple" and len(names) == 3:
            try:
                order = wm_claims_perm(names, int(row["target_perm"]))
            except (ValueError, TypeError, KeyError):
                order = names
        elif len(names) == 2:
            try:
                order = names if int(row["target_bit"]) == 0 else names[::-1]
            except (ValueError, TypeError):
                order = names
        else:
            order = names
        for name in order:
            insts.append(_orstub.FakeInst(name, x))
            x += 1000
    return _orstub.FakeBlock(insts)


def wm_claims_perm(names, perm_idx):
    import watermark_common as wcm
    return list(wcm.permuted_order_names(tuple(sorted(names)), perm_idx))


class TestCertificatePathMatches(_Fixture):
    def test_placement_claims_same_content_and_order(self):
        cert_path, p_claims, _ = self.seal()
        csv_active = [_tuple_of(r) for r in
                      _active_placement_rows(wv._read_csv(self.place_csv))
                      if r.get("A_name") and r.get("B_name")
                      and (r.get("kind") != "triple" or r.get("C_name"))]
        with cert_env(WM_CERT_FILE=cert_path,
                      WM_CERT_MASTER_SEED_HEX=self.seed_hex):
            cert_rows = wm_claims.load_placement_rows("")
        self.assertEqual([_tuple_of(r) for r in cert_rows], csv_active)
        self.assertEqual(len(cert_rows), len(p_claims))

    def test_cts_pairs_same_content_and_order(self):
        cert_path, _, c_claims = self.seal()
        csv_pairs = cwv.read_pairs_csv(self.cts_csv)
        with cert_env(WM_CERT_FILE=cert_path,
                      WM_CERT_MASTER_SEED_HEX=self.seed_hex):
            cert_pairs = cwv.rows_to_pairs(wm_claims.load_cts_rows(""))
        self.assertEqual(len(cert_pairs), len(c_claims))
        self.assertEqual([(p.target_lcb, p.target_bit, p.channel,
                           p.repair_fanout_target, p.repair_fanout_other)
                          for p in cert_pairs],
                         [(p.target_lcb, p.target_bit, p.channel,
                           p.repair_fanout_target, p.repair_fanout_other)
                          for p in csv_pairs])

    def test_unknown_channel_normalizes_the_same_way(self):
        """'weird' must become 'pure' on both paths, or the tamper check diverges."""
        cert_path, _, _ = self.seal()
        csv_pairs = {p.pair_key: p.channel for p in cwv.read_pairs_csv(self.cts_csv)}
        with cert_env(WM_CERT_FILE=cert_path,
                      WM_CERT_MASTER_SEED_HEX=self.seed_hex):
            cert_pairs = {p.pair_key: p.channel
                          for p in cwv.rows_to_pairs(wm_claims.load_cts_rows(""))}
        self.assertEqual(cert_pairs["lcbM+lcbN"], "pure")
        self.assertEqual(cert_pairs, csv_pairs)

    def test_end_to_end_verification_matches(self):
        cert_path, _, _ = self.seal()
        csv_rows = wv._read_csv(self.place_csv)
        block = _block_satisfying(csv_rows)
        csv_result = wv.verify_from_csv(block, csv_rows)
        with cert_env(WM_CERT_FILE=cert_path,
                      WM_CERT_MASTER_SEED_HEX=self.seed_hex):
            cert_result = wv.verify_from_csv(block, wm_claims.load_placement_rows(""))

        # The CSV carries one active-but-nameless row that the verifier skips
        # without counting; the certificate never contains such a claim.  Every
        # claim that does exist on both paths must agree.
        self.assertEqual(cert_result[1], csv_result[1])   # pairs ok
        self.assertEqual(cert_result[3], csv_result[3])   # groups ok
        self.assertEqual(cert_result[4], csv_result[4])   # failures

    def test_source_reported_as_certificate(self):
        cert_path, _, _ = self.seal()
        with cert_env(WM_CERT_FILE=cert_path,
                      WM_CERT_MASTER_SEED_HEX=self.seed_hex):
            self.assertTrue(wm_claims.cert_requested())
            self.assertEqual(wm_claims.loaded_source(), "certificate")

    def test_meta_exposes_routing_parameters(self):
        """f is recorded nowhere else on disk; Eq. 20 step 4 needs it."""
        cert_path, _, _ = self.seal(meta={"routing": {"f": 0.037,
                                                      "lambda_wm": 70.0}})
        with cert_env(WM_CERT_FILE=cert_path,
                      WM_CERT_MASTER_SEED_HEX=self.seed_hex):
            self.assertEqual(wm_claims.cert_meta()["routing"]["f"], 0.037)


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------

class TestCertificateFailures(_Fixture):
    def test_wrong_key_raises_auth_error(self):
        cert_path, _, _ = self.seal()
        with cert_env(WM_CERT_FILE=cert_path,
                      WM_CERT_MASTER_SEED_HEX="00" * 32):
            with self.assertRaises(wc.CertificateAuthError):
                wm_claims.load_placement_rows("")

    def test_missing_key_is_an_error_not_a_fallback(self):
        """Never silently read the plaintext CSV when a certificate was asked for."""
        cert_path, _, _ = self.seal()
        with cert_env(WM_CERT_FILE=cert_path):
            with self.assertRaises(wm_claims.ClaimSourceError):
                wm_claims.load_placement_rows(self.place_csv)

    def test_missing_cert_file_is_an_error(self):
        with cert_env(WM_CERT_FILE=os.path.join(self.tmp, "nope.bin"),
                      WM_CERT_MASTER_SEED_HEX=self.seed_hex):
            with self.assertRaises(wm_claims.ClaimSourceError):
                wm_claims.load_placement_rows(self.place_csv)

    def test_commitment_mismatch_is_distinguishable(self):
        import json
        cert_path, _, _ = self.seal()
        with open(cert_path, "rb") as f:
            blob = f.read()
        commit = os.path.join(self.tmp, "wm_commit.json")
        with open(commit, "w") as f:
            json.dump({"cert_sha256": wc.cert_sha256(blob).hex(),
                       "commitment_sha256": "11" * 32}, f)
        with cert_env(WM_CERT_FILE=cert_path,
                      WM_CERT_MASTER_SEED_HEX=self.seed_hex,
                      WM_CERT_COMMIT_JSON=commit):
            with self.assertRaises(wm_claims.CommitmentMismatch):
                wm_claims.load_placement_rows("")

    def test_valid_commitment_is_accepted(self):
        import json
        cert_path, _, _ = self.seal()
        with open(cert_path, "rb") as f:
            blob = f.read()
        cs = wc.cert_sha256(blob)
        c = wc.commitment(MASTER_SEED, ID_D0, NU, cs)
        commit = os.path.join(self.tmp, "wm_commit.json")
        with open(commit, "w") as f:
            json.dump({"cert_sha256": cs.hex(),
                       "commitment_sha256": c.hex()}, f)
        with cert_env(WM_CERT_FILE=cert_path,
                      WM_CERT_MASTER_SEED_HEX=self.seed_hex,
                      WM_CERT_COMMIT_JSON=commit):
            self.assertTrue(wm_claims.load_placement_rows(""))

    def test_swapped_certificate_reports_hash_mismatch_not_commitment(self):
        """A wrong .bin must not masquerade as a wrong key."""
        import json
        cert_path, _, _ = self.seal()
        commit = os.path.join(self.tmp, "wm_commit.json")
        with open(commit, "w") as f:
            json.dump({"cert_sha256": "22" * 32,
                       "commitment_sha256": "11" * 32}, f)
        with cert_env(WM_CERT_FILE=cert_path,
                      WM_CERT_MASTER_SEED_HEX=self.seed_hex,
                      WM_CERT_COMMIT_JSON=commit):
            with self.assertRaises(wm_claims.ClaimSourceError) as ctx:
                wm_claims.load_placement_rows("")
            self.assertNotIsInstance(ctx.exception, wm_claims.CommitmentMismatch)
            self.assertIn("does not match", str(ctx.exception))

    def test_stage_mismatch_is_caught(self):
        cert_path, _, _ = self.seal()
        with cert_env(WM_CERT_FILE=cert_path,
                      WM_CERT_MASTER_SEED_HEX=self.seed_hex,
                      WM_CERT_STAGE="cts"):
            with self.assertRaises(wm_claims.ClaimSourceError):
                wm_claims.load_placement_rows("")


class TestEnvVarNaming(unittest.TestCase):
    def test_every_variable_is_wm_prefixed(self):
        """place_wm.sh / cts_wm.sh forward only ``WM_*`` into the OpenROAD child.

        A variable without the prefix would be silently dropped and the
        certificate would appear not to have been requested at all.
        """
        for name in wm_claims.ALL_CERT_ENV_VARS:
            self.assertTrue(name.startswith("WM_"), name)

    def test_list_covers_what_the_modules_read(self):
        import re
        for path in (os.path.join(_WM, "wm_claims.py"),
                     os.path.join(_WM, "wm_cert.py")):
            with open(path) as f:
                src = f.read()
            for name in re.findall(r"WM_CERT_[A-Z_]+", src):
                self.assertIn(name, wm_claims.ALL_CERT_ENV_VARS,
                              f"{name} (used in {os.path.basename(path)}) is "
                              "missing from ALL_CERT_ENV_VARS")


if __name__ == "__main__":
    unittest.main()
