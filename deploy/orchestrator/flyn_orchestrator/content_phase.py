# deploy/orchestrator/flyn_orchestrator/content_phase.py
"""Content-workflow phase runner.

Walks the content workflow's 8-phase sequential pipeline:
  DECOMPOSED → DISPATCHED (PM refines spec)
  DISPATCHED → RUNNING    (Writer drafts)
  RUNNING    → CHANGES_REQUESTED          (if Editor or Fact-checker blocks)
  RUNNING    → DELIVERABLE_READY          (wants_send=False; draft posted to channel)
  RUNNING    → FINAL_APPROVAL_PENDING     (wants_send=True; teammate approves to send)

Approval handler routes FINAL_APPROVAL_PENDING → COMPLETED (sent) or CANCELLED.
"""
from __future__ import annotations
import json as _json
import os
import re as _re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .content import spec_content, draft_content, edit_content, fact_check_content, humanize_content
from .formatting import format_for_platform
from .types import ApprovalDecision, TaskRecord, TaskState

if TYPE_CHECKING:
    from .phase_services import PhaseServices


def _slugify_for_content(text: str) -> str:
    """Return a filesystem-safe slug from a content title (max 64 chars)."""
    s = _re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:64] or "untitled"


def run(task: TaskRecord, services: "PhaseServices") -> None:
    """Walk the content workflow's state machine."""
    _default_backend = (services.config.default_backend if services.config else "noop")
    backend = services.backend_registry.get(_default_backend)
    scratch = services.scratch_root / task.task_id
    scratch.mkdir(parents=True, exist_ok=True)

    # 1. Spec (PM)
    services.safe_transition(
        task.task_id, TaskState.DECOMPOSED, TaskState.DISPATCHED,
        actor="content", reason="PM refining spec",
    )
    content_spec = spec_content(
        task.intent, scratch_dir=scratch, backend=backend, task_id=task.task_id,
    )
    if content_spec is None or content_spec.title.startswith("("):
        services.safe_transition(
            task.task_id, TaskState.DISPATCHED, TaskState.FAILED,
            actor="content", reason="PM spec unparseable or ambiguous",
        )
        services.memory.emit(
            source="orchestrator", event_type="task_failed",
            subject=task.task_id, body="content PM step failed",
            dedup_key=f"orch-{task.task_id}-pm-fail", importance="warm",
        )
        return

    # 2. Draft (Writer)
    services.safe_transition(
        task.task_id, TaskState.DISPATCHED, TaskState.RUNNING,
        actor="content", reason="drafting",
    )
    draft = draft_content(
        content_spec, scratch_dir=scratch, backend=backend, task_id=task.task_id,
    )
    if not draft.strip():
        services.safe_transition(
            task.task_id, TaskState.RUNNING, TaskState.FAILED,
            actor="content", reason="writer produced no draft",
        )
        return

    # 3 + 4. Edit (Editor — fresh-context) and Fact-check (conditional).
    # Phase 4b: if either gate blocks, auto-retry the writer ONCE with the
    # blocking findings as additional context.
    edit_result, fc_result, failed_at, blocking = _run_edit_and_factcheck(
        content_spec, draft, scratch=scratch, backend=backend, task_id=task.task_id,
    )

    if failed_at is not None:
        # First gate failed — retry once with findings as writer context.
        retry_context = _build_retry_context_content(failed_at, blocking)
        services.memory.emit(
            source="orchestrator", event_type="content_retry_started",
            subject=task.task_id,
            body=f"first {failed_at} review failed; auto-retry with {len(blocking)} blocking findings",
            dedup_key=f"orch-{task.task_id}-content-retry", importance="warm",
        )

        draft = draft_content(
            content_spec, scratch_dir=scratch, backend=backend, task_id=task.task_id,
            extra_context=retry_context,
        )
        if not draft.strip():
            services.safe_transition(
                task.task_id, TaskState.RUNNING, TaskState.FAILED,
                actor="content-retry", reason="writer produced no draft on retry",
            )
            return

        edit_result, fc_result, failed_at, blocking = _run_edit_and_factcheck(
            content_spec, draft, scratch=scratch, backend=backend, task_id=task.task_id,
        )

        services.memory.emit(
            source="orchestrator",
            event_type="content_retry_passed" if failed_at is None else "content_retry_failed",
            subject=task.task_id,
            body=(
                "retry passed"
                if failed_at is None
                else f"retry still blocked at {failed_at}: {len(blocking)} findings"
            ),
            dedup_key=f"orch-{task.task_id}-content-retry-result", importance="warm",
        )

        if failed_at is not None:
            # Still blocked after retry — record + transition to CHANGES_REQUESTED.
            services.store.update_task_payload(task.task_id, {
                "content_retry_count": 1,
                "content_blocking_at": failed_at,
                "content_blocking_findings": [_finding_dict(f) for f in blocking],
            })
            services.safe_transition(
                task.task_id, TaskState.RUNNING, TaskState.CHANGES_REQUESTED,
                actor=failed_at,
                reason=f"{failed_at} blocked twice: {len(blocking)} findings",
            )
            services.memory.emit(
                source="orchestrator", event_type="content_changes_requested",
                subject=task.task_id,
                body=(
                    f"{failed_at} blocked with {len(blocking)} critical/important findings "
                    f"after retry: "
                    + (edit_result.summary if failed_at == "editor" and edit_result else
                       fc_result.summary if fc_result else "(no summary)")
                ),
                dedup_key=f"orch-{task.task_id}-{failed_at}-block", importance="warm",
            )
            return

    # 5. Humanize (optional)
    if content_spec.needs_humanize:
        draft = humanize_content(
            content_spec, draft, scratch_dir=scratch, backend=backend, task_id=task.task_id,
        )

    # 6. Format for platform
    formatted = format_for_platform(draft, platform=content_spec.platform)

    # 7. Write to disk
    root = Path(os.environ.get(
        "FLYN_CONTENT_OUTPUT_ROOT",
        str(Path.home() / "Work" / "content"),
    ))
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    topic_slug = _slugify_for_content(content_spec.title)
    topic_dir = root / topic_slug
    topic_dir.mkdir(parents=True, exist_ok=True)
    draft_path = topic_dir / f"{date_str}-{topic_slug}.md"
    draft_path.write_text(formatted.text)

    # Metadata sidecar
    meta_path = topic_dir / f"{date_str}-{topic_slug}.metadata.json"
    meta_path.write_text(_json.dumps({
        "task_id": task.task_id,
        "spec": {
            "title": content_spec.title,
            "platform": content_spec.platform,
            "tone": content_spec.tone,
            "voice": content_spec.voice,
            "length_target": content_spec.length_target,
            "wants_send": content_spec.wants_send,
            "send_destination": content_spec.send_destination,
        },
        "warnings": formatted.warnings,
    }, indent=2))

    # 8. Decide final state
    services.store.update_task_payload(task.task_id, {
        "draft_path": str(draft_path),
        "content_title": content_spec.title,
        "wants_send": content_spec.wants_send,
        "send_destination": content_spec.send_destination,
        "platform": content_spec.platform,
    })

    if content_spec.wants_send and content_spec.send_destination:
        services.safe_transition(
            task.task_id, TaskState.RUNNING, TaskState.FINAL_APPROVAL_PENDING,
            actor="router",
            reason=f"draft ready; awaiting send approval for {content_spec.send_destination}",
        )
    else:
        services.safe_transition(
            task.task_id, TaskState.RUNNING, TaskState.DELIVERABLE_READY,
            actor="router", reason=f"draft at {draft_path}",
        )

    services.memory.emit(
        source="orchestrator", event_type="content_drafted",
        subject=task.task_id,
        body=f"Content draft '{content_spec.title}' written to {draft_path}",
        dedup_key=f"orch-{task.task_id}-content", importance="warm",
    )

    # Notify originating channel with the formatted draft (truncated to 1500 chars)
    services.notify(
        services.store.get_task(task.task_id), None,
        content_draft_path=str(draft_path),
        content_draft_text=formatted.text[:1500],
    )


