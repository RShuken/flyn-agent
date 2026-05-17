# Smoke tests

Manual ship-gate — hits the **actually running** flyn-memory-router on
:8400. Excluded from default pytest run; intended as the post-install
verification step.

## Run

```
cd deploy/memory-router
python3 -m pytest tests/smoke/ -v -s
```

Expected: 5 tests pass. If the service isn't running, all are skipped
(not failed) via the module-scoped fixture.
