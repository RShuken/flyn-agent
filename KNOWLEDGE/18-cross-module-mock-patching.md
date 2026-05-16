---
name: cross-module-mock-patching
description: When extracting code into a new module, `@patch("oldmodule.fn")` decorators silently miss the call site that now lives in the new module. Patch the new module's namespace too, or import helpers via module-namespace so tests' patches at the helper's home module still intercept.
type: reference
---

# Cross-module mock patching after a refactor

Discovered during Phase 2c T05 (extracting `_run_dev_pr_phase` into `dev_phase.py`). Two distinct failure modes worth knowing:

## Failure mode 1: subprocess.run patched only at old module

`test_pr_lifecycle.py` had:

```python
@patch("flyn_orchestrator.router.subprocess.run")
def test_dev_workflow_opens_pr(mock_run, ...):
    mock_run.side_effect = _make_selective_subprocess_mock()
    ...
```

After the refactor, `git push` runs inside `flyn_orchestrator.dev_phase.subprocess.run` (separate module namespace). The patch on `router.subprocess.run` did NOT intercept it. Worse: the real `subprocess.run` ran, tried to push to a non-existent origin, and the test failed with a confusing "fatal: 'origin' does not appear to be a git repository" instead of a clean assertion mismatch.

**Fix:** patch BOTH module namespaces:

```python
@patch("flyn_orchestrator.pr.create_pr")
@patch("flyn_orchestrator.dev_phase.subprocess.run")
@patch("flyn_orchestrator.router.subprocess.run")
def test_dev_workflow_opens_pr(mock_router_run, mock_dev_phase_run, mock_create_pr, ...):
    mock_router_run.side_effect = _make_selective_subprocess_mock()
    mock_dev_phase_run.side_effect = _make_selective_subprocess_mock()
    ...
```

The `router.subprocess.run` patch is still needed because `_compute_diff` lives in `router.py` and runs `git diff`. Both patches must fire.

## Failure mode 2: function imported by name, not module

The naive refactor of dev_phase.py started with:

```python
from .pr import create_pr, merge_pr, pr_number_from_url
```

This binds `create_pr` to the name in `dev_phase`'s namespace at import time. Tests patching `flyn_orchestrator.pr.create_pr` would NOT intercept calls in `dev_phase.run_pr_phase` — they'd hit the bound reference, not the live module attribute.

**Fix:** import the module itself, call via attribute access:

```python
from . import pr as _pr
# ...
pr_url = _pr.create_pr(...)
```

Now `mock.patch("flyn_orchestrator.pr.create_pr")` replaces the attribute on the `pr` module, and `_pr.create_pr` resolves it at call time. Test patches intercept correctly.

## When to expect this

Any extraction refactor that touches code under test. The check: after the extraction, grep for `@patch.*<old_module>` in tests and verify each patched name still exists at that path. If the patched name moved, either:

- Patch at the new path too (option 1 — useful when the patched name is genuinely owned by both modules, like `subprocess.run`)
- Import via module namespace so the patch at the canonical home module still works (option 2 — preferred for helpers the new module merely uses)

## Why this is silent

`mock.patch` does not validate that the target name is a "good place" to patch. It happily replaces an attribute on a module that nothing calls. The test "passes the patch" but the production code, calling the real helper from elsewhere, runs unmocked. Catching this requires assertions on the mock (`mock_create_pr.assert_called_once_with(...)`) — a positive proof that the patch fired. Tests that only check the side-effect (e.g., "task ends in COMPLETED state") miss it.
