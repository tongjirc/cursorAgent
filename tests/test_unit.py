"""
Unit tests for pure functions in slack_listener.py.
No git or Slack dependencies needed.
"""

import os
import sys
import time
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import slack_listener as sl


class TestExtractCommits:
    def test_comma_separated(self):
        output = "COMMITS:abc123,def456,ghi789\n"
        assert sl.extract_commits(output, "COMMITS:") == ["abc123", "def456", "ghi789"]

    def test_space_separated(self):
        output = "COMMITS:abc123 def456 ghi789\n"
        assert sl.extract_commits(output, "COMMITS:") == ["abc123", "def456", "ghi789"]

    def test_mixed_separators(self):
        output = "PASSED:abc123, def456 ghi789\n"
        assert sl.extract_commits(output, "PASSED:") == ["abc123", "def456", "ghi789"]

    def test_single_commit(self):
        output = "COMMITS:abc123\n"
        assert sl.extract_commits(output, "COMMITS:") == ["abc123"]

    def test_no_match(self):
        assert sl.extract_commits("no match here", "COMMITS:") == []

    def test_empty_output(self):
        assert sl.extract_commits("", "COMMITS:") == []
        assert sl.extract_commits(None, "COMMITS:") == []

    def test_prefix_with_special_chars(self):
        output = "FAILED:abc123\n"
        assert sl.extract_commits(output, "FAILED:") == ["abc123"]

    def test_multiline_only_matches_first(self):
        output = "COMMITS:aaa\nCOMMITS:bbb\n"
        assert sl.extract_commits(output, "COMMITS:") == ["aaa"]


class TestExtractSingleValue:
    def test_basic(self):
        assert sl.extract_single_value("FILES:foo.py bar.py\n", "FILES:") == "foo.py bar.py"

    def test_no_match(self):
        assert sl.extract_single_value("nothing here", "FILES:") is None

    def test_empty(self):
        assert sl.extract_single_value("", "FILES:") is None
        assert sl.extract_single_value(None, "FILES:") is None


class TestQuote:
    def test_simple(self):
        assert sl._quote("hello") == "'hello'"

    def test_with_single_quotes(self):
        assert sl._quote("it's") == "'it'\\''s'"

    def test_with_spaces(self):
        assert sl._quote("hello world") == "'hello world'"

    def test_empty(self):
        assert sl._quote("") == "''"


class TestGetOutputTail:
    def test_short_output(self):
        assert sl.get_output_tail("line1\nline2\nline3", 5) == "line1\nline2\nline3"

    def test_long_output(self):
        lines = "\n".join("line{}".format(i) for i in range(100))
        tail = sl.get_output_tail(lines, 3)
        assert tail == "line97\nline98\nline99"

    def test_empty(self):
        assert sl.get_output_tail("") == ""
        assert sl.get_output_tail(None) == ""


class TestFmtElapsed:
    def test_seconds(self):
        assert sl._fmt_elapsed(5) == "5s"
        assert sl._fmt_elapsed(59) == "59s"

    def test_minutes(self):
        assert sl._fmt_elapsed(65) == "1m5s"
        assert sl._fmt_elapsed(3599) == "59m59s"

    def test_hours(self):
        assert sl._fmt_elapsed(3661) == "1h1m"
        assert sl._fmt_elapsed(7200) == "2h0m"


class TestMakeTaskLogPath:
    def test_with_info(self):
        path = sl.make_task_log_path("single", "abc123")
        assert "single" in path
        assert "abc123" in path
        assert path.endswith(".log")

    def test_without_info(self):
        path = sl.make_task_log_path("test")
        assert "test" in path
        assert path.endswith(".log")

    def test_sanitizes_special_chars(self):
        path = sl.make_task_log_path("batch", "abc/def:ghi")
        basename = os.path.basename(path)
        assert "/" not in basename.replace(os.sep, "")
        assert ":" not in basename


class TestReadTaskLogTail:
    def test_reads_tail(self, tmp_path):
        log_file = str(tmp_path / "test.log")
        with open(log_file, "w") as f:
            for i in range(50):
                f.write("line {}\n".format(i))
        tail = sl.read_task_log_tail(log_file, 5)
        assert "line 45" in tail
        assert "line 49" in tail
        assert "line 0" not in tail

    def test_missing_file(self):
        assert sl.read_task_log_tail("/nonexistent/file.log") == "(no log)"

    def test_short_file(self, tmp_path):
        log_file = str(tmp_path / "short.log")
        with open(log_file, "w") as f:
            f.write("only line\n")
        assert "only line" in sl.read_task_log_tail(log_file, 10)


class TestErrorResult:
    def test_has_all_keys(self):
        r = sl._error_result("boom")
        assert r["success"] is False
        assert r["output"] == "boom"
        assert r["returncode"] == -1
        assert r["is_conflict"] is False
        assert r["is_test_fail"] is False
        assert r["passed_commits"] == []
        assert r["failed_commit"] is None


class TestParseRefsAndBranch:
    def test_no_spaces(self):
        refs, branch = sl._parse_refs_and_branch("abc,def,ghi my_branch")
        assert refs == ["abc", "def", "ghi"]
        assert branch == "my_branch"

    def test_spaces_after_commas(self):
        refs, branch = sl._parse_refs_and_branch("abc, def, ghi my_branch")
        assert refs == ["abc", "def", "ghi"]
        assert branch == "my_branch"

    def test_spaces_before_commas(self):
        refs, branch = sl._parse_refs_and_branch("abc ,def ,ghi my_branch")
        assert refs == ["abc", "def", "ghi"]
        assert branch == "my_branch"

    def test_single_ref(self):
        refs, branch = sl._parse_refs_and_branch("abc123 my_branch")
        assert refs == ["abc123"]
        assert branch == "my_branch"

    def test_empty(self):
        refs, branch = sl._parse_refs_and_branch("")
        assert refs == []
        assert branch == ""

    def test_no_branch(self):
        refs, branch = sl._parse_refs_and_branch("abc123")
        assert refs == []
        assert branch == ""


class TestIsProcessAlive:
    def test_own_pid(self):
        assert sl._is_process_alive(os.getpid()) is True

    def test_none_pid(self):
        assert sl._is_process_alive(None) is False

    def test_dead_pid(self):
        assert sl._is_process_alive(999999999) is False
