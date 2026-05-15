#!/usr/bin/env python3
"""Repoint Fathom pipeline to MemoryRouter.

Before:
    POST localhost:8100/api/episode  (Graphiti direct, via graphiti_episode())

After:
    POST localhost:8400/api/memory/ingest  (router; warm tier; fans out to Graphiti + workspace file)

Passthrough mode (FLYN_MEMORY_ROUTER_PASSTHROUGH=true, default) preserves the
legacy direct write so this migration is reversible. Flip the env var to false
once the router is verified to be the single source of truth.

Source field: "fathom" (hard-coded — distinct from Krisp which uses "krisp").

The change happens in deploy/pm/fathom_router.py, inside ingest_to_graphiti().
Unlike Krisp (which flows through route_meeting_to_project() in deploy/pm/_lib.py),
Fathom has its own separate ingest function and write site. T22's _lib.py edit
does NOT cover Fathom — this is a separate Case B migration.

fathom_router.py is currently a skeleton: polling returns [] and only --manual
mode produces live writes. The router POST is wired in anyway so that once the
service-account API key is provisioned and polling is enabled, all new Fathom
meetings will flow through the router from day one.

Dedup key format: fathom-<project_slug>-<date>-<short_slug>
Event type: meeting_summary
"""
print(__doc__)
