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
        # Step 1: Prune stale worktree registrations
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=repo_path, check=False, capture_output=True,
        )
        # Step 2: If the branch exists but has no live worktree, force-delete it
        try:
            result = subprocess.run(
                ["git", "branch", "--list", branch],
                cwd=repo_path, check=True, capture_output=True, text=True,
            )
            if result.stdout.strip():
                # Branch exists — check if any worktree uses it
                wt_list = subprocess.run(
                    ["git", "worktree", "list", "--porcelain"],
                    cwd=repo_path, check=False, capture_output=True, text=True,
                ).stdout
                if f"branch refs/heads/{branch}" not in wt_list:
                    # Orphan branch — force-delete
                    subprocess.run(
                        ["git", "branch", "-D", branch],
                        cwd=repo_path, check=False, capture_output=True,
                    )
        except subprocess.CalledProcessError:
            pass  # If git branch lookup fails, fall through to add
        # Step 3: Attempt worktree add (first as new branch, then as existing)
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
