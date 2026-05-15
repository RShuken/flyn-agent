"""Per-task git worktree allocation. Branch name derived from task_id."""
from __future__ import annotations
import subprocess
from pathlib import Path


class WorktreeManager:
    def __init__(self, workspaces_dir: Path) -> None:
        self._dir = workspaces_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, task_id: str) -> Path:
        return self._dir / task_id

    def allocate(self, *, repo_path: Path, task_id: str, branch: str) -> Path:
        target = self._path_for(task_id)
        if target.exists():
            return target
        # If branch already exists, just point worktree at it; else create
        # `git worktree add <path> -b <branch>` from base or `git worktree add <path> <branch>` if branch exists
        # Try create-new-branch first; if it fails, fall back to existing branch.
        try:
            subprocess.run(
                ["git", "worktree", "add", "-b", branch, str(target)],
                cwd=repo_path, check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError:
            subprocess.run(
                ["git", "worktree", "add", str(target), branch],
                cwd=repo_path, check=True, capture_output=True, text=True,
            )
        return target

    def retire(self, worktree_path: Path) -> None:
        if not worktree_path.exists():
            return
        # cd to parent (the repo) — figure out where the worktree is registered
        # use `git worktree remove --force` from the worktree itself
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            check=False, capture_output=True,
        )
        # if still there (foreign worktree), nuke the dir
        if worktree_path.exists():
            import shutil
            shutil.rmtree(worktree_path, ignore_errors=True)
