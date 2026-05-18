"""WebhookPMAdapter — generic JSON-POST adapter for any external dashboard.

Every PMAdapter method fires a structured JSON event to ``target_url``.
All HTTP errors are swallowed (best-effort delivery); the adapter never
raises from a public method.

Intended for future dashboards, status pages, or notification surfaces that
don't yet have a dedicated adapter.  The secret header allows the receiving
server to verify the origin.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional

from ...types import TaskRecord, TaskState
from .._observability import emit_swallowed_error
from ._http import default_http

if TYPE_CHECKING:
    from ...memory import MemoryEmitter


class WebhookPMAdapter:
    name = "webhook"

    def __init__(
        self,
        target_url: str,
        secret: Optional[str] = None,
        http: Optional[Callable[..., Any]] = None,
        memory_emitter: Optional["MemoryEmitter"] = None,
    ) -> None:
        self._target_url = target_url
        self._secret = secret
        self._http = http if http is not None else default_http
        self._memory_emitter = memory_emitter

    @property
    def configured(self) -> bool:
        return bool(self._target_url)

    def _post(self, event_type: str, payload: dict[str, Any], *, task_id: Optional[str] = None) -> None:
        """Fire a JSON event; swallow all exceptions."""
        if not self.configured:
            return
        body = {"event": event_type, "data": payload}
        headers: dict[str, str] = {}
        if self._secret:
            headers["X-Flyn-Secret"] = self._secret
        try:
            self._http(
                method="POST",
                url=self._target_url,
                json=body,
                timeout=5,
                headers=headers,
            )
        except Exception as e:
            emit_swallowed_error(self._memory_emitter, self.name, event_type, e, task_id=task_id)

    def create_task(self, t: TaskRecord) -> str:
        self._post(
            "task_created",
            {
                "task_id": t.task_id,
                "workflow": t.workflow,
                "intent": t.intent,
                "sender_identifier": t.sender_identifier,
                "sender_role": t.sender_role,
            },
            task_id=t.task_id,
        )
        return f"webhook-{t.task_id}"

    def update_state(self, t: TaskRecord, to_state: TaskState) -> None:
        self._post(
            "state_changed",
            {
                "task_id": t.task_id,
                "to_state": to_state.value if hasattr(to_state, "value") else str(to_state),
            },
            task_id=t.task_id,
        )

    def link_artifact(self, t: TaskRecord, artifact: dict[str, Any]) -> None:
        self._post("artifact_linked", {"task_id": t.task_id, "artifact": artifact}, task_id=t.task_id)

    def comment_on_task(self, t: TaskRecord, body: str) -> None:
        self._post("comment_added", {"task_id": t.task_id, "body": body[:5000]}, task_id=t.task_id)
