#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Build binding message M, sign with sk, and derive per-stage seeds.

Outputs (under ``--out-dir``, default ``out/<design_id>``):

  - ``M.json``              canonical binding message
  - ``sig.bin``             64-byte raw Ed25519 signature
  - ``pk.pem``              copy of the public key for audit
  - ``bundle.json``         {M, sig_b64, pk_fingerprint, master_seed_hex}
  - ``seed_placement.hex``  hex(SHA256(master_seed || b"placement"))
  - ``seed_cts.hex``        hex(SHA256(master_seed || b"cts"))
  - ``seed_routing.hex``    hex(SHA256(master_seed || b"routing"))

master_seed = SHA256(sig).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import seed_common as sc


def _try_git_commit(tool_root: str) -> str:
    try:
        import subprocess

        out = subprocess.check_output(
            ["git", "-C", tool_root, "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return out.decode().strip()
    except Exception:
        return "unknown"


def main() -> int:
    p = argparse.ArgumentParser(
        description="Sign a binding message M and derive per-stage watermarking seeds"
    )
    p.add_argument("--sk", required=True, help="Path to sk.pem")
    p.add_argument("--pk", required=True, help="Path to pk.pem")
    p.add_argument("--owner-id", required=True)
    p.add_argument("--design-id", required=True)
    p.add_argument(
        "--date",
        default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        help="Date in binding message (default: today UTC)",
    )
    p.add_argument("--tool-name", default="OpenROAD")
    p.add_argument(
        "--tool-commit",
        default=None,
        help="Tool commit hash (default: git HEAD of --tool-root, else 'unknown')",
    )
    p.add_argument(
        "--tool-root",
        default=os.environ.get("OPENROAD_ROOT", ""),
        help="Git checkout whose HEAD is recorded in M.tool.commit "
             "(default: $OPENROAD_ROOT, else 'unknown')",
    )
    p.add_argument(
        "--nonce",
        default=None,
        help="Hex nonce (default: 16 random bytes)",
    )
    p.add_argument(
        "--out-dir",
        default=None,
        help="Output directory (default: ./out/<design_id>)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite output files if present",
    )
    args = p.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else Path("out") / args.design_id
    out_dir.mkdir(parents=True, exist_ok=True)

    outputs = [
        "M.json", "sig.bin", "pk.pem", "bundle.json", "master_seed.hex",
        "seed_placement.hex", "seed_cts.hex", "seed_routing.hex",
    ]
    existing = [o for o in outputs if (out_dir / o).exists()]
    if existing and not args.force:
        print(
            f"[sign] refusing to overwrite {existing} in {out_dir} "
            f"(pass --force to override)",
            file=sys.stderr,
        )
        return 1

    tool_commit = args.tool_commit or _try_git_commit(args.tool_root)
    nonce_hex = args.nonce or secrets.token_hex(16)

    m = {
        "owner_id": args.owner_id,
        "design_id": args.design_id,
        "date": args.date,
        "tool": {"name": args.tool_name, "commit": tool_commit},
        "nonce": nonce_hex,
    }
    canonical = sc.canonicalize_message(m)

    sk_handle = sc.load_ed25519_private_key(Path(args.sk).read_bytes())
    sig = sc.sign_message(sk_handle, canonical)
    if len(sig) != 64:
        print(f"[sign] WARNING: unexpected signature length {len(sig)}", file=sys.stderr)

    pk_bytes = Path(args.pk).read_bytes()
    pk_handle = sc.load_ed25519_public_key(pk_bytes)
    if not sc.verify_signature(pk_handle, canonical, sig):
        print("[sign] self-check: signature failed to verify against --pk", file=sys.stderr)
        return 2
    pk_fp = hashlib.sha256(sc.public_key_raw(pk_handle)).hexdigest()

    master_seed, seeds = sc.derive_seeds(sig)

    (out_dir / "M.json").write_bytes(canonical)
    (out_dir / "sig.bin").write_bytes(sig)
    shutil.copyfile(args.pk, out_dir / "pk.pem")

    bundle = {
        "M": m,
        "sig_b64": base64.b64encode(sig).decode("ascii"),
        "pk_fingerprint_sha256": pk_fp,
        "master_seed_hex": master_seed.hex(),
    }
    (out_dir / "bundle.json").write_text(json.dumps(bundle, indent=2) + "\n")

    # The master seed is also the certificate key K of paper Section IV.D: the
    # per-stage seeds and K_Gamma are siblings derived from it by domain
    # separation.  Written out so `certificate/cert.sh` can be pointed at a file
    # rather than having to dig it out of bundle.json.  Purely additive -- M,
    # the signature and every stage seed are unchanged.
    master_path = out_dir / "master_seed.hex"
    master_path.write_text(master_seed.hex() + "\n")
    os.chmod(master_path, 0o600)

    for label, seed in seeds.items():
        (out_dir / f"seed_{label}.hex").write_text(seed.hex() + "\n")

    print(f"[sign] out dir         : {out_dir}")
    print(f"[sign] pk fingerprint  : {pk_fp}")
    print(f"[sign] master_seed_hex : {master_seed.hex()}  -> master_seed.hex (0600)")
    for label in sc.STAGE_LABELS:
        print(f"[sign] seed_{label:<9}: {seeds[label].hex()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
