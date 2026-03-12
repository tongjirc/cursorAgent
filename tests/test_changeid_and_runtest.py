"""
Tests for Gerrit Change-Id resolution and run-test with branch parameter.
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


# ======================== is_gerrit_change_id ========================

class TestIsGerritChangeId:
    def test_valid_change_id(self):
        assert sl.is_gerrit_change_id("I" + "a" * 40) is True
        assert sl.is_gerrit_change_id("I" + "0123456789abcdef" * 2 + "01234567") is True

    def test_too_short(self):
        assert sl.is_gerrit_change_id("Iabc123") is False

    def test_no_prefix(self):
        assert sl.is_gerrit_change_id("a" * 41) is False

    def test_uppercase_hex(self):
        assert sl.is_gerrit_change_id("I" + "A" * 40) is False

    def test_commit_hash(self):
        assert sl.is_gerrit_change_id("abc123def456") is False

    def test_empty(self):
        assert sl.is_gerrit_change_id("") is False
        assert sl.is_gerrit_change_id(None) is False


# ======================== is_gerrit_change_number ========================

class TestIsGerritChangeNumber:
    def test_valid_numbers(self):
        assert sl.is_gerrit_change_number("766210") is True
        assert sl.is_gerrit_change_number("12345") is True
        assert sl.is_gerrit_change_number("1234567") is True

    def test_too_short(self):
        assert sl.is_gerrit_change_number("1234") is False

    def test_too_long(self):
        assert sl.is_gerrit_change_number("12345678") is False

    def test_not_digits(self):
        assert sl.is_gerrit_change_number("76621a") is False
        assert sl.is_gerrit_change_number("abc123") is False

    def test_change_id_not_number(self):
        assert sl.is_gerrit_change_number("I" + "a" * 40) is False

    def test_commit_hash_not_number(self):
        assert sl.is_gerrit_change_number("abcdef1234567890abcdef1234567890abcdef12") is False

    def test_empty(self):
        assert sl.is_gerrit_change_number("") is False
        assert sl.is_gerrit_change_number(None) is False


# ======================== resolve_change_id ========================

class TestResolveChangeId:
    def test_resolves_known_change_id(self, temp_repo):
        r = temp_repo
        original_repo = sl.REPO_PATH
        sl.REPO_PATH = r["path"]
        try:
            result = sl.resolve_change_id(r["change_id"])
            assert result == r["changeid_commit"]
        finally:
            sl.REPO_PATH = original_repo

    def test_returns_none_for_unknown(self, temp_repo):
        r = temp_repo
        original_repo = sl.REPO_PATH
        sl.REPO_PATH = r["path"]
        try:
            result = sl.resolve_change_id("I" + "f" * 40)
            assert result is None
        finally:
            sl.REPO_PATH = original_repo


# ======================== resolve_change_number ========================

class TestResolveChangeNumber:
    def test_resolves_latest_patchset(self):
        """Mock git ls-remote output to verify latest PS is picked."""
        ls_output = (
            "aaa111\trefs/changes/10/766210/1\n"
            "bbb222\trefs/changes/10/766210/2\n"
            "ccc333\trefs/changes/10/766210/3\n"
            "ddd444\trefs/changes/10/766210/4\n"
            "eee555\trefs/changes/10/766210/checks\n"
            "fff666\trefs/changes/10/766210/meta\n"
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ls_output

        with patch("subprocess.run", return_value=mock_result):
            commit = sl.resolve_change_number("766210")
            assert commit == "ddd444", "Should pick PS 4 (highest)"

    def test_returns_none_for_no_refs(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            commit = sl.resolve_change_number("999999")
            assert commit is None

    def test_single_patchset(self):
        ls_output = "abc123\trefs/changes/01/700001/1\n"
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ls_output

        with patch("subprocess.run", return_value=mock_result):
            commit = sl.resolve_change_number("700001")
            assert commit == "abc123"


# ======================== Abbreviated commit resolution ========================

class TestAbbreviatedCommit:
    def test_short_hash_resolved_to_full(self, temp_repo):
        """A 12-char abbreviated hash should resolve to the full 40-char hash."""
        r = temp_repo
        original_repo = sl.REPO_PATH
        sl.REPO_PATH = r["path"]
        try:
            short = r["ok_commit"][:12]
            say = MagicMock()
            resolved, errors = sl.resolve_refs([short], say, "ts1")
            assert errors is False
            assert len(resolved) == 1
            assert resolved[0] == r["ok_commit"]
        finally:
            sl.REPO_PATH = original_repo

    def test_7_char_hash_works(self, temp_repo):
        """git minimum abbreviation (7 chars) should work."""
        r = temp_repo
        original_repo = sl.REPO_PATH
        sl.REPO_PATH = r["path"]
        try:
            short = r["ok_commit"][:7]
            say = MagicMock()
            resolved, errors = sl.resolve_refs([short], say, "ts1")
            assert errors is False
            assert len(resolved) == 1
            assert resolved[0] == r["ok_commit"]
        finally:
            sl.REPO_PATH = original_repo

    def test_full_hash_passthrough(self, temp_repo):
        """Full 40-char hash should resolve to itself."""
        r = temp_repo
        original_repo = sl.REPO_PATH
        sl.REPO_PATH = r["path"]
        try:
            say = MagicMock()
            resolved, errors = sl.resolve_refs([r["ok_commit"]], say, "ts1")
            assert errors is False
            assert resolved == [r["ok_commit"]]
        finally:
            sl.REPO_PATH = original_repo

    def test_nonexistent_hash_errors(self, temp_repo):
        """A hash that doesn't exist should fail (even after fetch attempt)."""
        r = temp_repo
        original_repo = sl.REPO_PATH
        sl.REPO_PATH = r["path"]
        try:
            say = MagicMock()
            fake = "abcdef1234567"
            resolved, errors = sl.resolve_refs([fake], say, "ts1")
            assert errors is True
            assert resolved == []
        finally:
            sl.REPO_PATH = original_repo

    def test_mixed_short_and_change_id(self, temp_repo):
        """Mix of abbreviated commit and Change-Id."""
        r = temp_repo
        original_repo = sl.REPO_PATH
        sl.REPO_PATH = r["path"]
        try:
            short = r["ok_commit"][:12]
            say = MagicMock()
            resolved, errors = sl.resolve_refs([short, r["change_id"]], say, "ts1")
            assert errors is False
            assert len(resolved) == 2
            assert resolved[0] == r["ok_commit"]
            assert resolved[1] == r["changeid_commit"]
        finally:
            sl.REPO_PATH = original_repo