def handle_approval(
    task: TaskRecord,
    decision: ApprovalDecision,
    services: "PhaseServices",
) -> TaskRecord:
    """Handle FINAL_APPROVAL_PENDING for content: send draft or cancel."""
    task_id = task.task_id

    if not decision.approved:
        services.safe_transition(
            task_id, TaskState.FINAL_APPROVAL_PENDING, TaskState.CANCELLED,
            actor=decision.approver,
            reason=decision.reason or "rejected",
        )
        return services.store.get_task(task_id)

    # Approved — send the draft to the destination
    payload = task.raw_payload or {}
    draft_path_str = payload.get("draft_path")
    send_dest = payload.get("send_destination", "")
    platform = payload.get("platform", "generic")

    if draft_path_str:
        draft_text = Path(draft_path_str).read_text()
        # MVP: Telegram only — extract chat_id from send_destination string
        m = _re.search(r"chat_id\s+(\d+)", send_dest)
        if m and platform == "telegram":
            chat_id = m.group(1)
            try:
                ch = services.channels.get("telegram") if services.channels else None
                if ch:
                    ch.send(channel=chat_id, body=draft_text)
            except Exception:
                pass  # best-effort; memory event captures it
        else:
            # Non-Telegram platform — defer and log
            services.memory.emit(
                source="orchestrator", event_type="content_send_deferred",
                subject=task_id,
                body=(
                    f"Send to {send_dest!r} (platform={platform}) deferred — "
                    "Phase 4 MVP supports Telegram only"
                ),
                dedup_key=f"orch-{task_id}-send-deferred", importance="warm",
            )

    services.safe_transition(
        task_id, TaskState.FINAL_APPROVAL_PENDING, TaskState.COMPLETED,
        actor=decision.approver, reason="sent",
    )
    services.memory.emit(
        source="orchestrator", event_type="content_sent",
        subject=task_id, body=f"Content approved and sent to {send_dest!r}",
        dedup_key=f"orch-{task_id}-sent", importance="warm",
    )
    return services.store.get_task(task_id)


