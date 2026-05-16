# deploy/orchestrator/tests/unit/test_phase_services.py
import pytest
from pathlib import Path
from flyn_orchestrator.phase_services import PhaseServices


def test_phase_services_is_frozen():
    """Frozen dataclass: mutation raises FrozenInstanceError."""
    import dataclasses
    svc = PhaseServices(
        store=object(),
        memory=object(),
        channels=None,
        reviewer_invoker=lambda **kw: None,
        transition=lambda *a, **kw: None,
        safe_transition=lambda *a, **kw: None,
        notify=lambda *a, **kw: None,
        backend_registry=object(),
        scratch_root=Path("/tmp"),
        repo_path_for_workflow=lambda w: Path("/tmp"),
        workflows_dir=Path("/tmp"),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        svc.store = "mutated"  # type: ignore


def test_phase_services_exposes_expected_fields():
    """The 11-field surface is the contract phase runners depend on."""
    from dataclasses import fields
    field_names = {f.name for f in fields(PhaseServices)}
    assert field_names == {
        "store", "memory", "channels", "reviewer_invoker",
        "transition", "safe_transition", "notify",
        "backend_registry", "scratch_root", "repo_path_for_workflow",
        "workflows_dir",
    }
