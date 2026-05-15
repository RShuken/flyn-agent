from unittest.mock import MagicMock
from flyn_orchestrator.memory import MemoryEmitter


def test_emit_calls_router():
    http = MagicMock()
    http.post.return_value.status_code = 200
    e = MemoryEmitter(router_url="http://localhost:8400", http=http)
    e.emit(source="orchestrator", event_type="task_created", subject="T-1",
           body="task T-1 created", dedup_key="orch-T-1-created")
    assert http.post.called
    args, kwargs = http.post.call_args
    assert args[0].endswith("/api/memory/ingest")
    assert kwargs["json"]["source"] == "orchestrator"


def test_emit_swallows_router_failure():
    http = MagicMock()
    http.post.side_effect = Exception("router down")
    e = MemoryEmitter(router_url="http://localhost:8400", http=http)
    # must not raise — best-effort
    e.emit(source="x", event_type="y", subject="z", body="b"*20, dedup_key="k")