# ---------------------------------------------------------------------------
# Phase 4b retry helpers
# ---------------------------------------------------------------------------

def _run_edit_and_factcheck(content_spec, draft, *, scratch: Path, backend, task_id: str):
    """Run editor (always) + fact-checker (if spec.needs_fact_check).

    Returns: (edit_result, fc_result_or_None, failed_at, blocking_list)
      - failed_at == "editor" if editor blocked
      - failed_at == "fact_checker" if fact-checker blocked
      - failed_at is None if both passed (or fact-checker skipped + editor passed)
    """
    edit_result = edit_content(
        content_spec, draft, scratch_dir=scratch, backend=backend, task_id=task_id,
    )
    if not edit_result.passed:
        blocking = [e for e in edit_result.edits if e.severity in ("critical", "important")]
        return edit_result, None, "editor", blocking

    fc_result = None
    if content_spec.needs_fact_check:
        fc_result = fact_check_content(
            content_spec, draft, scratch_dir=scratch, backend=backend, task_id=task_id,
        )
        if not fc_result.passed:
            blocking = [f for f in fc_result.findings if f.severity in ("critical", "important")]
            return edit_result, fc_result, "fact_checker", blocking

    return edit_result, fc_result, None, []


def _build_retry_context_content(failed_at: str, blocking) -> str:
    """Format blocking findings as additional context for the retry writer prompt.

    `failed_at` is "editor" or "fact_checker". The retry context names the
    review stage so the writer knows what voice/tone or factual-grounding
    issue to address.
    """
    if not blocking:
        return ""
    header = (
        "## Editor findings from previous draft"
        if failed_at == "editor"
        else "## Fact-checker findings from previous draft"
    )
    lines = [header, ""]
    for item in blocking:
        sev = getattr(item, "severity", "important")
        if failed_at == "editor":
            # EditFinding: severity / type / where / suggestion
            type_ = getattr(item, "type", "other")
            where = getattr(item, "where", "")
            suggestion = getattr(item, "suggestion", "")
            line = f"- **{sev} / {type_}**" + (f" (at `{where}`)" if where else "") + f": {suggestion}"
        else:
            # FactCheckFinding: severity / category (or claim) / note
            cat = getattr(item, "category", getattr(item, "claim", "")) or ""
            note = getattr(item, "note", "")
            line = f"- **{sev}**" + (f" / {cat}" if cat else "") + f": {note}"
        lines.append(line)
    lines.append("")
    lines.append("Please address these in your revised draft.")
    return "\n".join(lines)


def _finding_dict(item) -> dict:
    """Serialize an EditFinding or FactCheckFinding to a payload dict."""
    return {
        "severity": getattr(item, "severity", ""),
        "type": getattr(item, "type", None) or getattr(item, "category", None) or "",
        "where": getattr(item, "where", "") or "",
        "suggestion": getattr(item, "suggestion", "") or "",
        "note": getattr(item, "note", "") or "",
    }
