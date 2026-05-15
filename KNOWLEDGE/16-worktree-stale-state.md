---
name: worktree-stale-state
description: WorktreeManager.allocate must prune+force-delete-orphan-branches before `git worktree add` or it fails on the second task when stale state from prior tasks lingers.
type: reference
---

# Worktree allocation fails after stale state

Discovered 2026-05-15 during Phase 1 post-merge verification. Reproduction:

1. Allocate worktree for T-0001 → succeeds; branch `flyn/T-0001` created, worktree at `/path/T-0001/`
2. Worker runs, task completes
3. Worktree dir gets removed externally (e.g., `worktree-gc` heartbeat in Phase 1b's future)
4. Allocate worktree for T-0002 → succeeds
5. Allocate worktree for T-0003 → **FAILS**: `fatal: a branch named 'flyn/T-0003' already exists` even though task is new

The cause: git's `worktree` ref-table still has `flyn/T-0001`'s ref alive, and the branch creation step fails on the second-try fallback because there's a stale orphan.

The fix: always run `git worktree prune` and force-delete-orphan-branches BEFORE the worktree-add step. See `worktree.py:allocate()`.

The defense matters because the orchestrator can leak state across many task runs; without it, every restart needs manual cleanup.
