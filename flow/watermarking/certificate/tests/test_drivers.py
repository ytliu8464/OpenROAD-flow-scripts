# SPDX-License-Identifier: BSD-3-Clause
"""T8 + T10 -- certify.py / verify_ownership.py end to end, with no OpenROAD.

``certify.py`` runs on synthetic embed CSVs in a temporary flow tree;
``verify_ownership.py`` then consumes the result with every stage skipped, so
the whole admissibility chain (certificate hash, Eq. 19, Eq. 18) is exercised
without a database, numpy or a container.  T10 covers the paper's
"earliest valid record is admissible" rule as pure dict manipulation.
"""

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_TESTS = os.path.dirname(os.path.abspath(__file__))
_CERT_DIR = os.path.dirname(_TESTS)
_WM = os.path.dirname(_CERT_DIR)
sys.path.insert(0, _WM)
sys.path.insert(0, _CERT_DIR)

import wm_cert as wc                     # noqa: E402
from verify_ownership import select_admissible_record, _gen_time_key  # noqa: E402

PY = sys.executable
CERTIFY = os.path.join(_CERT_DIR, "certify.py")
VERIFY = os.path.join(_CERT_DIR, "verify_ownership.py")

MASTER_SEED = bytes(range(1, 33))

PLACE_HEADER = [
    "kind", "id", "tile_tx", "tile_ty", "row_y_dbu",
    "A_name", "B_name", "C_name", "target_bit", "target_perm",
    "orig_order", "wm_order", "hpwl_delta_dbu", "disp_max_dbu",
    "satisfied", "skipped_reason",
]
CTS_HEADER = [
    "pair_idx", "pair_key", "channel", "L_A", "L_B", "target_lcb",
    "other_lcb", "target_bit", "final_bit", "repair_fanout_target",
    "repair_fanout_other", "skipped_reason",
]


