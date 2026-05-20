"""AES-GCM seal/unseal with Keychain-backed per-owner keys."""
from __future__ import annotations

import pytest


def test_seal_unseal_roundtrip(monkeypatch):
    """seal(plaintext, owner) → unseal(...) returns the original bytes."""
    from flyn_memory_router.conv import encrypted_raw
    fixed_key = b"0123456789abcdef"
    monkeypatch.setattr(encrypted_raw, "_get_key", lambda owner_id: fixed_key)

    plaintext = b'{"channel":"telegram","text":"hello"}'
    sealed = encrypted_raw.seal(plaintext, "ryan")
    assert sealed != plaintext
    assert len(sealed) > len(plaintext)  # nonce + tag overhead

    out = encrypted_raw.unseal(sealed, "ryan")
    assert out == plaintext


def test_unseal_wrong_owner_fails(monkeypatch):
    """Sealed with key A, attempted unseal with key B → tamper error."""
    from flyn_memory_router.conv import encrypted_raw
    keys = {"ryan": b"0123456789abcdef", "beth": b"fedcba9876543210"}
    monkeypatch.setattr(encrypted_raw, "_get_key", lambda owner_id: keys[owner_id])

    sealed = encrypted_raw.seal(b"secret", "ryan")
    with pytest.raises(Exception):  # cryptography raises InvalidTag
        encrypted_raw.unseal(sealed, "beth")


def test_keychain_locked_raises(monkeypatch):
    """If `security` CLI fails / times out, raise KeychainLocked."""
    from flyn_memory_router.conv import encrypted_raw
    import subprocess

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=2)

    # Patch subprocess.run inside the encrypted_raw module's reference
    monkeypatch.setattr(encrypted_raw.subprocess, "run", fake_run)
    # Clear any cached key for "ryan"
    encrypted_raw._get_key.cache_clear()
    with pytest.raises(encrypted_raw.KeychainLocked):
        encrypted_raw.seal(b"x", "ryan-locked-test")


def test_tamper_detection(monkeypatch):
    """Modifying any byte of ciphertext → InvalidTag on unseal."""
    from flyn_memory_router.conv import encrypted_raw
    monkeypatch.setattr(encrypted_raw, "_get_key", lambda owner_id: b"0123456789abcdef")

    sealed = encrypted_raw.seal(b"hello world", "ryan")
    # Flip a byte in the middle (past the nonce)
    tampered = sealed[:14] + bytes([sealed[14] ^ 0x01]) + sealed[15:]
    with pytest.raises(Exception):
        encrypted_raw.unseal(tampered, "ryan")


def test_get_key_rejects_short_keychain_value(monkeypatch):
    """If `security` returns a non-hex value (e.g. someone set it manually),
    raise KeychainLocked rather than silently producing a weak key."""
    from flyn_memory_router.conv import encrypted_raw
    import subprocess

    class FakeResult:
        returncode = 0
        stdout = "not-hex-and-too-short\n"
        stderr = ""

    monkeypatch.setattr(encrypted_raw.subprocess, "run",
                        lambda *a, **kw: FakeResult())
    encrypted_raw._get_key.cache_clear()
    with pytest.raises(encrypted_raw.KeychainLocked, match="unexpected format"):
        encrypted_raw.seal(b"x", "ryan-bad-fmt-test")


# --- F2: stale cached key after Keychain rotation ---
# Once _get_key has cached a key, rotating the underlying Keychain entry would
# cause every subsequent unseal() to silently fail with InvalidTag — the
# in-memory cache never re-reads the Keychain. unseal() must self-heal by
# clearing the cache and retrying once on InvalidTag.

def test_unseal_self_heals_on_keychain_rotation(monkeypatch):
    """Cache a stale key, rotate the keychain, unseal() retries with fresh key."""
    from flyn_memory_router.conv import encrypted_raw

    old_key = b"0123456789abcdef"   # 16 bytes
    new_key = b"fedcba9876543210"   # 16 bytes

    # Seal with the NEW key (simulating: someone sealed earlier with new key)
    monkeypatch.setattr(encrypted_raw, "_get_key", lambda owner_id: new_key)
    encrypted_raw._get_key.cache_clear() if hasattr(encrypted_raw._get_key, "cache_clear") else None
    sealed = encrypted_raw.seal(b"important payload", "ryan-rotate")

    # Now simulate: process has the OLD key cached in lru, but Keychain rotated.
    # First _get_key call returns old (cached), second returns new (after clear).
    call_count = {"n": 0}

    def fake_get_key(owner_id: str) -> bytes:
        call_count["n"] += 1
        return old_key if call_count["n"] == 1 else new_key

    monkeypatch.setattr(encrypted_raw, "_get_key", fake_get_key)

    plaintext = encrypted_raw.unseal(sealed, "ryan-rotate")
    assert plaintext == b"important payload"
    # Verify it actually retried (called _get_key twice)
    assert call_count["n"] == 2


def test_unseal_persistently_bad_key_still_raises(monkeypatch):
    """If even after cache clear the key is still wrong, raise (don't loop)."""
    from flyn_memory_router.conv import encrypted_raw

    wrong_key = b"0000000000000000"
    right_key = b"fedcba9876543210"

    monkeypatch.setattr(encrypted_raw, "_get_key", lambda owner_id: right_key)
    sealed = encrypted_raw.seal(b"x", "ryan-bad")

    # After clear, still returns wrong key (e.g. Keychain itself is corrupted)
    monkeypatch.setattr(encrypted_raw, "_get_key", lambda owner_id: wrong_key)

    with pytest.raises(Exception):  # cryptography.InvalidTag on second try
        encrypted_raw.unseal(sealed, "ryan-bad")
