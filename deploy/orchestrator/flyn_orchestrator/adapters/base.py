"""ChannelAdapter, NotifyAdapter, PMAdapter Protocols."""
from __future__ import annotations
from typing import Any, Optional, Protocol, runtime_checkable
from ..types import InboundTaskRequest, TaskRecord, TaskState


@runtime_checkable
class ChannelAdapter(Protocol):
    name: str
    def ingest(self, raw_message: dict[str, Any]) -> Optional[InboundTaskRequest]: ...
    def send(self, channel: str, body: str, attachments: Optional[list] = None) -> None: ...
    def approve_button(self, task_id: str, action: str) -> None: ...


@runtime_checkable
class NotifyAdapter(Protocol):
    name: str
    def send(self, event: str, audience: str) -> None: ...


@runtime_checkable
class PMAdapter(Protocol):
    name: str
    def create_task(self, t: TaskRecord) -> str: ...   # returns external_id
    def update_state(self, t: TaskRecord, to_state: TaskState) -> None: ...
    def link_artifact(self, t: TaskRecord, artifact: dict[str, Any]) -> None: ...
    def comment_on_task(self, t: TaskRecord, body: str) -> None: ...
