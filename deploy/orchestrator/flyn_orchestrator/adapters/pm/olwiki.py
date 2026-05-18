"""OLWikiPMAdapter — mirrors Flyn tasks into the OL wiki as Decision records.

The OL wiki lives at http://127.0.0.1:8200.  The only write endpoint used in
Phase 7 MVP is ``POST /api/decisions``, which creates a durable Decision row
linking the task to a human-readable decision record.

State transitions, artifact links, and comments are no-ops in Phase 7 MVP;
lifecycle mirroring is deferred to Phase 7b once the wiki gains native
status fields.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional

from ...types import TaskRecord, TaskState
from .._observability import emit_swallowed_error
from ._http import default_http

if TYPE_CHECKING:
    from ...memory import MemoryEmitter


class OLWikiPMAdapter:
    name = "olwiki"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8200",
        http: Optional[Callable[..., Any]] = None,
        memory_emitter: Optional["MemoryEmitter"] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = http if http is not None else default_http
        self._memory_emitter = memory_emitter

    @property
    def configured(self) -> bool:
        return bool(self._base_url)

    def create_task(self, t: TaskRecord) -> str:
        """POST a Decision to OL wiki representing acceptance of the task.

        Returns ``olwiki-decision-<id>`` on success, or a synthetic stub ID
        on any HTTP / parse error (best-effort — never raises).
        """
        summary_raw = (t.intent or "").strip()
        payload = {
            "decided_by": t.sender_identifier,
            "summary": (summary_raw[:300] if summary_raw else f"Flyn task {t.task_id}"),
            "body_md": (
                f"## Flyn task {t.task_id}\n\n"
                f"**Workflow:** {t.workflow}\n"
                f"**Requester:** {t.sender_identifier} ({t.sender_role})\n\n"
                f"**Intent:**\n\n{t.intent}\n"
            ),
            "question_ids": [],
            "source_meeting": None,
        }
        try:
            resp = self._http(
                method="POST",
                url=f"{self._base_url}/api/decisions",
                json=payload,
                timeout=5,
            )
            data = resp.json() if hasattr(resp, "json") else resp
            decision_id = data.get("id") if isinstance(data, dict) else None
            if decision_id is None:
                return f"olwiki-stub-{t.task_id}"
            return f"olwiki-decision-{decision_id}"
        except Exception as e:
            emit_swallowed_error(self._memory_emitter, self.name, "create_task", e, task_id=t.task_id)
            return f"olwiki-stub-{t.task_id}"

    def update_state(self, t: TaskRecord, to_state: TaskState) -> None:
        # MVP no-op: OL wiki has no native state field. Deferred to Phase 7b.
        return

    def link_artifact(self, t: TaskRecord, artifact: dict[str, Any]) -> None:
        return  # MVP no-op (deferred)

    def comment_on_task(self, t: TaskRecord, body: str) -> None:
        return  # MVP no-op (deferred)
