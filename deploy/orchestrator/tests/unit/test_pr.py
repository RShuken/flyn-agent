from unittest.mock import patch, MagicMock
import pytest
from flyn_orchestrator.pr import create_pr, comment_pr, merge_pr, PRError


@patch("subprocess.run")
def test_create_pr_returns_url(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=0, stdout="https://github.com/x/y/pull/42\n", stderr="")
    url = create_pr(repo_path=tmp_path, title="t", body="b", base="main", head="feat/x")
    assert url == "https://github.com/x/y/pull/42"
    args = mock_run.call_args[0][0]
    assert "gh" in args[0]
    assert "pr" in args and "create" in args


@patch("subprocess.run")
def test_create_pr_raises_on_failure(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="auth required")
    with pytest.raises(PRError) as ex:
        create_pr(repo_path=tmp_path, title="t", body="b", base="main", head="feat/x")
    assert "auth required" in str(ex.value)


@patch("subprocess.run")
def test_comment_pr_invokes_gh(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=0, stdout="comment posted", stderr="")
    comment_pr(repo_path=tmp_path, pr_number=42, body="hi")
    args = mock_run.call_args[0][0]
    assert args[0:3] == ["gh", "pr", "comment"]
    assert "42" in args


@patch("subprocess.run")
def test_merge_pr_returns_true_on_success(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=0, stdout="merged", stderr="")
    assert merge_pr(repo_path=tmp_path, pr_number=42, method="merge") is True


@patch("subprocess.run")
def test_merge_pr_returns_false_on_failure(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="merge conflict")
    assert merge_pr(repo_path=tmp_path, pr_number=42, method="merge") is False


def test_pr_number_from_url():
    from flyn_orchestrator.pr import pr_number_from_url, PRError
    assert pr_number_from_url("https://github.com/x/y/pull/42") == 42
    assert pr_number_from_url("https://github.com/x/y/pull/3") == 3
    with pytest.raises(PRError):
        pr_number_from_url("https://github.com/x/y")
