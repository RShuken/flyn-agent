"""EmailChannelAdapter — IMAP for inbound, SMTP for outbound.

Configured via auth-profiles.json (email:flynn@getcora.io) or env vars
(FLYN_EMAIL_SMTP_HOST / FLYN_EMAIL_IMAP_HOST / FLYN_EMAIL_USERNAME /
FLYN_EMAIL_PASSWORD / FLYN_EMAIL_SMTP_PORT / FLYN_EMAIL_IMAP_PORT).

When not configured:
- send() is a no-op (best-effort guarantee, never raises)
- ingest() still works for allowlisted senders who can supply raw message dicts

Inbound pipeline:
  ingest(raw_email_dict)
    → verify_email_auth        (reject on SPF/DKIM fail unless allowlisted)
    → detect_injection         (reject on suspicious body)
    → parse_subject            (extract tag + task_id_ref)
    → build InboundTaskRequest

Best-effort guarantee (KNOWLEDGE/20-adapters-never-raise.md): all methods
swallow every exception and return None / no-op rather than raising.
"""
from __future__ import annotations

import json
import os
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable, Optional

from ...types import InboundTaskRequest, SenderRole
from .email_auth import verify_email_auth
from .email_subject import TAG_TASK, format_subject, parse_subject
from .injection_detect import detect_injection

# Hardcoded MVP allowlist — Phase 1b will read from CONTACTS.md
DEFAULT_ALLOWLIST: frozenset[str] = frozenset({
    "ryanshuken@gmail.com",
    "beth@cora.community",
    "eric@cora.community",
})

_OWNER_EMAIL = "ryanshuken@gmail.com"


def _load_email_config() -> Optional[dict]:
    """Try auth-profiles.json first, then env vars. Returns config dict or None."""
    p = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
    if p.exists():
        try:
            d = json.load(open(p))
            for key in ("email:flynn@getcora.io", "email:default", "email"):
                if key in d.get("profiles", {}):
                    prof = d["profiles"][key]
                    if all(prof.get(k) for k in ("smtp_host", "imap_host", "username", "password")):
                        return prof
        except Exception:
            pass

    if all(
        os.environ.get(k)
        for k in ("FLYN_EMAIL_SMTP_HOST", "FLYN_EMAIL_IMAP_HOST", "FLYN_EMAIL_USERNAME", "FLYN_EMAIL_PASSWORD")
    ):
        return {
            "smtp_host": os.environ["FLYN_EMAIL_SMTP_HOST"],
            "smtp_port": int(os.environ.get("FLYN_EMAIL_SMTP_PORT", "587")),
            "imap_host": os.environ["FLYN_EMAIL_IMAP_HOST"],
            "imap_port": int(os.environ.get("FLYN_EMAIL_IMAP_PORT", "993")),
            "username": os.environ["FLYN_EMAIL_USERNAME"],
            "password": os.environ["FLYN_EMAIL_PASSWORD"],
        }

    return None


def _classify_sender(sender_email: str, allowlist: frozenset[str]) -> SenderRole:
    low = sender_email.lower()
    if low == _OWNER_EMAIL.lower():
        return "owner"
    if low in (a.lower() for a in allowlist):
        return "teammate"
    return "other"


