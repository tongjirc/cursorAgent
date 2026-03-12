"""
Tests covering gaps: push fail revert, _extract_conflict_diff, _extract_git_log,
push_fail handlers, target branch auto-fetch.
"""

import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

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


# ======================== _extract_conflict_diff ========================

class TestExtractConflictDiff:
    def test_extracts_diff_between_markers(self):
        output = (
            "some stuff\n"
            "CONFLICT_DIFF_START\n"
            "diff --cc file.cpp\n"
            "<<<<<<< HEAD\n"
            "current code\n"
            "=======\n"
            "incoming code\n"
            ">>>>>>> abc123\n"
            "CONFLICT_DIFF_END\n"
            "more stuff\n"
        )
        diff = sl._extract_conflict_diff(output)
        assert "<<<<<<< HEAD" in diff
        assert "current code" in diff
        assert "incoming code" in diff
        assert "CONFLICT_DIFF_START" not in diff
        assert "CONFLICT_DIFF_END" not in diff
        assert "some stuff" not in diff
        assert "more stuff" not in diff

    def test_empty_when_no_markers(self):
        assert sl._extract_conflict_diff("no markers here") == ""

    def test_empty_input(self):
        assert sl._extract_conflict_diff("") == ""
        assert sl._extract_conflict_diff(None) == ""


# ======================== _extract_git_log ========================

class TestExtractGitLog:
    def test_extracts_git_log_section(self):
        output = (
            "Tests passed\n"
            "Push succeeded\n"
            "Git Log (sandbox/xxx):\n"
            "* abc123 - (HEAD) some commit (5 min ago) <Author>\n"
            "* def456 - another commit (10 min ago) <Author>\n"
            "STEP_SUCCESS\n"
            "PASSED:abc,def\n"
        )
        git_log = sl._extract_git_log(output)
        assert "Git Log" in git_log
        assert "abc123" in git_log
        assert "def456" in git_log
        assert "STEP_SUCCESS" not in git_log

    def test_empty_when_no_git_log(self):
        assert sl._extract_git_log("no git log here") == ""

    def test_empty_input(self):
        assert sl._extract_git_log("") == ""
        assert sl._extract_git_log(None) == ""


# ======================== Push fail handlers ========================

class TestPushFailHandlers:
    @pytest.fixture
    def mock_task(self):
        say = MagicMock()
        return {
            "type": "single", "commits": ["abc123"],
            "target_branch": "release/6.0",
            "say": say, "ts": "ts1",
            "user": "U123", "user_name": "Test User",
            "log_file": "/tmp/fake.log",
        }

    def test_single_push_fail(self, mock_task):
        result = {
            "success": False, "output": "PUSH_FAIL\nfatal: rejected",
            "is_conflict": False, "is_test_fail": False,
            "is_no_change": False, "is_push_fail": True,
        }
        sl.handle_single_result(result, mock_task)
        calls = mock_task["say"].call_args_list
        msg = str(calls[0])
        assert "Push failed" in msg

    def test_batch_push_fail(self):
        say = MagicMock()
        task = {
            "type": "batch", "commits": ["abc", "def"],
            "target_branch": "release/6.0",
            "say": say, "ts": "ts1",
            "user": "U123", "user_name": "Test User",
        }
        result = {
            "success": False, "output": "PUSH_FAIL\nfatal: rejected",
            "is_conflict": False, "is_test_fail": False,
            "is_push_fail": True,
        }
        sl.handle_batch_result(result, task)
        calls = say.call_args_list
        msg = str(calls[0])
        assert "push failed" in msg.lower()

    def test_step_push_failed_commits(self):
        say = MagicMock()
        task = {
            "type": "step", "commits": ["abc", "def"],
            "target_branch": "release/6.0",
            "say": say, "ts": "ts1",
            "user": "U123", "user_name": "Test User",
        }
        result = {
            "success": False, "output": "STEP_PARTIAL\nPASSED:abc\nPUSH_FAILED:def",
            "is_partial": True,
            "passed_commits": ["abc"],
            "failed_commits": [],
            "conflict_commits": [],
            "push_failed_commits": ["def"],
        }
        sl.handle_step_result(result, task)
        calls = say.call_args_list
        full_msg = " ".join(str(c) for c in calls)
        assert "Push failed" in full_msg
        assert "def" in full_msg


# ======================== Push fail + revert in scripts ========================

class TestScriptPushFailRevert:
    def test_single_push_fail_reverts(self, temp_repo, scripts_dir):
        """When push fails, local commit should be reverted."""
        r = temp_repo
        script = os.path.join(scripts_dir, "execute_cherry_pick.sh")

        # Remove origin so push will fail
        _git(r["path"], "remote", "remove", "origin")

        # Record HEAD before
        _, head_before, _ = _git(r["path"], "log", "--oneline", "target", "-1")

        rc, out, err = _run_script(script, [
            r["ok_commit"], r["target_branch"], r["path"], "true",
        ], r["path"])

        assert "PUSH_FAIL" in out
        assert rc != 0

        # Verify commit was reverted: target branch HEAD should be same as before
        _git(r["path"], "checkout", r["target_branch"])
        _, head_after, _ = _git(r["path"], "log", "--oneline", "-1")
        assert head_before == head_after, \
            "Local commit should be reverted after push fail"

    def test_batch_push_fail_reverts(self, temp_repo, scripts_dir):
        """Batch push fail should revert all commits."""
        r = temp_repo
        script = os.path.join(scripts_dir, "batch_cherry_pick.sh")

        _git(r["path"], "remote", "remove", "origin")

        _, head_before, _ = _git(r["path"], "log", "--oneline", "target", "-1")

        rc, out, err = _run_script(script, [
            r["ok_commit"], r["target_branch"], r["path"], "true",
        ], r["path"])

        assert "PUSH_FAIL" in out

        _git(r["path"], "checkout", r["target_branch"])
        _, head_after, _ = _git(r["path"], "log", "--oneline", "-1")
        assert head_before == head_after

    def test_step_push_fail_reverts_single(self, temp_repo, scripts_dir):
        """Step push fail should revert that commit but continue."""
        r = temp_repo
        script = os.path.join(scripts_dir, "step_cherry_pick.sh")

        _git(r["path"], "remote", "remove", "origin")

        rc, out, err = _run_script(script, [
            r["ok_commit"], r["target_branch"], r["path"], "true",
        ], r["path"])

        assert "PUSH_FAILED" in out
        assert "STEP_ALL_FAILED" in out


# ======================== Target branch auto-fetch ========================

class TestTargetBranchFetch:
    def test_checkout_remote_only_branch(self, temp_repo, scripts_dir):
        """If target branch only exists on origin, script should fetch and checkout."""
        r = temp_repo
        script = os.path.join(scripts_dir, "execute_cherry_pick.sh")

        # Create a branch only on origin (not locally)
        bare = os.path.join(os.path.dirname(r["path"]), "origin.git")
        _git(r["path"], "push", "origin", "target:remote_only_branch")
        _git(r["path"], "branch", "-D", "target")  # delete local

        # Verify target doesn't exist locally
        rc_check, _, _ = _git(r["path"], "rev-parse", "--verify", "target")
        assert rc_check != 0, "target should not exist locally"

        # Script should fetch and checkout
        rc, out, err = _run_script(script, [
            r["ok_commit"], "target", r["path"], "true",
        ], r["path"])

        assert "after fetch" in out or "Switched to target" in out