# ======================== resolve_refs ========================

class TestResolveRefs:
    def test_plain_commit_passthrough(self):
        say = MagicMock()
        resolved, errors = sl.resolve_refs(["abc123", "def456"], say, "ts1")
        assert resolved == ["abc123", "def456"]
        assert errors is False

    def test_change_id_resolved(self, temp_repo):
        r = temp_repo
        original_repo = sl.REPO_PATH
        sl.REPO_PATH = r["path"]
        try:
            say = MagicMock()
            resolved, errors = sl.resolve_refs([r["change_id"]], say, "ts1")
            assert errors is False
            assert len(resolved) == 1
            assert resolved[0] == r["changeid_commit"]
            say.assert_called()
        finally:
            sl.REPO_PATH = original_repo

    def test_unknown_change_id_errors(self, temp_repo):
        r = temp_repo
        original_repo = sl.REPO_PATH
        sl.REPO_PATH = r["path"]
        try:
            say = MagicMock()
            bad_id = "I" + "f" * 40
            resolved, errors = sl.resolve_refs([bad_id], say, "ts1")
            assert errors is True
            assert resolved == []
        finally:
            sl.REPO_PATH = original_repo

    def test_mixed_refs(self, temp_repo):
        r = temp_repo
        original_repo = sl.REPO_PATH
        sl.REPO_PATH = r["path"]
        try:
            say = MagicMock()
            resolved, errors = sl.resolve_refs(
                ["abc123", r["change_id"]], say, "ts1"
            )
            assert errors is False
            assert len(resolved) == 2
            assert resolved[0] == "abc123"
            assert resolved[1] == r["changeid_commit"]
        finally:
            sl.REPO_PATH = original_repo

    def test_change_number_resolved(self):
        """Change number should be resolved via resolve_change_number."""
        say = MagicMock()
        with patch("slack_listener.resolve_change_number", return_value="resolved_hash"):
            resolved, errors = sl.resolve_refs(["766210"], say, "ts1")
            assert errors is False
            assert resolved == ["resolved_hash"]
            say.assert_called()

    def test_change_number_not_found(self):
        say = MagicMock()
        with patch("slack_listener.resolve_change_number", return_value=None):
            resolved, errors = sl.resolve_refs(["999999"], say, "ts1")
            assert errors is True
            assert resolved == []

    def test_all_three_types_mixed(self, temp_repo):
        """Mix of commit hash, Change-Id, and change number."""
        r = temp_repo
        original_repo = sl.REPO_PATH
        sl.REPO_PATH = r["path"]
        try:
            say = MagicMock()
            with patch("slack_listener.resolve_change_number", return_value="num_resolved"):
                resolved, errors = sl.resolve_refs(
                    ["abc123", r["change_id"], "766210"], say, "ts1"
                )
                assert errors is False
                assert len(resolved) == 3
                assert resolved[0] == "abc123"
                assert resolved[1] == r["changeid_commit"]
                assert resolved[2] == "num_resolved"
        finally:
            sl.REPO_PATH = original_repo


