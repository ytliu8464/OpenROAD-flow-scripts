# SPDX-License-Identifier: BSD-3-Clause
"""Shared helpers for canonical message encoding and per-stage seed derivation."""

from __future__ import annotations

import hashlib
import json
from typing import Dict, Tuple


STAGE_LABELS = ("placement", "cts", "routing")


def canonicalize_message(m: Dict) -> bytes:
    """Canonical JSON encoding used when signing / verifying M.

    Uses sorted keys and compact separators, which is a pragmatic equivalent
    of RFC 8785 (JCS) for our scalar-valued message shape.
    """
    return json.dumps(m, sort_keys=True, separators=(",", ":")).encode("utf-8")


def derive_seeds(sig: bytes) -> Tuple[bytes, Dict[str, bytes]]:
    """Return (master_seed, {stage -> seed}) from a raw signature.

    master_seed   = SHA256(sig)
    seed_<stage>  = SHA256(master_seed || stage_label_bytes)
    """
    master_seed = hashlib.sha256(sig).digest()
    seeds = {
        label: hashlib.sha256(master_seed + label.encode("utf-8")).digest()
        for label in STAGE_LABELS
    }
    return master_seed, seeds


def load_ed25519_public_key(pk_pem_bytes: bytes):
    """Load an Ed25519 public key from PEM (or our PyNaCl-fallback raw PEM)."""
    try:
        from cryptography.hazmat.primitives import serialization

        return ("cryptography", serialization.load_pem_public_key(pk_pem_bytes))
    except Exception:
        pass
    try:
        import nacl.signing  # type: ignore

        # Our fallback PEM wraps the 32B raw public key as hex between markers.
        txt = pk_pem_bytes.decode("utf-8")
        lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
        hex_lines = [ln for ln in lines if not ln.startswith("-----")]
        raw = bytes.fromhex("".join(hex_lines))
        return ("pynacl", nacl.signing.VerifyKey(raw))
    except Exception as e:
        raise RuntimeError(f"Failed to load public key: {e}")


def load_ed25519_private_key(sk_pem_bytes: bytes):
    try:
        from cryptography.hazmat.primitives import serialization

        return (
            "cryptography",
            serialization.load_pem_private_key(sk_pem_bytes, password=None),
        )
    except Exception:
        pass
    try:
        import nacl.signing  # type: ignore

        txt = sk_pem_bytes.decode("utf-8")
        lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
        hex_lines = [ln for ln in lines if not ln.startswith("-----")]
        raw = bytes.fromhex("".join(hex_lines))
        return ("pynacl", nacl.signing.SigningKey(raw))
    except Exception as e:
        raise RuntimeError(f"Failed to load private key: {e}")


def sign_message(sk_handle, canonical_bytes: bytes) -> bytes:
    backend, sk = sk_handle
    if backend == "cryptography":
        return sk.sign(canonical_bytes)
    if backend == "pynacl":
        signed = sk.sign(canonical_bytes)
        return bytes(signed.signature)
    raise RuntimeError(f"Unknown backend: {backend}")


def verify_signature(pk_handle, canonical_bytes: bytes, sig: bytes) -> bool:
    backend, pk = pk_handle
    try:
        if backend == "cryptography":
            pk.verify(sig, canonical_bytes)
            return True
        if backend == "pynacl":
            pk.verify(canonical_bytes, sig)
            return True
    except Exception:
        return False
    return False


def public_key_raw(pk_handle) -> bytes:
    backend, pk = pk_handle
    if backend == "cryptography":
        from cryptography.hazmat.primitives import serialization

        return pk.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    if backend == "pynacl":
        return bytes(pk)
    raise RuntimeError(f"Unknown backend: {backend}")
