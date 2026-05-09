#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Verify a signed bundle and recompute per-stage seeds.

Reads ``M.json``, ``sig.bin``, ``pk.pem`` and optionally the three
``seed_*.hex`` files. Re-canonicalizes ``M``, verifies Ed25519 signature,
re-derives ``master_seed`` and per-stage seeds, and compares against
on-disk values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import seed_common as sc


def main() -> int:
    p = argparse.ArgumentParser(description="Verify a gen_key/ bundle")
    p.add_argument(
        "--bundle-dir",
        required=True,
        help="Directory containing M.json, sig.bin, pk.pem (and optional seed_*.hex)",
    )
    args = p.parse_args()

    d = Path(args.bundle_dir)
    for name in ("M.json", "sig.bin", "pk.pem"):
        if not (d / name).is_file():
            print(f"[verify] missing {name} in {d}", file=sys.stderr)
            return 1

    m_bytes = (d / "M.json").read_bytes()
    try:
        m = json.loads(m_bytes.decode("utf-8"))
    except Exception as e:
        print(f"[verify] M.json is not valid JSON: {e}", file=sys.stderr)
        return 1
    canonical = sc.canonicalize_message(m)
    if canonical != m_bytes:
        print(
            "[verify] WARNING: M.json is not in canonical form; "
            "using re-canonicalized bytes for verification",
            file=sys.stderr,
        )

    sig = (d / "sig.bin").read_bytes()
    pk_handle = sc.load_ed25519_public_key((d / "pk.pem").read_bytes())

    if not sc.verify_signature(pk_handle, canonical, sig):
        print("[verify] signature verification FAILED", file=sys.stderr)
        return 2

    pk_fp = hashlib.sha256(sc.public_key_raw(pk_handle)).hexdigest()
    master_seed, seeds = sc.derive_seeds(sig)

    print(f"[verify] signature OK")
    print(f"[verify] pk fingerprint  : {pk_fp}")
    print(f"[verify] master_seed_hex : {master_seed.hex()}")

    mismatch = False
    for label in sc.STAGE_LABELS:
        path = d / f"seed_{label}.hex"
        expected = seeds[label].hex()
        if path.is_file():
            got = path.read_text().strip()
            tag = "OK" if got == expected else "MISMATCH"
            if got != expected:
                mismatch = True
            print(f"[verify] seed_{label:<9}: {expected}  [{tag}]")
        else:
            print(f"[verify] seed_{label:<9}: {expected}  (no file on disk)")

    bundle_path = d / "bundle.json"
    if bundle_path.is_file():
        try:
            b = json.loads(bundle_path.read_text())
            if b.get("master_seed_hex") != master_seed.hex():
                print(
                    "[verify] bundle.json master_seed_hex MISMATCH",
                    file=sys.stderr,
                )
                mismatch = True
            if b.get("pk_fingerprint_sha256") != pk_fp:
                print(
                    "[verify] bundle.json pk_fingerprint_sha256 MISMATCH",
                    file=sys.stderr,
                )
                mismatch = True
        except Exception as e:
            print(f"[verify] bundle.json unreadable: {e}", file=sys.stderr)
            mismatch = True

    return 0 if not mismatch else 3


if __name__ == "__main__":
    raise SystemExit(main())
