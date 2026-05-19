"""Per-owner AES-GCM encryption with keys in macOS Keychain.

The plaintext is the redacted-raw Telegram payload; ciphertext format is
`nonce(12 bytes) || ciphertext || auth_tag(16 bytes)` — standard AES-GCM
layout. The 16-byte key per owner is generated on first use via os.urandom
and stored as a generic password in the user's login keychain.

The `security` CLI is used as a subprocess (no pyobjc dep). If the keychain
is locked (Mac asleep, screen locked) the CLI fails fast and we raise
KeychainLocked — the caller (conv_write adapter) treats this as a hard
"can't store this message" condition.
"""
from __future__ import annotations

import os
import subprocess
from functools import lru_cache

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEYCHAIN_SERVICE_PREFIX = "flyn-conv-memory"
KEYCHAIN_ACCOUNT = "aes-key"
KEYCHAIN_TIMEOUT_S = 2.0


class KeychainLocked(Exception):
    """Raised when the macOS keychain cannot be read (locked / timeout)."""


def seal(plaintext: bytes, owner_id: str) -> bytes:
    """Encrypt plaintext with the owner's AES-GCM key. Returns nonce||ct||tag."""
    key = _get_key(owner_id)
    aes = AESGCM(key)
    nonce = os.urandom(12)
    ct = aes.encrypt(nonce, plaintext, associated_data=None)
    return nonce + ct


def unseal(ciphertext: bytes, owner_id: str) -> bytes:
    """Decrypt. Raises cryptography.exceptions.InvalidTag on tamper/wrong key."""
    key = _get_key(owner_id)
    aes = AESGCM(key)
    nonce, ct = ciphertext[:12], ciphertext[12:]
    return aes.decrypt(nonce, ct, associated_data=None)


@lru_cache(maxsize=8)
def _get_key(owner_id: str) -> bytes:
    """Read (or create) the owner's 16-byte AES key from the login keychain."""
    service = f"{KEYCHAIN_SERVICE_PREFIX}:{owner_id}"
    try:
        result = subprocess.run(
            ["security", "find-generic-password",
             "-s", service, "-a", KEYCHAIN_ACCOUNT, "-w"],
            capture_output=True, text=True,
            timeout=KEYCHAIN_TIMEOUT_S, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise KeychainLocked(f"security CLI timed out for {service}") from exc
    except FileNotFoundError as exc:
        raise KeychainLocked("security CLI not found (not macOS?)") from exc

    if result.returncode == 0:
        key_str = result.stdout.strip()
        # Strictly require the stored value to be 32 lowercase hex chars (= 128-bit key).
        # If a stale Keychain entry has the wrong format, fail loudly — silently
        # padding to 16 bytes would produce a weak / zero-padded key.
        if len(key_str) != 32 or not all(c in "0123456789abcdef" for c in key_str):
            raise KeychainLocked(
                f"Keychain entry for {service} has unexpected format "
                f"(got {len(key_str)} chars; expected 32 lowercase hex). "
                f"Delete the entry with `security delete-generic-password -s {service} -a {KEYCHAIN_ACCOUNT}` and rerun."
            )
        return bytes.fromhex(key_str)

    # Not found — create a new 16-byte key
    new_key = os.urandom(16)
    create = subprocess.run(
        ["security", "add-generic-password",
         "-s", service, "-a", KEYCHAIN_ACCOUNT, "-w", new_key.hex()],
        capture_output=True, text=True,
        timeout=KEYCHAIN_TIMEOUT_S, check=False,
    )
    if create.returncode != 0:
        raise KeychainLocked(f"add-generic-password failed: {create.stderr.strip()}")
    return new_key
