#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Seal the accepted watermark claims into a certificate (paper Section IV.D).

Runs after embedding, reads the embed CSVs, and writes::

    wm_cert.bin            header || C_Gamma       (AES-256-GCM, Eq. 18)
    wm_commit.json         ID(D0), nu, SHA256(C_Gamma), c   (Eq. 19)
    wm_commit.tsq          DER RFC 3161 request over SHA256(R)
    wm_id_d0.json          the ID(D0) preimage, for audit
    wm_cert_manifest.json  what was sealed and where it came from

The plaintext embed CSVs are read and **left in place**: the analysis harness,
the attack campaigns and the wrong-key sweep all still read them directly.

Why this step also records the parameters
-----------------------------------------
``experiments/drivers/adaptive_params.sh`` derives the effective watermark
configuration per design from the reference run's timing class, and nothing on
disk records the result.  In particular ``f`` (``WATERMARK_FRACTION``) is only
echoed into a gitignored log, so Eq. 20's step 4 -- "derive K_R and reconstruct
WM_R" -- was not actually executable from the key alone.  Certifying captures
the configuration into the sealed metadata and into ID(D0), which is what makes
"design version and watermark configuration" a well-defined thing to hash.

Consequently this must run **inside the driver, after
``apply_adaptive_wm_params``**, so that the environment it reads is the one the
embedders actually used.  See ``experiments/drivers/run_all_stage.sh``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_WM_ROOT = os.path.dirname(_HERE)
for _p in (_WM_ROOT,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import wm_cert as wc          # noqa: E402
import wm_claims              # noqa: E402


# ---------------------------------------------------------------------------
# The watermark-configuration allow-list
# ---------------------------------------------------------------------------
#
# Explicit rather than "every WM_* variable": the environment also carries
# paths, flow variants and container settings, and folding those into ID(D0)
# would make it depend on *where* the run happened instead of *what* was run.
# Adding a new embedder knob means adding it here -- and the value recorded is
# whatever the environment held, so a knob left at its argparse default is
# simply absent.  That is deliberate: the embedder's argparse default is the
# repo's single source of truth (see place_wm.sh) and must not be duplicated.

PLACEMENT_KNOBS = (
    "WM_GRID_NX", "WM_GRID_NY", "WM_PAIR_DIST_UM", "WM_PAIRS_PER_TILE",
    "WM_GROUPS_PER_TILE", "WM_USE_GROUPS",
    "WM_HPWL_EPS_PAIR_DBU", "WM_HPWL_EPS_GROUP_DBU",
    "WM_HPWL_EPS_PAIR_RELAXED_DBU", "WM_HPWL_CACHE", "WM_HPWL_NET_FANOUT_MAX",
    "WM_FANOUT_MAX", "WM_FANOUT_DIFF_MAX",
    "WM_SLACK_THRESHOLD_NS", "WM_NEIGHBOR_SLACK_MARGIN_NS",
    "WM_CRIT_BIN_NS", "WM_CRIT_BIN_RELAXED_NS",
    "WM_TILE_DENSITY_MAX", "WM_TILE_DISP_CAP_UM", "WM_TILE_OVERSAMPLE",
    "WM_TILE_TOUCH_FRAC_MAX", "WM_TILE_TOUCH_FLOOR_PAIRS",
    "WM_BLOCKAGE_MARGIN_SITES",
    "WM_PAIR_NEIGHBOR_K", "WM_PAIR_NEIGHBOR_K_RELAXED",
    "WM_POST_GUARD", "WM_POST_GUARD_FINAL_CHECK", "WM_GUARD_DEGRADE_NS",
    "WM_MIN_PAIRS_TOTAL", "WM_MAX_DISP_X", "WM_MAX_DISP_Y",
)

CTS_KNOBS = (
    "WM_CTS_NUM_PAIRS", "WM_CTS_SIBLING_DIST_UM", "WM_CTS_DELTA_SITES",
    "WM_CTS_FANOUT_MARGIN", "WM_CTS_MAX_FANOUT",
    "WM_CTS_SLEW_HEADROOM_FRAC", "WM_CTS_MAX_TRANSITION_NS",
    "WM_CTS_MAX_CAP_FF", "WM_CTS_SKEW_SLACK_PS", "WM_CTS_MAX_ATTEMPTS",
    "WM_CTS_R_MAX", "WM_CTS_AVOID_HOLD_REPAIR", "WM_CTS_CHANNEL_BUDGET",
    "WM_CTS_QL_SLEW_HEADROOM_FRAC", "WM_CTS_QL_CAP_HEADROOM_FRAC",
    "WM_CTS_QL_SETUP_SLACK_PS", "WM_CTS_QL_HOLD_SLACK_PS",
    "WM_CTS_QL_SKEW_SLACK_PS",
)

ROUTING_KNOBS = ("WATERMARK_FRACTION", "WATERMARK_STRENGTH", "WATERMARK_P")

ADAPTIVE_KEYS = (
    "WM_REF_TIMING_CLASS", "WM_REF_TCP", "WM_REF_WNS", "WM_REF_TNS",
    "WM_REF_STDCELLS", "WM_REF_NETS", "WM_REF_UTIL", "WM_REF_WNS_FRAC",
)

PLACEMENT_CSV_NAMES = (
    "wm_place_order_embed_all_stage.csv",
    "wm_place_order_embed.csv",
    "wm_place_order_embed_v2.csv",
)
CTS_CSV_NAMES = (
    "wm_cts_pairs_embed_all_stage.csv",
    "wm_cts_pairs_embed.csv",
)


def _collect_wm_config(env) -> Dict[str, Any]:
    def pick(names):
        return {k: env[k] for k in names if env.get(k, "") != ""}

    cfg: Dict[str, Any] = {
        "placement": pick(PLACEMENT_KNOBS),
        "cts": pick(CTS_KNOBS),
        "routing": pick(ROUTING_KNOBS),
    }
    adaptive = pick(ADAPTIVE_KEYS)
    cfg["adaptive"] = {
        "enabled": env.get("PDMARKS_ADAPTIVE_PARAMS", "1") != "0",
        **adaptive,
    }
    return cfg


def _git_head(path: str) -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", path, "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=5)
        return out.decode().strip()
    except Exception:
        return "unknown"


