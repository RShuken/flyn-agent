# Migration helpers

One file per pipeline being migrated to the MemoryRouter. Each is a *self-documenting* migration:
the script just prints its docstring describing what the diff did. The actual code change lives
in the target pipeline's source file; see the migrating commit for the exact line range.

Migration is gated by `FLYN_MEMORY_ROUTER_PASSTHROUGH=true` (default) which keeps the legacy
direct-to-Graphiti write running in parallel. Flip to false per-pipeline once you've verified
the router is producing equivalent output.

- `migrate_krisp.py` — Krisp meeting webhook pipeline
- `migrate_fathom.py` — Fathom meeting summary pipeline (Task 23)
