"""
Shared fixtures for cherry-pick bot tests.

Creates a temporary git repo with known branches and commits
that can be cherry-picked, conflicted, etc.
"""

import os
import shutil
import subprocess
import tempfile

import pytest


def _git(repo, *args):
    """Run a git command in the given repo, return stdout."""
    result = subprocess.run(
        ["git"] + list(args),
        cwd=repo, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("git {} failed: {}".format(
            " ".join(args), result.stderr))
    return result.stdout.strip()


@pytest.fixture
def temp_repo(tmp_path):
    """
    Create a temporary git repo with this structure:

        main:    init -> A (file_a.txt)
        target:  init -> B (file_b.txt)   (branched from init)

    Commits available:
        ok_commit      - adds file_ok.txt on main (no conflict with target)
        conflict_commit - modifies file_b.txt on main (conflicts with target)

    Returns dict with repo path, commit hashes, and branch names.
    """
    # Create a bare repo to act as "origin"
    bare = str(tmp_path / "origin.git")
    os.makedirs(bare)
    _git(bare, "init", "--bare", "-b", "main")

    repo = str(tmp_path / "repo")
    os.makedirs(repo)

    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "remote", "add", "origin", bare)

    with open(os.path.join(repo, "file_a.txt"), "w") as f:
        f.write("initial content\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")

    _git(repo, "checkout", "-b", "target")
    with open(os.path.join(repo, "file_b.txt"), "w") as f:
        f.write("target branch content\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "B: add file_b on target")
    target_commit = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "main")

    with open(os.path.join(repo, "file_ok.txt"), "w") as f:
        f.write("this file won't clash\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add file_ok.txt")
    ok_commit = _git(repo, "rev-parse", "HEAD")

    with open(os.path.join(repo, "file_b.txt"), "w") as f:
        f.write("clashing content from main\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "modify file_b.txt on main")
    conflict_commit = _git(repo, "rev-parse", "HEAD")

    # Commit with a Gerrit-style Change-Id in the message
    with open(os.path.join(repo, "file_changeid.txt"), "w") as f:
        f.write("change-id test\n")
    _git(repo, "add", "-A")
    change_id = "I" + "a1b2c3d4e5" * 4
    _git(repo, "commit", "-m", "add file_changeid\n\nChange-Id: {}".format(change_id))
    changeid_commit = _git(repo, "rev-parse", "HEAD")

    # Create a test_branch with a simple test file for run-test
    _git(repo, "checkout", "-b", "test_branch")
    with open(os.path.join(repo, "test_file.txt"), "w") as f:
        f.write("test branch content\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add test_file on test_branch")

    _git(repo, "checkout", "main")

    # Push all branches to origin so push works in tests
    _git(repo, "push", "origin", "main")
    _git(repo, "push", "origin", "target")
    _git(repo, "push", "origin", "test_branch")

    return {
        "path": repo,
        "ok_commit": ok_commit,
        "conflict_commit": conflict_commit,
        "target_commit": target_commit,
        "changeid_commit": changeid_commit,
        "change_id": change_id,
        "target_branch": "target",
        "test_branch": "test_branch",
        "original_branch": "main",
    }


@pytest.fixture
def scripts_dir():
    """Path to the scripts/ directory."""
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts")


@pytest.fixture
def task_log_dir(tmp_path):
    """Temporary directory for task logs."""
    d = str(tmp_path / "task_logs")
    os.makedirs(d)
    return d
