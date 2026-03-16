"""
Tests for AI suggestion integration in result handlers.

Mocks analyze_conflict / analyze_test_failure to verify:
1. AI is called on conflict / test failure for all task types
2. AI suggestion is sent via say() when available
3. No AI message when AI returns None
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import slack_listener as sl


@pytest.fixture
def mock_task():
    """Build a fake task with a mock say()."""
    def make(task_type="single", commits=None, target_branch="release/6.0"):
        say = MagicMock()
        return {
            "type": task_type,
            "commits": commits or ["abc123"],
            "target_branch": target_branch,
            "say": say,
            "ts": "1234567890.123456",
            "user": "U123",
            "user_name": "Test User",
            "log_file": "/tmp/fake.log",
        }
    return make


# ======================== Single Cherry-Pick ========================

class TestSingleAI:
    @patch("slack_listener.analyze_conflict", return_value="Suggestion: keep both changes")
    def test_conflict_triggers_ai(self, mock_ai, mock_task):
        task = mock_task()
        result = {
            "success": False, "output": "CONFLICT\nFILES:Plan.cpp",
            "is_conflict": True, "is_test_fail": False, "is_no_change": False,
        }
        sl.handle_single_result(result, task)

        mock_ai.assert_called_once()
        calls = task["say"].call_args_list
        ai_msg = [c for c in calls if "AI analysis" in str(c)]
        assert len(ai_msg) == 1, "Should send AI suggestion for conflict"
        assert "keep both changes" in str(ai_msg[0])

    @patch("slack_listener.analyze_conflict", return_value=None)
    def test_conflict_no_ai_when_none(self, mock_ai, mock_task):
        task = mock_task()
        result = {
            "success": False, "output": "CONFLICT\nFILES:Plan.cpp",
            "is_conflict": True, "is_test_fail": False, "is_no_change": False,
        }
        sl.handle_single_result(result, task)

        mock_ai.assert_called_once()
        calls = task["say"].call_args_list
        ai_msg = [c for c in calls if "AI analysis" in str(c)]
        assert len(ai_msg) == 0, "Should not send AI message when analyze returns None"

    @patch("slack_listener.analyze_test_failure", return_value="Fix: check import path")
    def test_test_fail_triggers_ai(self, mock_ai, mock_task):
        task = mock_task()
        result = {
            "success": False, "output": "TEST_FAIL\nOUTPUT:AssertionError",
            "is_conflict": False, "is_test_fail": True, "is_no_change": False,
        }
        sl.handle_single_result(result, task)

        mock_ai.assert_called_once()
        calls = task["say"].call_args_list
        ai_msg = [c for c in calls if "AI analysis" in str(c)]
        assert len(ai_msg) == 1
        assert "check import path" in str(ai_msg[0])

    @patch("slack_listener.analyze_test_failure", return_value=None)
    def test_test_fail_no_ai_when_none(self, mock_ai, mock_task):
        task = mock_task()
        result = {
            "success": False, "output": "TEST_FAIL\nOUTPUT:error",
            "is_conflict": False, "is_test_fail": True, "is_no_change": False,
        }
        sl.handle_single_result(result, task)

        mock_ai.assert_called_once()
        calls = task["say"].call_args_list
        ai_msg = [c for c in calls if "AI analysis" in str(c)]
        assert len(ai_msg) == 0


# ======================== Batch Cherry-Pick ========================

class TestBatchAI:
    @patch("slack_listener.analyze_conflict", return_value="Batch conflict: rebase and retry")
    def test_conflict_triggers_ai(self, mock_ai, mock_task):
        task = mock_task("batch", ["abc", "def"])
        result = {
            "success": False, "output": "CONFLICT\nCOMMIT:def\nFILES:Plan.cpp",
            "is_conflict": True, "is_test_fail": False,
        }
        sl.handle_batch_result(result, task)

        mock_ai.assert_called_once()
        calls = task["say"].call_args_list
        ai_msg = [c for c in calls if "AI analysis" in str(c)]
        assert len(ai_msg) == 1, "Batch conflict should trigger AI suggestion"
        assert "rebase and retry" in str(ai_msg[0])

    @patch("slack_listener.analyze_conflict", return_value=None)
    def test_conflict_no_ai_when_none(self, mock_ai, mock_task):
        task = mock_task("batch", ["abc", "def"])
        result = {
            "success": False, "output": "CONFLICT\nCOMMIT:def\nFILES:Plan.cpp",
            "is_conflict": True, "is_test_fail": False,
        }
        sl.handle_batch_result(result, task)

        mock_ai.assert_called_once()
        calls = task["say"].call_args_list
        ai_msg = [c for c in calls if "AI analysis" in str(c)]
        assert len(ai_msg) == 0

    @patch("slack_listener.analyze_test_failure", return_value="Batch test fix suggestion")
    def test_test_fail_triggers_ai(self, mock_ai, mock_task):
        task = mock_task("batch", ["abc", "def"])
        result = {
            "success": False, "output": "TEST_FAIL\nOUTPUT:error",
            "is_conflict": False, "is_test_fail": True,
        }
        sl.handle_batch_result(result, task)

        mock_ai.assert_called_once()
        calls = task["say"].call_args_list
        ai_msg = [c for c in calls if "AI analysis" in str(c)]
        assert len(ai_msg) == 1


# ======================== Step Cherry-Pick ========================

class TestStepAI:
    @patch("slack_listener.analyze_conflict", return_value="Step conflict suggestion")
    @patch("slack_listener.analyze_test_failure", return_value=None)
    def test_conflict_only(self, mock_test_ai, mock_conflict_ai, mock_task):
        task = mock_task("step", ["abc", "def"])
        result = {
            "success": False, "output": "STEP_PARTIAL\nPASSED:abc\nFAILED:\nCONFLICT:def",
            "is_partial": True,
            "passed_commits": ["abc"],
            "failed_commits": [],
            "conflict_commits": ["def"],
        }
        sl.handle_step_result(result, task)

        mock_conflict_ai.assert_called_once()
        mock_test_ai.assert_not_called()
        calls = task["say"].call_args_list
        ai_msg = [c for c in calls if "AI analysis" in str(c)]
        assert len(ai_msg) == 1

    @patch("slack_listener.analyze_conflict", return_value=None)
    @patch("slack_listener.analyze_test_failure", return_value="Step test fix")
    def test_test_fail_only(self, mock_test_ai, mock_conflict_ai, mock_task):
        task = mock_task("step", ["abc", "def"])
        result = {
            "success": False, "output": "STEP_PARTIAL\nPASSED:abc\nFAILED:def\nCONFLICT:",
            "is_partial": True,
            "passed_commits": ["abc"],
            "failed_commits": ["def"],
            "conflict_commits": [],
        }
        sl.handle_step_result(result, task)

        mock_conflict_ai.assert_not_called()
        mock_test_ai.assert_called_once()
        calls = task["say"].call_args_list
        ai_msg = [c for c in calls if "AI analysis" in str(c)]
        assert len(ai_msg) == 1

    @patch("slack_listener.analyze_conflict", return_value="Conflict fix advice")
    @patch("slack_listener.analyze_test_failure", return_value="Test fix advice")
    def test_both_conflict_and_test_fail(self, mock_test_ai, mock_conflict_ai, mock_task):
        """When step has both conflicts and test failures, both AI analyses fire."""
        task = mock_task("step", ["a", "b", "c"])
        result = {
            "success": False, "output": "STEP_PARTIAL\nPASSED:a\nFAILED:b\nCONFLICT:c",
            "is_partial": True,
            "passed_commits": ["a"],
            "failed_commits": ["b"],
            "conflict_commits": ["c"],
        }
        sl.handle_step_result(result, task)

        mock_conflict_ai.assert_called_once()
        mock_test_ai.assert_called_once()
        calls = task["say"].call_args_list
        ai_msgs = [c for c in calls if "AI analysis" in str(c)]
        assert len(ai_msgs) == 2, "Both conflict and test-fail AI analyses should fire"

    @patch("slack_listener.analyze_conflict", return_value="All-fail conflict advice")
    @patch("slack_listener.analyze_test_failure", return_value="All-fail test advice")
    def test_all_failed(self, mock_test_ai, mock_conflict_ai, mock_task):
        """When all commits fail (not partial), AI still fires."""
        task = mock_task("step", ["a", "b"])
        result = {
            "success": False, "output": "STEP_ALL_FAILED\nPASSED:\nFAILED:a\nCONFLICT:b",
            "is_partial": False,
            "passed_commits": [],
            "failed_commits": ["a"],
            "conflict_commits": ["b"],
        }
        sl.handle_step_result(result, task)

        mock_conflict_ai.assert_called_once()
        mock_test_ai.assert_called_once()


# ======================== Run-Test ========================

class TestRunTestAI:
    @patch("slack_listener.analyze_test_failure", return_value="Run-test fix suggestion")
    def test_failure_triggers_ai(self, mock_ai, mock_task):
        task = mock_task("test", [])
        result = {
            "success": False, "output": "FAILED test_xxx", "branch": "main",
        }
        sl.handle_test_result(result, task)

        mock_ai.assert_called_once()
        calls = task["say"].call_args_list
        ai_msg = [c for c in calls if "AI analysis" in str(c)]
        assert len(ai_msg) == 1

    @patch("slack_listener.analyze_test_failure", return_value=None)
    def test_failure_no_ai_when_none(self, mock_ai, mock_task):
        task = mock_task("test", [])
        result = {
            "success": False, "output": "FAILED test_xxx", "branch": "main",
        }
        sl.handle_test_result(result, task)

        mock_ai.assert_called_once()
        calls = task["say"].call_args_list
        ai_msg = [c for c in calls if "AI analysis" in str(c)]
        assert len(ai_msg) == 0

    def test_success_no_ai(self, mock_task):
        """Success should not trigger AI analysis at all."""
        task = mock_task("test", [])
        result = {
            "success": True, "output": "ALL PASSED", "branch": "main",
        }
        with patch("slack_listener.analyze_test_failure") as mock_ai:
            sl.handle_test_result(result, task)
            mock_ai.assert_not_called()


# ======================== Prompt formatting ========================

class TestPromptFormatting:
    def test_analyze_conflict_prompt(self):
        with patch("slack_listener.analyze_with_ai") as mock:
            mock.return_value = None
            sl.analyze_conflict("Plan.cpp, BUILD", "some output tail")
            prompt = mock.call_args[0][0]
            assert "Plan.cpp" in prompt
            assert "conflict" in prompt.lower()
            assert "accept" in prompt.lower() or "current" in prompt.lower()

    def test_analyze_test_failure_prompt(self):
        with patch("slack_listener.analyze_with_ai") as mock:
            mock.return_value = None
            sl.analyze_test_failure("AssertionError: expected True")
            prompt = mock.call_args[0][0]
            assert "AssertionError" in prompt
            assert "fix" in prompt.lower() or "cause" in prompt.lower()

    def test_long_output_truncated(self):
        with patch("slack_listener.analyze_with_ai") as mock:
            mock.return_value = None
            long_output = "\n".join("line {}".format(i) for i in range(500))
            long_output += "\nFAILED test_something"
            sl.analyze_test_failure(long_output)
            prompt = mock.call_args[0][0]
            assert prompt.count("\n") <= 50, "Prompt should truncate to ~40 lines of output"
