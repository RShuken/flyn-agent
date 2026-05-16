"""Parametrized PMAdapter contract conformance suite.

Every class that claims to be a PMAdapter must satisfy this suite.
Currently covers: LinearPMAdapter, OLWikiPMAdapter, WebhookPMAdapter.

Rules being verified:
  - isinstance check against PMAdapter Protocol (runtime_checkable)
  - non-empty ``name`` attribute
  - ``configured`` is a bool
  - ``create_task`` returns a non-empty string
  - ``update_state``, ``link_artifact``, ``comment_on_task`` return None
  - None of the above raise
  - When the HTTP layer raises, the adapter does NOT propagate (best-effort)
"""
from __future__ import annotations

import pytest

from flyn_orchestrator.adapters.base import PMAdapter
from flyn_orchestrator.adapters.pm.linear import LinearPMAdapter
from flyn_orchestrator.adapters.pm.olwiki import OLWikiPMAdapter
from flyn_orchestrator.adapters.pm.webhook import WebhookPMAdapter
from flyn_orchestrator.types import TaskRecord, TaskState


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

def _make_task(task_id: str = "T-conformance") -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        workflow="dev",
        state=TaskState.INBOUND,
        sender_role="owner",
        sender_identifier="conformance-runner",
        intent="Verify PMAdapter contract",
    )


def _stub_http_ok(**kwargs):
    """Stub HTTP client that returns a minimal success response."""
    class _FakeResp:
        def json(self):
            return {"id": 42}
        @property
        def status_code(self):
            return 201
    return _FakeResp()


def _stub_http_raises(**kwargs):
    """Stub HTTP client that always raises (simulates network failure)."""
    raise OSError("simulated network failure")


# ---------------------------------------------------------------------------
# Parametrized fixture — one instance per adapter
# ---------------------------------------------------------------------------

@pytest.fixture(
    params=[
        pytest.param(("linear", lambda: LinearPMAdapter(api_key="stub-key")), id="linear"),
        pytest.param(("olwiki", lambda: OLWikiPMAdapter(http=_stub_http_ok)), id="olwiki"),
        pytest.param(
            ("webhook", lambda: WebhookPMAdapter(target_url="http://stub.local", http=_stub_http_ok)),
            id="webhook",
        ),
    ]
)
def pm_adapter(request):
    name, factory = request.param
    return name, factory()


@pytest.fixture(
    params=[
        pytest.param(("olwiki", lambda: OLWikiPMAdapter(http=_stub_http_raises)), id="olwiki_err"),
        pytest.param(
            ("webhook", lambda: WebhookPMAdapter(target_url="http://stub.local", http=_stub_http_raises)),
            id="webhook_err",
        ),
    ]
)
def pm_adapter_errhttp(request):
    """Adapters wired to an HTTP client that always fails."""
    name, factory = request.param
    return name, factory()


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------

def test_adapter_implements_protocol(pm_adapter):
    """isinstance check against the runtime_checkable PMAdapter Protocol."""
    name, adapter = pm_adapter
    assert isinstance(adapter, PMAdapter), (
        f"{name!r} does not satisfy PMAdapter structural protocol"
    )


def test_adapter_has_nonempty_name(pm_adapter):
    name, adapter = pm_adapter
    assert isinstance(adapter.name, str), f"{name}: 'name' must be a str"
    assert adapter.name, f"{name}: 'name' must be non-empty"


def test_adapter_configured_is_bool(pm_adapter):
    name, adapter = pm_adapter
    assert isinstance(adapter.configured, bool), (
        f"{name}: 'configured' must return bool, got {type(adapter.configured)}"
    )


def test_create_task_returns_nonempty_string(pm_adapter):
    name, adapter = pm_adapter
    t = _make_task()
    result = adapter.create_task(t)
    assert isinstance(result, str), f"{name}: create_task must return str"
    assert result, f"{name}: create_task must return non-empty string"


def test_update_state_returns_none(pm_adapter):
    name, adapter = pm_adapter
    t = _make_task()
    result = adapter.update_state(t, TaskState.RUNNING)
    assert result is None, f"{name}: update_state must return None"


def test_link_artifact_returns_none(pm_adapter):
    name, adapter = pm_adapter
    t = _make_task()
    result = adapter.link_artifact(t, {"url": "https://example.com/artifact"})
    assert result is None, f"{name}: link_artifact must return None"


def test_comment_on_task_returns_none(pm_adapter):
    name, adapter = pm_adapter
    t = _make_task()
    result = adapter.comment_on_task(t, "This is a review comment.")
    assert result is None, f"{name}: comment_on_task must return None"


def test_http_error_does_not_propagate_from_create_task(pm_adapter_errhttp):
    """Best-effort guarantee: HTTP failure must NOT surface as an exception."""
    name, adapter = pm_adapter_errhttp
    t = _make_task()
    # Must not raise
    result = adapter.create_task(t)
    assert isinstance(result, str) and result, (
        f"{name}: create_task must still return non-empty string on HTTP failure"
    )


def test_http_error_does_not_propagate_from_update_state(pm_adapter_errhttp):
    name, adapter = pm_adapter_errhttp
    t = _make_task()
    adapter.update_state(t, TaskState.COMPLETED)  # must not raise


def test_http_error_does_not_propagate_from_link_artifact(pm_adapter_errhttp):
    name, adapter = pm_adapter_errhttp
    t = _make_task()
    adapter.link_artifact(t, {"url": "https://x"})  # must not raise


def test_http_error_does_not_propagate_from_comment_on_task(pm_adapter_errhttp):
    name, adapter = pm_adapter_errhttp
    t = _make_task()
    adapter.comment_on_task(t, "comment under failure")  # must not raise
