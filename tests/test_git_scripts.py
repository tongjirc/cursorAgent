"""
Integration tests for shell scripts using a temporary git repo.

Each test creates a fresh temp repo (via conftest.py fixture),
runs the actual shell scripts, and verifies git state + output markers.
"""

import os
import subprocess

import pytest


def _git(repo, *args):
    result = subprocess.run(
        ["git"] + list(args),
        cwd=repo, capture_output=True, text=True,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _run_script(script_path, args, repo, timeout=30):
    """Run a shell script and return (returncode, stdout, stderr)."""
    cmd = ["bash", script_path] + args
    result = subprocess.run(
        cmd, cwd=repo, capture_output=True, text=True, timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


# ======================== execute_cherry_pick.sh ========================

class TestSingleCherryPick:
    def test_success(self, temp_repo, scripts_dir):
        """Clean cherry-pick with passing test → commit on target branch."""
        r = temp_repo
        script = os.path.join(scripts_dir, "execute_cherry_pick.sh")

        rc, out, err = _run_script(script, [
            r["ok_commit"], r["target_branch"], r["path"], "true",
        ], r["path"])

        assert rc == 0, "Expected rc=0, got {}\n{}".format(rc, out)
        assert "NO_CHANGE" not in out
        assert "CONFLICT" not in out
        assert "TEST_FAIL" not in out

        # verify: original commit message is preserved on target
        _, log_out, _ = _git(r["path"], "log", "--oneline", r["target_branch"], "-1")
        assert "add file_ok" in log_out, "Original commit message should be preserved: {}".format(log_out)

        # verify: we're back on original branch
        _, branch, _ = _git(r["path"], "rev-parse", "--abbrev-ref", "HEAD")
        assert branch == r["original_branch"]

    def test_conflict(self, temp_repo, scripts_dir):
        """Cherry-pick that conflicts → CONFLICT marker, rollback, return to original branch."""
        r = temp_repo
        script = os.path.join(scripts_dir, "execute_cherry_pick.sh")

        rc, out, err = _run_script(script, [
            r["conflict_commit"], r["target_branch"], r["path"], "true",
        ], r["path"])

        assert rc != 0
        assert "CONFLICT" in out

        # verify: back on original branch
        _, branch, _ = _git(r["path"], "rev-parse", "--abbrev-ref", "HEAD")
        assert branch == r["original_branch"]

        # verify: working tree is clean
        _, status, _ = _git(r["path"], "status", "--porcelain")
        assert status == "", "Working tree not clean after conflict rollback: {}".format(status)

    def test_test_failure(self, temp_repo, scripts_dir):
        """Cherry-pick succeeds but test fails → TEST_FAIL, rollback, return to original branch."""
        r = temp_repo
        script = os.path.join(scripts_dir, "execute_cherry_pick.sh")

        rc, out, err = _run_script(script, [
            r["ok_commit"], r["target_branch"], r["path"], "false",
        ], r["path"])

        assert rc != 0
        assert "TEST_FAIL" in out

        _, branch, _ = _git(r["path"], "rev-parse", "--abbrev-ref", "HEAD")
        assert branch == r["original_branch"]

        _, status, _ = _git(r["path"], "status", "--porcelain")
        assert status == ""

    def test_no_change(self, temp_repo, scripts_dir):
        """Cherry-pick a commit that's already on target → NO_CHANGE."""
        r = temp_repo
        script = os.path.join(scripts_dir, "execute_cherry_pick.sh")

        # First, cherry-pick ok_commit onto target
        _run_script(script, [
            r["ok_commit"], r["target_branch"], r["path"], "true",
        ], r["path"])

        # Cherry-pick the same commit again
        rc, out, err = _run_script(script, [
            r["ok_commit"], r["target_branch"], r["path"], "true",
        ], r["path"])

        assert "NO_CHANGE" in out

    def test_nonexistent_branch(self, temp_repo, scripts_dir):
        """Cherry-pick to a branch that doesn't exist → failure."""
        r = temp_repo
        script = os.path.join(scripts_dir, "execute_cherry_pick.sh")

        rc, out, err = _run_script(script, [
            r["ok_commit"], "nonexistent-branch", r["path"], "true",
        ], r["path"])

        assert rc != 0


# ======================== batch_cherry_pick.sh ========================

class TestBatchCherryPick:
    def test_success(self, temp_repo, scripts_dir):
        """Batch cherry-pick with all clean commits → SUCCESS."""
        r = temp_repo
        script = os.path.join(scripts_dir, "batch_cherry_pick.sh")

        rc, out, err = _run_script(script, [
            r["ok_commit"], r["target_branch"], r["path"], "true",
        ], r["path"])

        assert rc == 0
        assert "SUCCESS" in out

        _, branch, _ = _git(r["path"], "rev-parse", "--abbrev-ref", "HEAD")
        assert branch == r["original_branch"]

    def test_conflict_rollback(self, temp_repo, scripts_dir):
        """Batch with a conflicting commit → CONFLICT, full rollback."""
        r = temp_repo
        script = os.path.join(scripts_dir, "batch_cherry_pick.sh")

        commits = "{},{}".format(r["ok_commit"], r["conflict_commit"])
        rc, out, err = _run_script(script, [
            commits, r["target_branch"], r["path"], "true",
        ], r["path"])

        assert rc != 0
        assert "CONFLICT" in out

        _, branch, _ = _git(r["path"], "rev-parse", "--abbrev-ref", "HEAD")
        assert branch == r["original_branch"]

        _, status, _ = _git(r["path"], "status", "--porcelain")
        assert status == ""

    def test_test_failure_rollback(self, temp_repo, scripts_dir):
        """Batch cherry-pick succeeds but test fails → TEST_FAIL, full rollback."""
        r = temp_repo
        script = os.path.join(scripts_dir, "batch_cherry_pick.sh")

        rc, out, err = _run_script(script, [
            r["ok_commit"], r["target_branch"], r["path"], "false",
        ], r["path"])

        assert rc != 0
        assert "TEST_FAIL" in out

        _, branch, _ = _git(r["path"], "rev-parse", "--abbrev-ref", "HEAD")
        assert branch == r["original_branch"]


# ======================== step_cherry_pick.sh ========================

class TestStepCherryPick:
    def test_all_success(self, temp_repo, scripts_dir):
        """Step cherry-pick with one clean commit → STEP_SUCCESS."""
        r = temp_repo
        script = os.path.join(scripts_dir, "step_cherry_pick.sh")

        rc, out, err = _run_script(script, [
            r["ok_commit"], r["target_branch"], r["path"], "true",
        ], r["path"])

        assert "STEP_SUCCESS" in out
        assert "PASSED:" in out

        _, branch, _ = _git(r["path"], "rev-parse", "--abbrev-ref", "HEAD")
        assert branch == r["original_branch"]

    def test_conflict_continues(self, temp_repo, scripts_dir):
        """Step with conflict commit → conflict is reported, other commits still tried."""
        r = temp_repo
        script = os.path.join(scripts_dir, "step_cherry_pick.sh")

        commits = "{},{}".format(r["conflict_commit"], r["ok_commit"])
        rc, out, err = _run_script(script, [
            commits, r["target_branch"], r["path"], "true",
        ], r["path"])

        assert "CONFLICT:" in out

        _, branch, _ = _git(r["path"], "rev-parse", "--abbrev-ref", "HEAD")
        assert branch == r["original_branch"]

    def test_test_failure_continues(self, temp_repo, scripts_dir):
        """Step where test fails → FAILED reported, back to original branch."""
        r = temp_repo
        script = os.path.join(scripts_dir, "step_cherry_pick.sh")

        rc, out, err = _run_script(script, [
            r["ok_commit"], r["target_branch"], r["path"], "false",
        ], r["path"])

        assert "FAILED:" in out

        _, branch, _ = _git(r["path"], "rev-parse", "--abbrev-ref", "HEAD")
        assert branch == r["original_branch"]

        _, status, _ = _git(r["path"], "status", "--porcelain")
        assert status == ""
