"""
Tests for revert functionality: revert.sh script and handler.
"""

import os
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import slack_listener as sl


def _git(repo, *args):
    result = subprocess.run(
        ["git"] + list(args),
        cwd=repo, capture_output=True, text=True,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _run_script(script_path, args, repo, timeout=30):
    cmd = ["bash", script_path] + args
    result = subprocess.run(
        cmd, cwd=repo, capture_output=True, text=True, timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


# ======================== Revert script ========================

class TestRevertScript:
    def test_single_revert_success(self, temp_repo, scripts_dir):
        """Revert a commit on target branch -> test passes -> push."""
        r = temp_repo
        script = os.path.join(scripts_dir, "revert.sh")

        rc, out, err = _run_script(script, [
            r["target_commit"], r["target_branch"], r["path"], "true",
        ], r["path"])

        assert rc == 0, "Expected rc=0, got {}\n{}".format(rc, out)
        assert "SUCCESS" in out
        assert "reverted" in out.lower()

        _, branch, _ = _git(r["path"], "rev-parse", "--abbrev-ref", "HEAD")
        assert branch == r["original_branch"]

    def test_revert_test_failure(self, temp_repo, scripts_dir):
        """Revert succeeds but test fails -> rollback."""
        r = temp_repo
        script = os.path.join(scripts_dir, "revert.sh")

        rc, out, err = _run_script(script, [
            r["target_commit"], r["target_branch"], r["path"], "false",
        ], r["path"])

        assert rc != 0
        assert "TEST_FAIL" in out

        _, branch, _ = _git(r["path"], "rev-parse", "--abbrev-ref", "HEAD")
        assert branch == r["original_branch"]

    def test_revert_push_fail(self, temp_repo, scripts_dir):
        """Revert succeeds, test passes, but push fails -> rollback."""
        r = temp_repo
        script = os.path.join(scripts_dir, "revert.sh")

        _git(r["path"], "remote", "remove", "origin")

        rc, out, err = _run_script(script, [
            r["target_commit"], r["target_branch"], r["path"], "true",
        ], r["path"])

        assert "PUSH_FAIL" in out

    def test_batch_revert_success(self, temp_repo, scripts_dir):
        """Revert multiple commits on target branch."""
        r = temp_repo
        script = os.path.join(scripts_dir, "revert.sh")

        # First cherry-pick ok_commit onto target to have 2 commits to revert
        cp_script = os.path.join(scripts_dir, "execute_cherry_pick.sh")
        _run_script(cp_script, [
            r["ok_commit"], r["target_branch"], r["path"], "true",
        ], r["path"])

        # Get the new commit on target
        _git(r["path"], "checkout", r["target_branch"])
        _, new_head, _ = _git(r["path"], "rev-parse", "HEAD")
        _git(r["path"], "checkout", r["original_branch"])

        commits = "{},{}".format(new_head, r["target_commit"])
        rc, out, err = _run_script(script, [
            commits, r["target_branch"], r["path"], "true",
        ], r["path"])

        assert rc == 0
        assert "SUCCESS" in out
        assert "2 commits reverted" in out


# ======================== Revert result handler ========================

class TestRevertHandler:
    @pytest.fixture
    def mock_task(self):
        say = MagicMock()
        return {
            "type": "revert", "commits": ["abc123"],
            "target_branch": "release/6.0",
            "say": say, "ts": "ts1",
            "user": "U123", "user_name": "Test User",
            "log_file": "/tmp/fake.log",
        }

    def test_success(self, mock_task):
        result = {
            "success": True,
            "output": "reverted\nGit Log (release/6.0):\n* abc -> msg\nSUCCESS\nCOMMITS:abc123",
            "passed_commits": ["abc123"],
        }
        sl.handle_revert_result(result, mock_task)
        msg = str(mock_task["say"].call_args_list[0])
        assert "Revert succeeded" in msg

    def test_conflict(self, mock_task):
        result = {
            "success": False,
            "output": "CONFLICT\nCOMMIT:abc123\nFILES:Plan.cpp",
            "is_conflict": True, "is_test_fail": False, "is_push_fail": False,
            "failed_commit": "abc123",
        }
        sl.handle_revert_result(result, mock_task)
        msg = str(mock_task["say"].call_args_list[0])
        assert "conflict" in msg.lower()

    def test_test_fail(self, mock_task):
        result = {
            "success": False,
            "output": "TEST_FAIL\nOUTPUT:error",
            "is_conflict": False, "is_test_fail": True, "is_push_fail": False,
        }
        sl.handle_revert_result(result, mock_task)
        msg = str(mock_task["say"].call_args_list[0])
        assert "test failed" in msg.lower()

    def test_push_fail(self, mock_task):
        result = {
            "success": False,
            "output": "PUSH_FAIL",
            "is_conflict": False, "is_test_fail": False, "is_push_fail": True,
        }
        sl.handle_revert_result(result, mock_task)
        msg = str(mock_task["say"].call_args_list[0])
        assert "push failed" in msg.lower()
