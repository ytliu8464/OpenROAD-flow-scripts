#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Ownership verification against a suspect layout (paper Eq. 20).

Sequence, in order, stopping as soon as the claim becomes inadmissible:

    0.  SHA256(C_Gamma) matches the commitment record       -> else exit 3
    1.  c = SHA256(K || ID(D0) || nu || SHA256(C_Gamma))     -> Eq. 19, exit 3
    2.  K_Gamma = HMAC(K, ID(D0), nu, "cert"); open C_Gamma  -> Eq. 17/18, exit 3
    3.  evaluate Gamma_P and Gamma_C against the suspect layout
    4.  derive K_R from K, rebuild WM_R, run the routing statistic
    5.  accept iff at least two available stages pass        -> Eq. 16

Steps 0-2 are cheap and run before any OpenROAD process is started, so a wrong
key costs nothing.

Extraction-rate denominators
----------------------------
``r_s`` divides by ``|Gamma_s|``, not by the number of claims the verifier
managed to look at.  The paper says a certified object that is missing or
cannot be located unambiguously counts as a mismatch, and
``watermark_verify.verify_from_csv`` skips a row with an empty name *without*
counting it -- so dividing by "checked" would quietly drop such claims from both
numerator and denominator.

Exit codes
----------
``0`` admissible and accepted; ``2`` admissible but not accepted; ``3``
inadmissible (Eq. 19 or Eq. 18 failed); ``1`` operational error.  ``3`` is a
genuinely different outcome from "the watermark is absent" and must not be
conflated with it.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_WM_ROOT = os.path.dirname(_HERE)
_EXPERIMENTS = os.path.join(_WM_ROOT, "experiments")
for _p in (_WM_ROOT, _EXPERIMENTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import wm_cert as wc               # noqa: E402
from wm_prf import derive_stage_seeds  # noqa: E402

EXIT_ACCEPTED = 0
EXIT_ERROR = 1
EXIT_NOT_ACCEPTED = 2
EXIT_INADMISSIBLE = 3


# ---------------------------------------------------------------------------
# Timestamp records: "the earliest valid record is admissible"
# ---------------------------------------------------------------------------

# OpenSSL renders genTime as e.g. "Aug 13 04:11:07 2026 GMT", and with
# sub-second precision as "Aug 13 04:11:07.123 2026 GMT" -- note the fraction
# sits in the middle, before the year, not at the end of the string.
_FRACTION_RE = re.compile(r"\.\d+")
_TZ_RE = re.compile(r"\s*(?:GMT|UTC|Z)\s*$")


def _gen_time_key(gen_time: Optional[str]) -> Optional[datetime]:
    """Parse OpenSSL's ``Time stamp:`` rendering into something orderable."""
    if not gen_time:
        return None
    text = _TZ_RE.sub("", _FRACTION_RE.sub("", gen_time.strip())).strip()
    for fmt in ("%b %d %H:%M:%S %Y", "%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def select_admissible_record(records: List[Dict[str, Any]],
                             cert_sha256_hex: str) -> Dict[str, Any]:
    """Apply the paper's rule: earliest valid record wins, later ones ignored.

    ``records`` are ``{"path", "commit", "timestamp"}`` triples.  A record is a
    candidate only if it names this certificate and carries a token that
    verified.  Among candidates the earliest ``genTime`` is chosen.

    Two verified records for the same ``(owner, ID(D0))`` that carry *different*
    commitments are a conflicting-ownership signal, not something to silently
    resolve: the result then carries ``conflict: True`` and lists them all.
    """
    out: Dict[str, Any] = {
        "chosen": None, "rejected": [], "conflict": False,
        "n_records": len(records),
    }
    candidates: List[Dict[str, Any]] = []

    for rec in records:
        commit = rec.get("commit") or {}
        label = {
            "path": rec.get("path"),
            "owner_id": commit.get("owner_id", ""),
            "id_d0_sha256": commit.get("id_d0_sha256", ""),
            "commitment_sha256": commit.get("commitment_sha256", ""),
            "gen_time": (rec.get("timestamp") or {}).get("gen_time"),
        }
        if commit.get("cert_sha256") and commit["cert_sha256"] != cert_sha256_hex:
            out["rejected"].append({**label, "reason": "names a different certificate"})
            continue
        ts = rec.get("timestamp") or {}
        if not ts.get("verified"):
            out["rejected"].append({
                **label,
                "reason": ts.get("reason") or "no verified timestamp token"})
            continue
        candidates.append({**label, "_key": _gen_time_key(label["gen_time"])})

    if not candidates:
        out["reason"] = "no_verified_timestamp"
        return out

    distinct = {c["commitment_sha256"] for c in candidates}
    if len(distinct) > 1:
        out["conflict"] = True
        out["reason"] = "conflicting commitments for the same owner and design"
        out["candidates"] = candidates
        return out

    # Unparseable genTimes sort last so a well-formed record is always preferred.
    candidates.sort(key=lambda c: (c["_key"] is None, c["_key"] or datetime.max))
    chosen = candidates[0]
    for later in candidates[1:]:
        out["rejected"].append({**{k: v for k, v in later.items() if k != "_key"},
                                "reason": "superseded by an earlier valid record"})
    out["chosen"] = {k: v for k, v in chosen.items() if k != "_key"}
    out["reason"] = "earliest verified record"
    return out


# ---------------------------------------------------------------------------
# Stage evaluation
# ---------------------------------------------------------------------------

def _child_env(base: Dict[str, str], **extra) -> Dict[str, str]:
    env = dict(base)
    env.update({k: str(v) for k, v in extra.items() if v is not None})
    return env


def _run(cmd, env, log_path: Optional[str]) -> Tuple[int, str]:
    proc = subprocess.run(cmd, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT)
    text = proc.stdout.decode("utf-8", "replace")
    if log_path:
        with open(log_path, "w") as f:
            f.write(text)
    return proc.returncode, text


def _verify_placement(args, cert_env, n_claims: int, workdir: str) -> Dict[str, Any]:
    """Run the placement verifier on the suspect ODB through place_wm.sh.

    Going through the wrapper rather than invoking OpenROAD directly reuses the
    PYTHONPATH / OPENROAD_EXE / container plumbing it already gets right --
    including rebuilding the WM_* environment across ``singularity exec -e``,
    which is why the certificate variables are WM_-prefixed.
    """
    out: Dict[str, Any] = {"available": False, "n_claims": n_claims}
    if n_claims == 0:
        out["note"] = "no placement claims in the certificate"
        return out

    summary = os.path.join(workdir, "placement_verify.csv")
    env = _child_env(cert_env,
                     WM_VERIFY_INPUT=args.suspect_odb,
                     WM_VERIFY_CELL_LIST=summary,
                     WM_CERT_STAGE="placement")
    env.pop("WM_CELL_LIST", None)
    rc, text = _run(["bash", os.path.join(_WM_ROOT, "placement_wm", "place_wm.sh"),
                     "verify"], env, os.path.join(workdir, "placement_verify.log"))
    out["exit_code"] = rc

    if not os.path.isfile(summary):
        out["note"] = "the placement verifier produced no summary CSV"
        out["tail"] = text.strip().splitlines()[-5:]
        return out

    metrics = {}
    with open(summary, newline="") as f:
        for row in csv.DictReader(f):
            metrics[row.get("metric", "")] = row.get("value", "")

    def _int(key):
        try:
            return int(metrics.get(key, 0))
        except (TypeError, ValueError):
            return 0

    ok = _int("pairs_ok") + _int("groups_ok")
    out.update({
        "available": True,
        "ok": ok,
        "checked": _int("pairs_checked") + _int("groups_checked"),
        # Denominator is |Gamma_P|: an unlocatable certified tuple is a mismatch.
        "r_P": ok / n_claims,
    })
    return out


def _verify_cts(args, cert_env, n_claims: int, workdir: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"available": False, "n_claims": n_claims}
    if n_claims == 0:
        out["note"] = "no CTS claims in the certificate"
        return out

    report = os.path.join(workdir, "cts_verify.csv")
    env = _child_env(cert_env,
                     WM_CTS_VERIFY_INPUT=args.suspect_odb,
                     WM_CTS_VERIFY_CSV=report,
                     WM_CERT_STAGE="cts")
    env.pop("WM_CELL_LIST", None)
    rc, text = _run(["bash", os.path.join(_WM_ROOT, "cts_wm", "cts_wm.sh"),
                     "verify"], env, os.path.join(workdir, "cts_verify.log"))
    out["exit_code"] = rc

    if not os.path.isfile(report):
        out["note"] = "the CTS verifier produced no report CSV"
        out["tail"] = text.strip().splitlines()[-5:]
        return out

    satisfied = missing = tampered = 0
    rows = 0
    with open(report, newline="") as f:
        for row in csv.DictReader(f):
            rows += 1
            if str(row.get("satisfied", "")).strip().lower() in ("true", "1"):
                satisfied += 1
            if str(row.get("missing", "")).strip().lower() in ("true", "1"):
                missing += 1
            if str(row.get("tampered", "")).strip().lower() in ("true", "1"):
                tampered += 1

    out.update({
        "available": True,
        "ok": satisfied,
        "checked": rows,
        "missing": missing,
        "tampered": tampered,
        "r_C": satisfied / n_claims,
    })
    return out


def _verify_routing(args, master_seed: bytes, meta: Dict[str, Any],
                    workdir: str) -> Dict[str, Any]:
    """Eq. 20 step 4: rebuild WM_R from the key and run the routing statistic.

    ``f`` comes from the sealed metadata.  It is recorded nowhere else on disk
    -- ``routing_wm/run.sh`` only echoes it into a gitignored log -- which is
    why the harness scripts have historically hard-coded 0.01 or 0.05 here.
    """
    out: Dict[str, Any] = {"available": False}
    f = args.routing_f if args.routing_f is not None else (meta.get("routing") or {}).get("f")
    if f in (None, ""):
        out["note"] = "no routing fraction f in the certificate"
        return out
    out["f"] = float(f)
    out["lambda_wm"] = (meta.get("routing") or {}).get("lambda_wm")

    qr_csv = args.route_qr_csv
    if not qr_csv:
        odb = args.suspect_route_odb or args.suspect_odb
        if not odb:
            out["note"] = "no routed ODB or route_qr CSV supplied"
            return out
        qr_csv = os.path.join(workdir, "route_qr.csv")
        rc, text = _run(
            ["bash", os.path.join(_EXPERIMENTS, "tools", "dump_route_qr.sh")],
            _child_env(dict(os.environ), WM_ODB=odb, WM_QR_CSV=qr_csv),
            os.path.join(workdir, "dump_route_qr.log"))
        if not os.path.isfile(qr_csv):
            out["note"] = f"dump_route_qr.sh produced no CSV (rc={rc})"
            return out

    try:
        # numpy is imported at module scope by lib.route_stat, so keep this
        # local: a host without numpy must still be able to verify P and C.
        from lib.route_stat import (per_net_qr, build_qr_vector, observed_T,
                                    design_seed, randomization_pvalue,
                                    exact_tail_log10)
        from lib.keyless_verify import routing_wm_set
    except Exception as e:
        out["note"] = f"routing statistics unavailable ({e})"
        return out

    counts = per_net_qr(qr_csv)
    if not counts:
        out["note"] = f"no per-net routing data in {qr_csv}"
        return out

    seed_r = derive_stage_seeds(master_seed)["routing"]
    wm_set = routing_wm_set(seed_r, counts.keys(), float(f))
    vec = build_qr_vector(counts, wm_set)
    if vec.k == 0 or vec.k >= vec.n_eligible:
        out["note"] = (f"degenerate selection: |WM_R|={vec.k} of "
                       f"{vec.n_eligible} eligible nets")
        return out

    t_obs = observed_T(vec.q, vec.sel)
    design_id = args.design_id or meta.get("design") or ""
    p_r = randomization_pvalue(vec.q, design_seed(design_id), vec.k, t_obs,
                               B=args.randomization_B)
    out.update({
        "available": True,
        "design_id": design_id,
        "k": vec.k,
        "n_eligible": vec.n_eligible,
        "T_R": t_obs,
        "p_R": p_r,
        "B": args.randomization_B,
        # Assumption-free, and not floored at 1/(B+1) the way p_R is.
        "exact_tail_log10": exact_tail_log10(vec.q, vec.sel),
        "q_selected_mean": float(vec.q[vec.sel].mean()),
        "q_unselected_mean": float(vec.q[~vec.sel].mean()),
        "route_qr_csv": qr_csv,
    })
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _fail(verdict: Dict[str, Any], reason: str, args) -> int:
    verdict["admissibility"]["admissible"] = False
    verdict["admissibility"]["reason"] = reason
    _emit(verdict, args)
    print(f"[verify] INADMISSIBLE: {reason}", file=sys.stderr)
    return EXIT_INADMISSIBLE


def _emit(verdict: Dict[str, Any], args) -> None:
    text = json.dumps(verdict, indent=2, sort_keys=True, default=str)
    if args.out_json:
        os.makedirs(os.path.dirname(os.path.abspath(args.out_json)) or ".",
                    exist_ok=True)
        with open(args.out_json, "w") as f:
            f.write(text + "\n")
    else:
        print(text)


def main() -> int:
    from lib.thresholds import TAU_P, TAU_C, ALPHA_R, ownership_pass

    p = argparse.ArgumentParser(description="PDMarks ownership verification")
    p.add_argument("--cert", required=True)
    p.add_argument("--master-seed-hex", required=True,
                   help="claimed key: hex, a *.hex file, or a bundle.json")
    p.add_argument("--commit", action="append", default=[],
                   help="wm_commit.json; repeatable (earliest valid wins)")
    p.add_argument("--tsr", action="append", default=[],
                   help="TSA token, positionally paired with --commit")
    p.add_argument("--tsa-cafile", default=None)
    p.add_argument("--allow-unstamped", action="store_true",
                   help="proceed when no commitment record carries a verified token")
    p.add_argument("--suspect-odb", default=None)
    p.add_argument("--suspect-route-odb", default=None)
    p.add_argument("--route-qr-csv", default=None)
    p.add_argument("--design-id", default=None)
    p.add_argument("--routing-f", type=float, default=None)
    p.add_argument("--randomization-B", type=int, default=100_000)
    p.add_argument("--tau-P", type=float, default=TAU_P)
    p.add_argument("--tau-C", type=float, default=TAU_C)
    p.add_argument("--alpha-R", type=float, default=ALPHA_R)
    p.add_argument("--skip-placement", action="store_true")
    p.add_argument("--skip-cts", action="store_true")
    p.add_argument("--skip-routing", action="store_true")
    p.add_argument("--recheck-id", action="store_true",
                   help="recompute ID(D0) from the local flow tree and compare")
    p.add_argument("--workdir", default=None)
    p.add_argument("--out-json", default=None)
    args = p.parse_args()

    workdir = args.workdir or os.path.join(
        os.path.dirname(os.path.abspath(args.cert)) or ".", "verify_work")
    os.makedirs(workdir, exist_ok=True)

    verdict: Dict[str, Any] = {
        "schema": "pdmarks-verdict/1",
        "certificate": {"path": os.path.abspath(args.cert)},
        "admissibility": {"cert_hash_ok": None, "commitment_ok": None,
                          "aead_auth_ok": None, "admissible": None, "reason": ""},
        "gamma": {},
        "stages": {},
        "timestamp": {},
        "decision": {},
    }

    try:
        with open(args.cert, "rb") as f:
            cert_blob = f.read()
    except OSError as e:
        print(f"[verify] cannot read the certificate: {e}", file=sys.stderr)
        return EXIT_ERROR

    try:
        master_seed = wc.load_master_seed(args.master_seed_hex)
    except ValueError as e:
        print(f"[verify] bad --master-seed-hex: {e}", file=sys.stderr)
        return EXIT_ERROR

    # -- step 0: framing and certificate identity ---------------------------
    try:
        header = wc.parse_certificate_header(cert_blob)
        actual_sha = wc.cert_sha256(cert_blob)
    except wc.CertificateFormatError as e:
        verdict["admissibility"]["cert_hash_ok"] = False
        return _fail(verdict, f"malformed certificate: {e}", args)

    verdict["certificate"].update({
        "version": header.version, "suite": header.suite,
        "id_d0_sha256": header.id_d0.hex(), "nu_hex": header.nu.hex(),
        "cert_sha256": actual_sha.hex(), "bytes": len(cert_blob),
    })

    records: List[Dict[str, Any]] = []
    for i, path in enumerate(args.commit):
        try:
            with open(path) as f:
                rec = json.load(f)
        except (OSError, ValueError) as e:
            print(f"[verify] cannot read {path}: {e}", file=sys.stderr)
            return EXIT_ERROR
        tsr = args.tsr[i] if i < len(args.tsr) else None
        tsq = os.path.join(os.path.dirname(os.path.abspath(path)),
                           rec.get("tsq_file", "wm_commit.tsq"))
        ts = (wc.record_timestamp_response(tsr, tsq if os.path.isfile(tsq) else None,
                                           cafile=args.tsa_cafile)
              if tsr else {"verified": False, "reason": "no token supplied"})
        records.append({"path": path, "commit": rec, "timestamp": ts})

    if records:
        mismatched = [r["path"] for r in records
                      if r["commit"].get("cert_sha256")
                      and r["commit"]["cert_sha256"] != actual_sha.hex()]
        if len(mismatched) == len(records):
            verdict["admissibility"]["cert_hash_ok"] = False
            return _fail(
                verdict,
                "cert_hash_mismatch: no commitment record names this "
                f"certificate (SHA256(C_Gamma)={actual_sha.hex()})", args)
        verdict["admissibility"]["cert_hash_ok"] = True

        sel = select_admissible_record(records, actual_sha.hex())
        verdict["timestamp"] = sel
        if sel["conflict"]:
            return _fail(verdict,
                         "conflicting timestamped commitments for the same "
                         "owner and design identifier", args)
        if sel["chosen"] is None and not args.allow_unstamped:
            print("[verify] WARNING: no commitment record carries a verified "
                  "timestamp; ordering versus layout release is unproven "
                  "(pass --allow-unstamped to silence)", file=sys.stderr)

        # -- step 1: Eq. 19 -------------------------------------------------
        chosen = sel["chosen"] or next(
            (r["commit"] for r in records
             if r["commit"].get("cert_sha256") == actual_sha.hex()), None)
        expected_hex = (chosen or {}).get("commitment_sha256")
        if not expected_hex:
            verdict["admissibility"]["commitment_ok"] = False
            return _fail(verdict, "no usable commitment_sha256 in the record(s)",
                         args)
        try:
            expected = bytes.fromhex(expected_hex)
        except ValueError:
            verdict["admissibility"]["commitment_ok"] = False
            return _fail(verdict, "commitment_sha256 is not valid hex", args)

        if not wc.check_commitment(master_seed, header.id_d0, header.nu,
                                   actual_sha, expected):
            verdict["admissibility"]["commitment_ok"] = False
            return _fail(verdict,
                         "commitment_mismatch: the claimed key does not open "
                         "the registered commitment (Eq. 19)", args)
        verdict["admissibility"]["commitment_ok"] = True
    else:
        verdict["admissibility"]["reason"] = (
            "no commitment record supplied; Eq. 19 was not checked")

    # -- step 2: Eqs. 17-18 -------------------------------------------------
    try:
        cert = wc.open_certificate(master_seed, cert_blob)
    except wc.CertificateAuthError as e:
        verdict["admissibility"]["aead_auth_ok"] = False
        return _fail(verdict, f"aead_auth_failed: {e}", args)
    except wc.CertificateFormatError as e:
        verdict["admissibility"]["aead_auth_ok"] = False
        return _fail(verdict, f"malformed certificate: {e}", args)

    verdict["admissibility"]["aead_auth_ok"] = True
    verdict["admissibility"]["admissible"] = True
    verdict["certificate"]["aead_backend"] = cert.backend

    meta = cert.gamma.meta
    n_p, n_c = len(cert.gamma.placement), len(cert.gamma.cts)
    verdict["gamma"] = {
        "n_placement_claims": n_p,
        "n_cts_claims": n_c,
        "stages": meta.get("stages", []),
        "design": meta.get("design"),
        "platform": meta.get("platform"),
        "routing": meta.get("routing", {}),
        "created_utc": meta.get("created_utc"),
    }

    if args.recheck_id:
        verdict["certificate"]["id_recheck"] = _recheck_id(meta, header, args)

    # -- step 3: placement and CTS -----------------------------------------
    cert_env = _child_env(dict(os.environ),
                          WM_CERT_FILE=os.path.abspath(args.cert),
                          WM_CERT_MASTER_SEED_HEX=args.master_seed_hex,
                          WM_CERT_REQUIRE="1")

    r_p = r_c = p_r = None
    if not args.skip_placement and args.suspect_odb:
        st = _verify_placement(args, cert_env, n_p, workdir)
        verdict["stages"]["placement"] = st
        r_p = st.get("r_P")
    if not args.skip_cts and args.suspect_odb:
        st = _verify_cts(args, cert_env, n_c, workdir)
        verdict["stages"]["cts"] = st
        r_c = st.get("r_C")
    if not args.skip_routing:
        st = _verify_routing(args, master_seed, meta, workdir)
        verdict["stages"]["routing"] = st
        p_r = st.get("p_R")

    # -- step 5: Eq. 16 -----------------------------------------------------
    decision = ownership_pass(r_p, r_c, p_r, tau_P=args.tau_P,
                              tau_C=args.tau_C, alpha_R=args.alpha_R)
    decision["thresholds"] = {"tau_P": args.tau_P, "tau_C": args.tau_C,
                              "alpha_R": args.alpha_R}
    verdict["decision"] = decision

    _emit(verdict, args)

    def _fmt(v):
        return "--" if v is None else (f"{v:.4g}" if isinstance(v, float) else str(v))

    print(f"[verify] admissible : yes (backend={cert.backend})")
    print(f"[verify] claims     : P={n_p} C={n_c}")
    print(f"[verify] r_P={_fmt(r_p)} r_C={_fmt(r_c)} p_R={_fmt(p_r)} "
          f"r_R={_fmt(decision.get('r_R'))}")
    print(f"[verify] stages pass: {decision['num_pass']} of "
          f"{sum(1 for v in (r_p, r_c, decision.get('r_R')) if v is not None)}")
    print(f"[verify] ACCEPTED   : {decision['accept']}")
    return EXIT_ACCEPTED if decision["accept"] else EXIT_NOT_ACCEPTED


def _recheck_id(meta, header, args) -> Dict[str, Any]:
    """Optional owner-side check that the local tree still hashes to ID(D0).

    Verification never needs this: ID(D0) enters only the KDF, the AAD and the
    commitment, all of which read it from the certificate.  Someone who no
    longer has D_0 can still verify ownership.
    """
    flow_home = os.environ.get("FLOW_HOME") or os.path.dirname(_WM_ROOT)
    try:
        preimage = wc.build_id_d0_preimage(
            design=meta.get("design", ""),
            design_nickname=meta.get("design_nickname", ""),
            platform=meta.get("platform", ""),
            ref_flow_variant=meta.get("ref_flow_variant", ""),
            flow_home=flow_home,
            wm_config=meta.get("wm_config", {}),
            tool={},  # tool identity is not reproducible after the fact
            profile="full")
        recomputed = wc.id_d0(preimage)
    except Exception as e:
        return {"checked": False, "note": str(e)}
    return {
        "checked": True,
        "matches": recomputed == header.id_d0,
        "recomputed_sha256": recomputed.hex(),
        "note": ("tool identity is excluded from this recomputation, so a "
                 "mismatch is expected unless the certificate was made with "
                 "the same empty tool block"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
