"""Tests for the commit-range mode of scripts/security-sweep.py.

The sweep is the repo's PII gate. Its working-tree mode can only see the final
files, so these tests pin the cases that made the pre-push hook and CI miss a
leak that an intermediate commit published and a later commit redacted:
a redacted value in history, filenames containing spaces, merge commits (which
can add content while resolving a conflict), and merge commit messages.

Each test builds a throwaway git repository and runs the script as a
subprocess, the same way the hook and CI invoke it.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SWEEP = REPO_ROOT / "scripts" / "security-sweep.py"

# 32 hex chars, not composed of the sweep's synthetic fixture blocks, so it is
# treated as identity material. Built at runtime: writing the value literally
# would make this test file a finding for the repo's own sweep. "cafe" is only
# 4 hex chars, below the sweep's 8-char minimum, so the source stays clean.
LEAK = "cafe" * 8


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=check
    )


def sweep(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SWEEP), *args], cwd=repo, capture_output=True, text=True
    )


def commit(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "sweep@example.invalid")
    git(path, "config", "user.name", "Sweep Test")
    return path


def test_commit_scan_catches_value_redacted_in_later_commit(repo: Path) -> None:
    (repo / "doc.md").write_text(f"peer: {LEAK}\n")
    commit(repo, "add doc")
    (repo / "doc.md").write_text("peer: <peer-A>\n")
    commit(repo, "redact")

    result = sweep(repo, "--commits", "HEAD", "--not", "--remotes")

    assert result.returncode == 1, result.stdout
    assert "doc.md" in result.stdout


def test_tree_scan_stays_clean_after_redaction(repo: Path) -> None:
    """The gap the commit mode exists to close: the final tree is clean."""
    (repo / "doc.md").write_text(f"peer: {LEAK}\n")
    commit(repo, "add doc")
    (repo / "doc.md").write_text("peer: <peer-A>\n")
    commit(repo, "redact")

    assert sweep(repo).returncode == 0


def test_filename_with_spaces_is_scanned(repo: Path) -> None:
    (repo / "private notes.md").write_text(f"peer: {LEAK}\n")
    commit(repo, "add notes")
    (repo / "private notes.md").write_text("peer: <peer-A>\n")
    commit(repo, "redact")

    result = sweep(repo, "--commits", "HEAD", "--not", "--remotes")

    assert result.returncode == 1, result.stdout
    assert "private notes.md" in result.stdout


def test_merge_commit_content_is_scanned(repo: Path) -> None:
    (repo / "base.md").write_text("safe\n")
    commit(repo, "base")
    git(repo, "checkout", "-b", "feat")
    (repo / "notes.md").write_text(f"peer: {LEAK}\n")
    commit(repo, "feature")
    git(repo, "checkout", "main")
    (repo / "other.md").write_text("other\n")
    commit(repo, "other")
    git(repo, "merge", "--no-ff", "feat", "-m", "merge feature")
    (repo / "notes.md").write_text("peer: <peer-A>\n")
    commit(repo, "redact")

    result = sweep(repo, "--commits", "HEAD", "--not", "--remotes")

    assert result.returncode == 1, result.stdout
    assert "notes.md" in result.stdout


def test_evil_merge_resolution_is_scanned(repo: Path) -> None:
    """Conflict resolution can add a value neither parent has."""
    (repo / "f.txt").write_text("A\n")
    commit(repo, "base")
    git(repo, "checkout", "-b", "b1")
    (repo / "f.txt").write_text("B\n")
    commit(repo, "b1")
    git(repo, "checkout", "main")
    git(repo, "checkout", "-b", "b2")
    (repo / "f.txt").write_text("C\n")
    commit(repo, "b2")
    git(repo, "checkout", "b1")
    conflicted = git(repo, "merge", "b2", check=False)
    assert conflicted.returncode != 0, "expected a conflict to resolve"
    (repo / "f.txt").write_text(f"peer: {LEAK}\n")
    commit(repo, "resolve conflict")
    (repo / "f.txt").write_text("peer: <peer-A>\n")
    commit(repo, "redact")

    result = sweep(repo, "--commits", "HEAD", "--not", "--remotes")

    assert result.returncode == 1, result.stdout
    assert "f.txt" in result.stdout


def test_merge_commit_message_is_scanned(repo: Path) -> None:
    (repo / "a.md").write_text("a\n")
    commit(repo, "base")
    git(repo, "checkout", "-b", "feat")
    (repo / "b.md").write_text("b\n")
    commit(repo, "feat")
    git(repo, "checkout", "main")
    git(repo, "merge", "--no-ff", "feat", "-m", f"merge feature {LEAK}")

    result = sweep(repo, "--commits", "HEAD", "--not", "--remotes")

    assert result.returncode == 1, result.stdout
    assert "commit-message" in result.stdout


def test_clean_history_passes(repo: Path) -> None:
    (repo / "a.md").write_text("safe\n")
    commit(repo, "base")

    assert sweep(repo, "--commits", "HEAD", "--not", "--remotes").returncode == 0
