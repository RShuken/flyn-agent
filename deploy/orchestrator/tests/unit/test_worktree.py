import subprocess
from pathlib import Path
import pytest
from flyn_orchestrator.worktree import WorktreeManager


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "src-repo"
    r.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=r, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=r, check=True)
    (r / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "."], cwd=r, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=r, check=True, capture_output=True)
    return r


def test_allocate_and_retire(tmp_path: Path, repo: Path):
    mgr = WorktreeManager(workspaces_dir=tmp_path / "ws")
    path = mgr.allocate(repo_path=repo, task_id="T-0001", branch="feat/T-0001-test")
    assert path.exists()
    assert (path / "README.md").exists()
    # retire
    mgr.retire(path)
    assert not path.exists()


def test_allocate_idempotent_for_same_task(tmp_path: Path, repo: Path):
    mgr = WorktreeManager(workspaces_dir=tmp_path / "ws")
    p1 = mgr.allocate(repo_path=repo, task_id="T-0001", branch="feat/T-0001-test")
    p2 = mgr.allocate(repo_path=repo, task_id="T-0001", branch="feat/T-0001-test")
    assert p1 == p2