class EmailChannelAdapter:
    """ChannelAdapter implementation for email (IMAP/SMTP).

    Parameters
    ----------
    config:
        Optional pre-built config dict (smtp_host, imap_host, username,
        password, smtp_port, imap_port).  If None, auto-detected from
        auth-profiles.json or env vars.
    allowlist:
        Set of email addresses that bypass SPF/DKIM auth checks.
    smtp_sender:
        Injected callable for tests: ``smtp_sender(to, subject, body) → None``.
        When provided, bypasses the real smtplib connection.
    imap_fetcher:
        Injected callable for tests: ``imap_fetcher() → list[dict]``.
        Reserved for future poll-mode support; not used by ingest() today.
    """

    name = "email"

    def __init__(
        self,
        config: Optional[dict] = None,
        allowlist: Optional[frozenset[str]] = None,
        smtp_sender: Optional[Callable] = None,
        imap_fetcher: Optional[Callable] = None,
        contacts_path: Optional["Path"] = None,
    ) -> None:
        self._config = config if config is not None else _load_email_config()
        # Allowlist precedence: explicit `allowlist=` arg > CONTACTS.md loader
        # > hardcoded DEFAULT_ALLOWLIST. The CONTACTS.md path defaults to
        # ~/.openclaw/workspace/CONTACTS.md when not provided.
        if allowlist is not None:
            self._allowlist: frozenset[str] = allowlist
        else:
            from .email_allowlist import load_allowlist_from_contacts
            from pathlib import Path as _P
            contacts = contacts_path if contacts_path is not None else (
                _P.home() / ".openclaw" / "workspace" / "CONTACTS.md"
            )
            loaded = load_allowlist_from_contacts(contacts)
            self._allowlist = loaded if loaded is not None else DEFAULT_ALLOWLIST
        self._smtp_sender = smtp_sender
        self._imap_fetcher = imap_fetcher

    @property
    def configured(self) -> bool:
        """True when SMTP/IMAP credentials are available."""
        return self._config is not None

    # ------------------------------------------------------------------
    # ChannelAdapter protocol
    # ------------------------------------------------------------------

    def ingest(self, raw_message: dict[str, Any]) -> Optional[InboundTaskRequest]:
        """Convert a raw email dict into an InboundTaskRequest.

        raw_message keys:
          from        — sender address (required)
          subject     — subject line (may contain [FLYN-TAG:TASK_ID] prefix)
          body        — plain-text body
          headers     — dict of headers; must include Authentication-Results
          message_id  — RFC 2822 Message-ID (optional; synthesised if absent)

        Returns None when:
          - 'from' is missing or empty
          - SPF/DKIM verification fails for a non-allowlisted sender
          - Injection patterns are detected in the body
        """
        try:
            sender = (raw_message.get("from") or "").strip()
            if not sender:
                return None

            # Auth gate
            allowed, auth_reason = verify_email_auth(
                raw_message.get("headers", {}),
                sender,
                allowlist=self._allowlist,
            )
            if not allowed:
                return None

            # Injection gate
            body = raw_message.get("body", "") or ""
            suspicious, _reasons = detect_injection(body)
            if suspicious:
                return None

            # Parse subject tag
            subject_info = parse_subject(raw_message.get("subject", "") or "")

            # Build sender role
            sender_role: SenderRole = _classify_sender(sender, self._allowlist)

            # Synthesise intent from subject + body
            clean = subject_info["clean_subject"]
            intent_parts = [p for p in (clean, body) if p]
            intent = "\n\n".join(intent_parts).strip() or "(empty email)"

            # Synthesise a stable message-id if none provided
            message_id = raw_message.get("message_id", "") or f"email-{sender}-{hash(body) & 0xFFFFFFFF}"

            return InboundTaskRequest(
                channel="email",
                sender_identifier=sender,
                sender_role=sender_role,
                intent=intent,
                external_message_id=message_id,
                raw_payload={
                    "channel": "email",
                    "from": sender,
                    "subject_tag": subject_info["tag"],
                    "task_id_ref": subject_info["task_id"],
                    "auth_reason": auth_reason,
                },
            )
        except Exception:
            return None  # best-effort — never raise

    def send(self, channel: str, body: str, attachments: Optional[list] = None) -> None:
        """Send an email to *channel* (interpreted as a To: address).

        No-op when not configured. Best-effort: swallows all SMTP errors.
        *attachments* is accepted for protocol compatibility but ignored in MVP.
        """
        if not self.configured:
            return
        try:
            subject = format_subject(TAG_TASK, None, body[:60])
            if self._smtp_sender is not None:
                self._smtp_sender(to=channel, subject=subject, body=body)
                return
            # Real SMTP path
            import smtplib

            msg = EmailMessage()
            msg["From"] = self._config["username"]  # type: ignore[index]
            msg["To"] = channel
            msg["Subject"] = subject
            msg.set_content(body)

            with smtplib.SMTP(
                self._config["smtp_host"],  # type: ignore[index]
                self._config.get("smtp_port", 587),  # type: ignore[index]
            ) as s:
                s.starttls()
                s.login(
                    self._config["username"],  # type: ignore[index]
                    self._config["password"],  # type: ignore[index]
                )
                s.send_message(msg)
        except Exception:
            return  # best-effort

    def approve_button(self, task_id: str, action: str) -> None:
        """MVP no-op.

        Email approval UX is reply-with-tagged-subject:
        ``[FLYN-APPROVE:T-XXXX] approve``
        A future phase can generate an HTML email with approve/reject links.
        """
        return
