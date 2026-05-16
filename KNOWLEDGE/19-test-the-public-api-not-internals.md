---
name: test-the-public-api-not-internals
description: Tests that call private methods directly trap future refactors. They force you to keep shims or churn tests for every internal rename. Test the public API; the private internals get exercised transitively.
type: reference
---

# Test the public API, not internals

Discovered during Phase 2c. An integration test (`test_critical_tier_owner_only`) called `router._handle_ops_approval(task, approver, decision, *, approver_role, rationale)` directly. When Phase 2c moved that logic to `ops_phase.handle_approval(task, decision, services)`, the test's call signature became dead — different positional/keyword args, different module.

The original Phase 2c PR kept a 24-line shim on `TaskRouter` (`_handle_ops_approval` → `ops_phase._handle_approval_impl`) so the test wouldn't break. That shim was pure technical debt: it existed only to support one test's habit of reaching past the public API.

## The fix

Rewrite the test to use the public surface. For approval flows, that's `router.handle_approval(task_id, ApprovalDecision(...))`. The `ApprovalDecision` carries everything the internal implementation needs:

| Internal arg | Public encoding |
|---|---|
| `approver_role="teammate"` | `decision.gate = "teammate"` |
| `approver_role="owner"` | `decision.gate in ("owner", "critical")` |
| `decision="approve"` | `decision.approved = True` |
| `decision="reject"` | `decision.approved = False` |
| `rationale="..."` | `decision.reason = "..."` |

The test now reads like real production code:

```python
with pytest.raises(PermissionError, match="owner"):
    router.handle_approval(
        task_id,
        ApprovalDecision(
            task_id=task_id, gate="teammate", approver="eric@example.com",
            approved=True, reason="Eric thinks it is fine",
        ),
    )
```

And the 24-line shim deletes cleanly.

## When to break the rule

Two cases where reaching past the public API is the right call:

1. **Pre-refactor scaffolding.** When the public API doesn't yet exist (or is mid-redesign), private-method tests are scaffolding that gets retired with the next pass.
2. **Verifying an invariant that the public API can't observe.** E.g., asserting that a private cache hits N times when the public surface always returns the same result. Rare.

Most "I need to test the internal because the public is too thin" situations are smell — usually the public is missing a query method that should be added.

## How this couples

Tests that target `_methodname` make refactoring a 2-step move:

1. Move the method. Test breaks.
2. Add a shim with the old signature. Test passes again, but now you have two definitions of the same thing and the shim's signature drift can hide bugs.

OR

1. Move the method. Update the test in lockstep.

The second path is one PR. The first path is technical-debt accrual.

## Tooling

Lint rule worth considering: forbid `router._<name>(` in test files. Private-attribute access on the System Under Test is the smell to catch.
