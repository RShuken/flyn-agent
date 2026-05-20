"""Encrypt stage: AES-GCM seal the raw payload + write ciphertext to messages."""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from ..encrypted_raw import KeychainLocked, seal
from ..schema import open_db
from ..state import Stage
from ..work_queue import Job


class EncryptHandler:
    """Reads messages.body, seals it with the owner's Keychain key,
    writes the ciphertext back to encrypted_raw column.

    The handler does NOT modify workflow state — the worker calls
    advance_stage(ENCRYPT) on its behalf after successful return.
    """

    stage = Stage.ENCRYPT

    def __init__(self, owner_id: str):
        self.owner_id = owner_id

    async def handle(self, job: Job, db_path: Path) -> None:
        def _work() -> None:
            with open_db(db_path) as conn:
                row = conn.execute(
                    "SELECT body, encrypted_raw FROM messages WHERE id = ?",
                    (job.message_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeError(f"message {job.message_id} not found")
                # Idempotent: if already encrypted, skip the actual seal.
                # (The seal would produce a different ciphertext due to fresh
                # nonce; encrypting twice is wasteful.)
                if row["encrypted_raw"] is not None and len(row["encrypted_raw"]) > 0:
                    return
                plaintext = row["body"].encode("utf-8")
                ciphertext = seal(plaintext, self.owner_id)
                conn.execute(
                    "UPDATE messages SET encrypted_raw = ? WHERE id = ?",
                    (ciphertext, job.message_id),
                )

        await asyncio.to_thread(_work)