# ======================== run-test with branch ========================

class TestRunTestWithBranch:
    def test_run_test_on_target_branch(self, temp_repo, scripts_dir):
        """run-test on a specific branch should switch, test, switch back."""
        r = temp_repo
        script = os.path.join(scripts_dir, "execute_cherry_pick.sh")

        original_repo = sl.REPO_PATH
        original_init = sl.SHELL_INIT
        original_test = sl.TEST_COMMAND
        sl.REPO_PATH = r["path"]
        sl.SHELL_INIT = "true"
        sl.TEST_COMMAND = "true"

        try:
            log_file = os.path.join(r["path"], "test.log")
            result = sl.run_test_only(log_file, target_branch=r["test_branch"])

            assert result["success"] is True
            assert result["branch"] == r["test_branch"]

            _, current, _ = _git(r["path"], "rev-parse", "--abbrev-ref", "HEAD")
            assert current == r["original_branch"], \
                "Should switch back to original branch, got {}".format(current)
        finally:
            sl.REPO_PATH = original_repo
            sl.SHELL_INIT = original_init
            sl.TEST_COMMAND = original_test

    def test_run_test_on_current_branch(self, temp_repo):
        """run-test without branch should test on current branch."""
        r = temp_repo

        original_repo = sl.REPO_PATH
        original_init = sl.SHELL_INIT
        original_test = sl.TEST_COMMAND
        sl.REPO_PATH = r["path"]
        sl.SHELL_INIT = "true"
        sl.TEST_COMMAND = "true"

        try:
            log_file = os.path.join(r["path"], "test.log")
            result = sl.run_test_only(log_file)

            assert result["success"] is True
            assert result["branch"] == r["original_branch"]
        finally:
            sl.REPO_PATH = original_repo
            sl.SHELL_INIT = original_init
            sl.TEST_COMMAND = original_test

    def test_run_test_failure_on_branch(self, temp_repo):
        """run-test with failing test should return failure and switch back."""
        r = temp_repo

        original_repo = sl.REPO_PATH
        original_init = sl.SHELL_INIT
        original_test = sl.TEST_COMMAND
        sl.REPO_PATH = r["path"]
        sl.SHELL_INIT = "true"
        sl.TEST_COMMAND = "false"

        try:
            log_file = os.path.join(r["path"], "test.log")
            result = sl.run_test_only(log_file, target_branch=r["test_branch"])

            assert result["success"] is False
            assert result["branch"] == r["test_branch"]

            _, current, _ = _git(r["path"], "rev-parse", "--abbrev-ref", "HEAD")
            assert current == r["original_branch"], \
                "Should switch back even on failure, got {}".format(current)
        finally:
            sl.REPO_PATH = original_repo
            sl.SHELL_INIT = original_init
            sl.TEST_COMMAND = original_test

    def test_run_test_nonexistent_branch(self, temp_repo):
        """run-test on nonexistent branch should fail."""
        r = temp_repo

        original_repo = sl.REPO_PATH
        original_init = sl.SHELL_INIT
        original_test = sl.TEST_COMMAND
        sl.REPO_PATH = r["path"]
        sl.SHELL_INIT = "true"
        sl.TEST_COMMAND = "true"

        try:
            log_file = os.path.join(r["path"], "test.log")
            result = sl.run_test_only(log_file, target_branch="nonexistent")

            assert result["success"] is False
        finally:
            sl.REPO_PATH = original_repo
            sl.SHELL_INIT = original_init
            sl.TEST_COMMAND = original_test


# ======================== Cherry-pick with Change-Id (integration) ========================

class TestCherryPickWithChangeId:
    def test_single_cp_with_change_id(self, temp_repo, scripts_dir):
        """Cherry-pick using a Change-Id should resolve and succeed."""
        r = temp_repo

        original_repo = sl.REPO_PATH
        original_init = sl.SHELL_INIT
        original_test = sl.TEST_COMMAND
        original_scripts = sl.SCRIPTS_DIR
        sl.REPO_PATH = r["path"]
        sl.SHELL_INIT = "true"
        sl.TEST_COMMAND = "true"
        sl.SCRIPTS_DIR = scripts_dir

        try:
            commit = sl.resolve_change_id(r["change_id"])
            assert commit is not None

            import tempfile
            log_file = os.path.join(tempfile.gettempdir(), "cpbot_test_cp.log")
            result = sl.run_cherry_pick(commit, r["target_branch"], log_file)
            assert result["success"] is True
        finally:
            sl.REPO_PATH = original_repo
            sl.SHELL_INIT = original_init
            sl.TEST_COMMAND = original_test
            sl.SCRIPTS_DIR = original_scripts
