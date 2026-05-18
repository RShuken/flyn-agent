"""Observability helper for adapter swallowed-error events.

Closes the gap documented in KNOWLEDGE/20 — adapter methods MUST NOT raise, but
when they swallow an HTTP/IO/auth error there's no signal anywhere that
something went wrong. This helper emits a `adapter_swallowed_error` memory
event so the failure is observable.

Defense in depth: the emit() call is itself wrapped in try/except, so a broken
memory_emitter cannot violate the adapter's never-raise contract.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..memory import MemoryEmitter


def emit_swallowed_error(
    memory_emitter: Optional["MemoryEmitter"],
    adapter_name: str,
    method: str,
    exc: BaseException,
    *,
    task_id: Optional[str] = None,
) -> None:
    """Emit a `adapter_swallowed_error` memory event. No-op if memory_emitter is None.

    Body shape: "<adapter_name>.<method> swallowed <ExceptionClass>: <truncated message>"
    dedup_key shape: f"adapter-{adapter_name}-{method}-{task_id or 'no-task'}"

    Never raises. If the memory_emitter itself throws, the error is swallowed —
    observability must NEVER violate the adapter's best-effort contract.
    """
    if memory_emitter is None:
        return
    try:
        exc_class = type(exc).__name__
        msg = str(exc)[:200] or "(no message)"
        body = f"{adapter_name}.{method} swallowed {exc_class}: {msg}"
        dedup = f"adapter-{adapter_name}-{method}-{task_id or 'no-task'}"
        memory_emitter.emit(
            source="orchestrator",
            event_type="adapter_swallowed_error",
            subject=task_id or adapter_name,
            body=body,
            dedup_key=dedup,
            importance="cool",
        )
    except Exception:
        # Memory layer broken — swallow. Adapter's never-raise contract wins.
        return