class _Tree(unittest.TestCase):
    """A minimal flow tree: designs/, results/ and a gen_key bundle."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.flow_home = os.path.join(self.tmp, "flow")
        self.results = os.path.join(self.flow_home, "results", "nangate45",
                                    "jpeg", "pdmarks-all-stage")
        os.makedirs(self.results)
        os.makedirs(os.path.join(self.flow_home, "designs", "nangate45", "jpeg"))
        with open(os.path.join(self.flow_home, "designs", "nangate45", "jpeg",
                               "config.mk"), "w") as f:
            f.write("export DESIGN_NAME = jpeg_encoder\n")
        with open(os.path.join(self.results, "1_2_yosys.v"), "w") as f:
            f.write("module jpeg_encoder(); endmodule\n")
        with open(os.path.join(self.results, "clock_period.txt"), "w") as f:
            f.write("1.00\n")

        with open(os.path.join(self.results, "wm_place_order_embed.csv"),
                  "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(PLACE_HEADER)
            for i in range(6):
                w.writerow(["pair", f"c{i}a|c{i}b", "0", "0", "0",
                            f"c{i}a", f"c{i}b", "", str(i % 2), "",
                            "", "", "0", "0", "True",
                            "" if i < 4 else "balance_cap"])
        with open(os.path.join(self.results, "wm_cts_pairs_embed.csv"),
                  "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=CTS_HEADER)
            w.writeheader()
            for i in range(3):
                w.writerow(dict(pair_idx=str(i), pair_key=f"l{i}a+l{i}b",
                                channel="pure", L_A=f"l{i}a", L_B=f"l{i}b",
                                target_lcb=f"l{i}a", other_lcb=f"l{i}b",
                                target_bit=str(i % 2), final_bit=str(i % 2),
                                repair_fanout_target="",
                                repair_fanout_other="", skipped_reason=""))

        self.seed_file = os.path.join(self.tmp, "master_seed.hex")
        with open(self.seed_file, "w") as f:
            f.write(MASTER_SEED.hex() + "\n")

    def certify(self, *extra, expect=0, env_extra=None):
        env = dict(os.environ)
        for k in list(env):
            if k.startswith(("WM_", "WATERMARK_", "PDMARKS_")):
                env.pop(k)
        env.update(env_extra or {})
        cmd = [PY, CERTIFY, "--design", "jpeg", "--platform", "nangate45",
               "--ref-flow-variant", "pdmarks-all-stage",
               "--flow-home", self.flow_home,
               "--results-dir", self.results,
               "--master-seed-hex", self.seed_file,
               "--id-artifacts", "light", *extra]
        proc = subprocess.run(cmd, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT)
        text = proc.stdout.decode("utf-8", "replace")
        self.assertEqual(proc.returncode, expect, text)
        return text

    def verify(self, *extra, expect=2):
        """Default expect=2: admissible but not accepted.

        These smoke tests skip every stage, so no stage can pass and the
        two-of-three rule necessarily fails.  Exit 2 is the correct signal for
        that; exit 0 would mean ownership was actually established.
        """
        out_json = os.path.join(self.tmp, "verdict.json")
        cmd = [PY, VERIFY, "--cert", os.path.join(self.results, "wm_cert.bin"),
               "--master-seed-hex", self.seed_file,
               "--skip-placement", "--skip-cts", "--skip-routing",
               "--workdir", os.path.join(self.tmp, "work"),
               "--out-json", out_json, *extra]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT)
        text = proc.stdout.decode("utf-8", "replace")
        self.assertEqual(proc.returncode, expect, text)
        with open(out_json) as f:
            return json.load(f), text


class TestCertifyDriver(_Tree):
    def test_writes_the_expected_artifacts(self):
        self.certify("--stages", "placement,cts")
        for name in ("wm_cert.bin", "wm_commit.json", "wm_commit.tsq",
                     "wm_id_d0.json", "wm_cert_manifest.json"):
            self.assertTrue(os.path.isfile(os.path.join(self.results, name)),
                            f"{name} not written")

    def test_leaves_the_plaintext_csvs_alone(self):
        before = {}
        for name in ("wm_place_order_embed.csv", "wm_cts_pairs_embed.csv"):
            with open(os.path.join(self.results, name), "rb") as f:
                before[name] = f.read()
        self.certify("--stages", "placement,cts")
        for name, blob in before.items():
            with open(os.path.join(self.results, name), "rb") as f:
                self.assertEqual(f.read(), blob, f"{name} was modified")

    def test_only_accepted_rows_are_certified(self):
        """Two of the six placement rows carry a rejection reason."""
        self.certify("--stages", "placement,cts")
        with open(os.path.join(self.results, "wm_cert_manifest.json")) as f:
            manifest = json.load(f)
        self.assertEqual(manifest["n_placement_claims"], 4)
        self.assertEqual(manifest["n_cts_claims"], 3)

    def test_commit_record_is_self_describing(self):
        self.certify("--stages", "placement,cts")
        with open(os.path.join(self.results, "wm_commit.json")) as f:
            rec = json.load(f)
        with open(os.path.join(self.results, "wm_cert.bin"), "rb") as f:
            blob = f.read()
        self.assertEqual(rec["cert_sha256"], wc.cert_sha256(blob).hex())
        self.assertEqual(rec["record_encoding"], wc.RECORD_ENCODING)
        # The commitment must reproduce from the recorded fields alone.
        c = wc.commitment(MASTER_SEED, bytes.fromhex(rec["id_d0_sha256"]),
                          bytes.fromhex(rec["nu_hex"]),
                          bytes.fromhex(rec["cert_sha256"]))
        self.assertEqual(c.hex(), rec["commitment_sha256"])
        rs = wc.record_sha256(bytes.fromhex(rec["id_d0_sha256"]),
                              bytes.fromhex(rec["nu_hex"]),
                              bytes.fromhex(rec["cert_sha256"]), c)
        self.assertEqual(rs.hex(), rec["record_sha256"])

    def test_tsq_is_over_the_record_digest(self):
        self.certify("--stages", "placement,cts")
        with open(os.path.join(self.results, "wm_commit.json")) as f:
            rec = json.load(f)
        with open(os.path.join(self.results, "wm_commit.tsq"), "rb") as f:
            tsq = f.read()
        expected = wc.build_timestamp_request(
            bytes.fromhex(rec["record_sha256"]))
        self.assertEqual(tsq, expected)

    def test_routing_without_f_is_a_hard_error(self):
        """Sealing f is the point; silently omitting it would be worse."""
        out = self.certify("--stages", "routing", expect=2)
        self.assertIn("WATERMARK_FRACTION", out)

    def test_routing_f_from_params_file(self):
        with open(os.path.join(self.results, "wm_route_params.json"), "w") as f:
            json.dump({"f": 0.031, "lambda_wm": 70.0, "p_report": 0.4}, f)
        self.certify("--stages", "placement,cts,routing")
        with open(os.path.join(self.results, "wm_cert.bin"), "rb") as f:
            cert = wc.open_certificate(MASTER_SEED, f.read())
        self.assertEqual(cert.gamma.meta["routing"]["f"], 0.031)

    def test_effective_parameters_are_captured(self):
        """The adaptive knobs exist nowhere else on disk."""
        self.certify("--stages", "placement,cts", env_extra={
            "WM_PAIR_DIST_UM": "3.2", "WM_CTS_NUM_PAIRS": "20",
            "WM_REF_TIMING_CLASS": "moderate_neg",
        })
        with open(os.path.join(self.results, "wm_cert.bin"), "rb") as f:
            cert = wc.open_certificate(MASTER_SEED, f.read())
        cfg = cert.gamma.meta["wm_config"]
        self.assertEqual(cfg["placement"]["WM_PAIR_DIST_UM"], "3.2")
        self.assertEqual(cfg["cts"]["WM_CTS_NUM_PAIRS"], "20")
        self.assertEqual(cfg["adaptive"]["WM_REF_TIMING_CLASS"], "moderate_neg")

    def test_config_changes_id_d0(self):
        self.certify("--stages", "placement,cts",
                     env_extra={"WM_PAIR_DIST_UM": "2.0"})
        with open(os.path.join(self.results, "wm_commit.json")) as f:
            a = json.load(f)["id_d0_sha256"]
        self.certify("--stages", "placement,cts", "--force",
                     env_extra={"WM_PAIR_DIST_UM": "4.0"})
        with open(os.path.join(self.results, "wm_commit.json")) as f:
            b = json.load(f)["id_d0_sha256"]
        self.assertNotEqual(a, b)

    def test_refuses_to_overwrite_without_force(self):
        self.certify("--stages", "placement,cts")
        out = self.certify("--stages", "placement,cts", expect=1)
        self.assertIn("refusing to overwrite", out)

    def test_fresh_nonce_each_run(self):
        self.certify("--stages", "placement,cts")
        with open(os.path.join(self.results, "wm_commit.json")) as f:
            first = json.load(f)["nu_hex"]
        self.certify("--stages", "placement,cts", "--force")
        with open(os.path.join(self.results, "wm_commit.json")) as f:
            second = json.load(f)["nu_hex"]
        self.assertNotEqual(first, second)


class TestVerifyDriver(_Tree):
    def test_admissible_with_the_right_key(self):
        self.certify("--stages", "placement,cts")
        verdict, out = self.verify(
            "--commit", os.path.join(self.results, "wm_commit.json"))
        self.assertTrue(verdict["admissibility"]["admissible"])
        self.assertTrue(verdict["admissibility"]["commitment_ok"])
        self.assertTrue(verdict["admissibility"]["aead_auth_ok"])
        self.assertEqual(verdict["gamma"]["n_placement_claims"], 4)
        # Every stage skipped -> nothing passes -> not accepted, but admissible.
        self.assertFalse(verdict["decision"]["accept"])

    def test_wrong_key_is_inadmissible_exit_3(self):
        self.certify("--stages", "placement,cts")
        wrong = os.path.join(self.tmp, "wrong.hex")
        with open(wrong, "w") as f:
            f.write("00" * 32 + "\n")
        out_json = os.path.join(self.tmp, "v.json")
        proc = subprocess.run(
            [PY, VERIFY, "--cert", os.path.join(self.results, "wm_cert.bin"),
             "--master-seed-hex", wrong,
             "--commit", os.path.join(self.results, "wm_commit.json"),
             "--skip-placement", "--skip-cts", "--skip-routing",
             "--workdir", os.path.join(self.tmp, "work"),
             "--out-json", out_json],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(proc.returncode, 3,
                         proc.stdout.decode("utf-8", "replace"))
        with open(out_json) as f:
            verdict = json.load(f)
        self.assertFalse(verdict["admissibility"]["admissible"])
        self.assertIn("commitment_mismatch", verdict["admissibility"]["reason"])

    def test_tampered_certificate_is_inadmissible(self):
        self.certify("--stages", "placement,cts")
        cert_path = os.path.join(self.results, "wm_cert.bin")
        with open(cert_path, "rb") as f:
            blob = bytearray(f.read())
        blob[-1] ^= 0x01
        with open(cert_path, "wb") as f:
            f.write(blob)
        # Without the commitment record the AEAD is what catches it.
        verdict, _ = self.verify(expect=3)
        self.assertFalse(verdict["admissibility"]["aead_auth_ok"])
        self.assertIn("aead_auth_failed", verdict["admissibility"]["reason"])

    def test_swapped_certificate_reports_hash_mismatch(self):
        """A .bin that is not the one committed to must say so precisely."""
        self.certify("--stages", "placement,cts")
        commit_path = os.path.join(self.results, "wm_commit.json")
        with open(commit_path) as f:
            rec = json.load(f)
        rec["cert_sha256"] = "aa" * 32
        with open(commit_path, "w") as f:
            json.dump(rec, f)
        verdict, _ = self.verify("--commit", commit_path, expect=3)
        self.assertFalse(verdict["admissibility"]["cert_hash_ok"])
        self.assertIn("cert_hash_mismatch", verdict["admissibility"]["reason"])

    def test_verdict_records_routing_parameters(self):
        with open(os.path.join(self.results, "wm_route_params.json"), "w") as f:
            json.dump({"f": 0.02, "lambda_wm": 100.0, "p_report": 0.4}, f)
        self.certify("--stages", "placement,cts,routing")
        verdict, _ = self.verify(
            "--commit", os.path.join(self.results, "wm_commit.json"))
        self.assertEqual(verdict["gamma"]["routing"]["f"], 0.02)


# ---------------------------------------------------------------------------
# T10 -- earliest valid record
# ---------------------------------------------------------------------------

CERT_SHA = "ab" * 32


def _rec(path, *, gen_time=None, verified=True, commitment="11" * 32,
         cert_sha=CERT_SHA, owner="alice", reason=""):
    return {
        "path": path,
        "commit": {"owner_id": owner, "id_d0_sha256": "cc" * 32,
                   "cert_sha256": cert_sha, "commitment_sha256": commitment},
        "timestamp": {"verified": verified, "gen_time": gen_time,
                      "reason": reason},
    }


class TestEarliestValidRecord(unittest.TestCase):
    def test_only_verified_record_wins(self):
        out = select_admissible_record([
            _rec("a", gen_time="Jan  1 00:00:00 2026 GMT", verified=False,
                 reason="no token"),
            _rec("b", gen_time="Feb  1 00:00:00 2026 GMT", verified=True),
            _rec("c", gen_time="Mar  1 00:00:00 2026 GMT", verified=False,
                 reason="bad chain"),
        ], CERT_SHA)
        self.assertEqual(out["chosen"]["path"], "b")
        self.assertEqual(len(out["rejected"]), 2)
        self.assertFalse(out["conflict"])

    def test_earliest_of_several_verified_wins(self):
        out = select_admissible_record([
            _rec("late", gen_time="Mar 15 12:00:00 2026 GMT"),
            _rec("early", gen_time="Jan 20 08:30:00 2026 GMT"),
            _rec("mid", gen_time="Feb 02 23:59:59 2026 GMT"),
        ], CERT_SHA)
        self.assertEqual(out["chosen"]["path"], "early")
        self.assertEqual({r["path"] for r in out["rejected"]}, {"late", "mid"})
        self.assertTrue(all("superseded" in r["reason"]
                            for r in out["rejected"]))

    def test_conflicting_commitments_are_flagged_not_resolved(self):
        out = select_admissible_record([
            _rec("a", gen_time="Jan  1 00:00:00 2026 GMT", commitment="11" * 32),
            _rec("b", gen_time="Feb  1 00:00:00 2026 GMT", commitment="22" * 32),
        ], CERT_SHA)
        self.assertTrue(out["conflict"])
        self.assertIsNone(out["chosen"])
        self.assertEqual(len(out["candidates"]), 2)

    def test_record_naming_another_certificate_is_rejected(self):
        out = select_admissible_record([
            _rec("other", gen_time="Jan  1 00:00:00 2026 GMT",
                 cert_sha="dd" * 32),
        ], CERT_SHA)
        self.assertIsNone(out["chosen"])
        self.assertIn("different certificate", out["rejected"][0]["reason"])

    def test_no_token_at_all(self):
        out = select_admissible_record([
            _rec("a", verified=False, reason="no token supplied")], CERT_SHA)
        self.assertIsNone(out["chosen"])
        self.assertEqual(out["reason"], "no_verified_timestamp")

    def test_empty_input(self):
        out = select_admissible_record([], CERT_SHA)
        self.assertIsNone(out["chosen"])
        self.assertEqual(out["n_records"], 0)

    def test_unparseable_gen_time_sorts_last_but_stays_usable(self):
        out = select_admissible_record([
            _rec("weird", gen_time="not a date"),
            _rec("good", gen_time="Jun  5 01:02:03 2026 GMT"),
        ], CERT_SHA)
        self.assertEqual(out["chosen"]["path"], "good")

        only_weird = select_admissible_record(
            [_rec("weird", gen_time="not a date")], CERT_SHA)
        self.assertEqual(only_weird["chosen"]["path"], "weird")


class TestGenTimeParsing(unittest.TestCase):
    def test_openssl_format(self):
        self.assertIsNotNone(_gen_time_key("Aug 13 04:11:07 2026 GMT"))

    def test_fractional_seconds(self):
        a = _gen_time_key("Aug 13 04:11:07.123 2026 GMT")
        b = _gen_time_key("Aug 13 04:11:07 2026 GMT")
        self.assertEqual(a, b)

    def test_ordering(self):
        self.assertLess(_gen_time_key("Jan  1 00:00:00 2026 GMT"),
                        _gen_time_key("Dec 31 23:59:59 2026 GMT"))

    def test_garbage_is_none(self):
        self.assertIsNone(_gen_time_key(None))
        self.assertIsNone(_gen_time_key("whenever"))


if __name__ == "__main__":
    unittest.main()
