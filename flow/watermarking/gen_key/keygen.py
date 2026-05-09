#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Generate an Ed25519 keypair (sk.pem, pk.pem) and append a registry entry.

registry.json format::

    {
      "entries": [
        {"owner_id": "...", "timestamp_utc": "...",
         "pk_fingerprint_sha256": "<hex>", "pk_path": "..."},
        ...
      ]
    }

This is a local stub for the "trusted registry". In production this step
would push ``(pk, owner_identity, timestamp)`` to an external service.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _load_ed25519_backend():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )

        def gen():
            sk = Ed25519PrivateKey.generate()
            sk_pem = sk.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            pk = sk.public_key()
            pk_pem = pk.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            pk_raw = pk.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            return sk_pem, pk_pem, pk_raw

        return gen
    except Exception:  # pragma: no cover - fallback path
        pass

    try:
        import nacl.signing  # type: ignore

        def gen():
            sk = nacl.signing.SigningKey.generate()
            pk = sk.verify_key
            sk_raw = bytes(sk)
            pk_raw = bytes(pk)
            sk_pem = (
                b"-----BEGIN ED25519 RAW PRIVATE KEY-----\n"
                + sk_raw.hex().encode() + b"\n"
                + b"-----END ED25519 RAW PRIVATE KEY-----\n"
            )
            pk_pem = (
                b"-----BEGIN ED25519 RAW PUBLIC KEY-----\n"
                + pk_raw.hex().encode() + b"\n"
                + b"-----END ED25519 RAW PUBLIC KEY-----\n"
            )
            return sk_pem, pk_pem, pk_raw

        return gen
    except Exception as e:
        raise RuntimeError(
            "Neither 'cryptography' nor 'PyNaCl' is available. Install one:\n"
            "  pip install cryptography    # preferred\n"
            "  pip install PyNaCl          # fallback\n"
            f"(original error: {e})"
        )


def main() -> int:
    p = argparse.ArgumentParser(description="Ed25519 keypair generator")
    p.add_argument("--owner-id", required=True, help="Owner identity string")
    p.add_argument(
        "--out-dir",
        default=os.environ.get("GEN_KEY_DIR", "keys"),
        help="Output directory for sk.pem / pk.pem (default: ./keys)",
    )
    p.add_argument(
        "--registry",
        default=os.environ.get("GEN_KEY_REGISTRY", "registry.json"),
        help="Path to local registry.json (default: ./registry.json)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing sk.pem / pk.pem if present",
    )
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sk_path = out_dir / "sk.pem"
    pk_path = out_dir / "pk.pem"

    if (sk_path.exists() or pk_path.exists()) and not args.force:
        print(
            f"[keygen] refusing to overwrite existing keys in {out_dir} "
            f"(pass --force to override)",
            file=sys.stderr,
        )
        return 1

    gen = _load_ed25519_backend()
    sk_pem, pk_pem, pk_raw = gen()

    sk_path.write_bytes(sk_pem)
    os.chmod(sk_path, 0o600)
    pk_path.write_bytes(pk_pem)
    os.chmod(pk_path, 0o644)

    fp = hashlib.sha256(pk_raw).hexdigest()

    registry_path = Path(args.registry)
    if registry_path.exists():
        try:
            data = json.loads(registry_path.read_text())
        except Exception:
            data = {"entries": []}
    else:
        data = {"entries": []}
    data.setdefault("entries", []).append({
        "owner_id": args.owner_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "pk_fingerprint_sha256": fp,
        "pk_path": str(pk_path.resolve()),
    })
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps(data, indent=2) + "\n")

    print(f"[keygen] sk -> {sk_path}  (0600)")
    print(f"[keygen] pk -> {pk_path}")
    print(f"[keygen] pk_fingerprint_sha256 = {fp}")
    print(f"[keygen] registry updated -> {registry_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
