#!/usr/bin/env python3
"""Repoint Krisp webhook pipeline to the MemoryRouter.

Before:
    POST localhost:8100/api/episode  (Graphiti direct, via graphiti_episode())

After:
    POST localhost:8400/api/memory/ingest  (router; warm tier; fans out to Graphiti + workspace file)

Passthrough mode (`FLYN_MEMORY_ROUTER_PASSTHROUGH=true`, default) preserves the
legacy direct write so this migration is reversible. Flip the env var to false
once the router is verified to be the single source of truth.

This script doesn't execute — it documents the diff. The actual edit is in
deploy/pm/_lib.py, inside route_meeting_to_project(), at the graphiti_episode()
call site (see the diff at the migrating commit for the exact line range).
"""
print(__doc__)