def _find(directory: str, names) -> Optional[str]:
    for name in names:
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            return path
    return None


def _resolve_routing(args, env, results_dir: str) -> Dict[str, Any]:
    """Resolve (f, lambda_wm, p), preferring explicit flags then the params file.

    ``routing_wm/run.sh`` now writes ``wm_route_params.json`` unconditionally,
    which is the reliable source; the environment is the fallback for runs that
    predate it.
    """
    out: Dict[str, Any] = {}
    params_path = os.path.join(results_dir, "wm_route_params.json")
    if os.path.isfile(params_path):
        try:
            with open(params_path) as f:
                out.update(json.load(f))
        except ValueError:
            pass

    for key, flag, envname in (("f", args.routing_f, "WATERMARK_FRACTION"),
                               ("lambda_wm", args.routing_lambda, "WATERMARK_STRENGTH"),
                               ("p_report", args.routing_p, "WATERMARK_P")):
        if flag is not None:
            out[key] = flag
        elif key not in out and env.get(envname):
            try:
                out[key] = float(env[envname])
            except ValueError:
                out[key] = env[envname]
    return out


def main() -> int:
    env = os.environ
    p = argparse.ArgumentParser(
        description="Seal watermark claims into a certificate (paper IV.D)")
    p.add_argument("--design", default=env.get("DESIGN"))
    p.add_argument("--design-nickname", default=env.get("DESIGN_NICKNAME"))
    p.add_argument("--platform", default=env.get("PLATFORM"))
    p.add_argument("--ref-flow-variant", default=env.get("WM_FLOW_VARIANT"))
    p.add_argument("--flow-variant", default=env.get("FLOW_VARIANT"))
    p.add_argument("--flow-home", default=env.get("FLOW_HOME") or
                   os.path.dirname(_WM_ROOT))
    p.add_argument("--results-dir", default=env.get("WM_RESULTS"),
                   help="directory holding the embed CSVs (default $WM_RESULTS)")
    p.add_argument("--out-dir", default=None,
                   help="where to write the certificate (default: --results-dir)")
    p.add_argument("--place-csv", default=None)
    p.add_argument("--cts-csv", default=None)
    p.add_argument("--master-seed-hex", default=None,
                   help="hex, a *.hex file, or a bundle.json "
                        "(default: gen_key/out/<design>/master_seed.hex)")
    p.add_argument("--stages", default="placement,cts,routing")
    p.add_argument("--routing-f", type=float, default=None)
    p.add_argument("--routing-lambda", type=float, default=None)
    p.add_argument("--routing-p", type=float, default=None)
    p.add_argument("--id-artifacts", choices=("full", "light"), default="full")
    p.add_argument("--nonce-hex", default=None,
                   help="24 hex chars; default: 12 fresh random bytes")
    p.add_argument("--tsq", dest="tsq", action="store_true", default=True)
    p.add_argument("--no-tsq", dest="tsq", action="store_false")
    p.add_argument("--tsa-policy", default=None)
    p.add_argument("--tsr", default=None,
                   help="a TSA token already obtained for this commitment")
    p.add_argument("--tsa-cafile", default=None)
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    for required in ("design", "platform"):
        if not getattr(args, required):
            p.error(f"--{required} is required (or set {required.upper()})")
    if not args.results_dir:
        p.error("--results-dir is required (or set WM_RESULTS)")

    nickname = args.design_nickname or args.design
    results_dir = os.path.abspath(args.results_dir)
    out_dir = os.path.abspath(args.out_dir or results_dir)
    stages = [s.strip() for s in args.stages.split(",") if s.strip()]

    # -- master seed --------------------------------------------------------
    seed_src = args.master_seed_hex
    if not seed_src:
        base = os.path.join(_WM_ROOT, "gen_key", "out", args.design)
        for candidate in ("master_seed.hex", "bundle.json"):
            if os.path.isfile(os.path.join(base, candidate)):
                seed_src = os.path.join(base, candidate)
                break
    if not seed_src:
        p.error("could not locate the master seed; pass --master-seed-hex")
    master_seed = wc.load_master_seed(seed_src)

    # -- claims -------------------------------------------------------------
    placement_claims: List[Any] = []
    cts_claims: List[Any] = []
    sources: Dict[str, Optional[str]] = {"placement": None, "cts": None}
    active: List[str] = []

    if "placement" in stages:
        path = args.place_csv or _find(results_dir, PLACEMENT_CSV_NAMES)
        if path:
            placement_claims = wm_claims.placement_claims_from_rows(
                wm_claims.read_placement_csv(path))
            sources["placement"] = path
            active.append("placement")
        else:
            print(f"[certify] no placement embed CSV in {results_dir}; "
                  "placement omitted from the certificate", file=sys.stderr)

    if "cts" in stages:
        path = args.cts_csv or _find(results_dir, CTS_CSV_NAMES)
        if path:
            cts_claims = wm_claims.cts_claims_from_rows(
                wm_claims.read_cts_csv(path))
            sources["cts"] = path
            active.append("cts")
        else:
            print(f"[certify] no CTS embed CSV in {results_dir}; "
                  "CTS omitted from the certificate", file=sys.stderr)

    routing: Dict[str, Any] = {}
    if "routing" in stages:
        routing = _resolve_routing(args, env, results_dir)
        if routing.get("f") in (None, ""):
            p.error(
                "the routing stage was requested but f (WATERMARK_FRACTION) "
                "could not be resolved. Without it a verifier cannot rebuild "
                "WM_R from the key, which is the whole point of sealing it. "
                "Pass --routing-f, or run this from a driver that exports "
                "WATERMARK_FRACTION.")
        active.append("routing")

    if not placement_claims and not cts_claims and "routing" not in active:
        print("[certify] nothing to certify", file=sys.stderr)
        return 1

    # -- identity -----------------------------------------------------------
    wm_config = _collect_wm_config(env)
    if routing:
        wm_config["routing"] = {**wm_config.get("routing", {}), **{
            k: v for k, v in routing.items() if v is not None}}

    tool = {
        "pdmarks_commit": _git_head(_WM_ROOT),
        "orfs_commit": _git_head(args.flow_home),
        "certify_version": 1,
    }
    preimage = wc.build_id_d0_preimage(
        design=args.design, design_nickname=nickname, platform=args.platform,
        ref_flow_variant=args.ref_flow_variant or "", flow_home=args.flow_home,
        wm_config=wm_config, tool=tool, profile=args.id_artifacts)
    id_d0_bytes = wc.id_d0(preimage)

    if args.nonce_hex:
        nu = bytes.fromhex(args.nonce_hex)
        if len(nu) != 12:
            p.error("--nonce-hex must be 24 hex characters (96 bits)")
    else:
        nu = os.urandom(12)

    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = {
        "v": 1,
        "design": args.design,
        "design_nickname": nickname,
        "platform": args.platform,
        "ref_flow_variant": args.ref_flow_variant or "",
        "flow_variant": args.flow_variant or "",
        "stages": active,
        "routing": routing,
        "wm_config": wm_config,
        "created_utc": created,
    }

    # -- seal, commit, stamp ------------------------------------------------
    cert_blob = wc.seal_certificate(
        master_seed, id_d0_bytes=id_d0_bytes, nu=nu, meta=meta,
        placement=placement_claims, cts=cts_claims)
    cert_sha = wc.cert_sha256(cert_blob)
    c = wc.commitment(master_seed, id_d0_bytes, nu, cert_sha)
    rec_sha = wc.record_sha256(id_d0_bytes, nu, cert_sha, c)

    os.makedirs(out_dir, exist_ok=True)
    paths = {
        "cert": os.path.join(out_dir, "wm_cert.bin"),
        "commit": os.path.join(out_dir, "wm_commit.json"),
        "tsq": os.path.join(out_dir, "wm_commit.tsq"),
        "id_d0": os.path.join(out_dir, "wm_id_d0.json"),
        "manifest": os.path.join(out_dir, "wm_cert_manifest.json"),
    }
    existing = [k for k, v in paths.items() if os.path.exists(v)]
    if existing and not args.force:
        print(f"[certify] refusing to overwrite {sorted(existing)} in {out_dir} "
              "(pass --force)", file=sys.stderr)
        return 1

    with open(paths["cert"], "wb") as f:
        f.write(cert_blob)

    commit_record: Dict[str, Any] = {
        "schema": "pdmarks-commit/1",
        "design": args.design,
        "design_nickname": nickname,
        "platform": args.platform,
        "owner_id": env.get("OWNER_ID", ""),
        "id_d0_sha256": id_d0_bytes.hex(),
        "nu_hex": nu.hex(),
        "cert_sha256": cert_sha.hex(),
        "commitment_sha256": c.hex(),
        "record_sha256": rec_sha.hex(),
        "record_encoding": wc.RECORD_ENCODING,
        "cert_file": os.path.basename(paths["cert"]),
        "created_utc": created,
        "tool": tool,
    }

    if args.tsq:
        tsq = wc.build_timestamp_request(rec_sha, policy_oid=args.tsa_policy)
        with open(paths["tsq"], "wb") as f:
            f.write(tsq)
        commit_record["tsq_file"] = os.path.basename(paths["tsq"])

    if args.tsr:
        stamped = os.path.join(out_dir, "wm_commit.tsr")
        if os.path.abspath(args.tsr) != os.path.abspath(stamped):
            with open(args.tsr, "rb") as src, open(stamped, "wb") as dst:
                dst.write(src.read())
        result = wc.record_timestamp_response(
            stamped, paths["tsq"] if args.tsq else None,
            cafile=args.tsa_cafile)
        commit_record["tsr_file"] = os.path.basename(stamped)
        commit_record["timestamp"] = result

    with open(paths["commit"], "w") as f:
        json.dump(commit_record, f, indent=2, sort_keys=True)
        f.write("\n")
    with open(paths["id_d0"], "w") as f:
        json.dump(preimage, f, indent=2, sort_keys=True)
        f.write("\n")
    with open(paths["manifest"], "w") as f:
        json.dump({
            "schema": "pdmarks-cert-manifest/1",
            "stages": active,
            "n_placement_claims": len(placement_claims),
            "n_cts_claims": len(cts_claims),
            "sources": sources,
            "results_dir": results_dir,
            "master_seed_source": seed_src,
            "id_artifact_profile": args.id_artifacts,
            "aead_backend": wc.active_backend(),
            "created_utc": created,
        }, f, indent=2, sort_keys=True)
        f.write("\n")

    print(f"[certify] stages          : {','.join(active) or '(none)'}")
    print(f"[certify] claims          : P={len(placement_claims)} "
          f"C={len(cts_claims)}")
    if routing:
        print(f"[certify] routing         : f={routing.get('f')} "
              f"lambda={routing.get('lambda_wm')}")
    print(f"[certify] ID(D0)          : {id_d0_bytes.hex()}")
    print(f"[certify] nu              : {nu.hex()}")
    print(f"[certify] SHA256(C_Gamma) : {cert_sha.hex()}")
    print(f"[certify] commitment c    : {c.hex()}")
    print(f"[certify] SHA256(R)       : {rec_sha.hex()}")
    print(f"[certify] aead backend    : {wc.active_backend()}")
    print(f"[certify] wrote           : {out_dir}")
    if args.tsq and not args.tsr:
        print("[certify] NOTE: wm_commit.tsq is only a *request*. The "
              "commitment is not binding until you obtain a token -- see "
              "certificate/README.md for the one-line curl to a TSA.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
