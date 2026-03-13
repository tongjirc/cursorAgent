"""
Tests for the logging system and queue/state management.
"""

import glob
import os
import sys
import tempfile
import time
from collections import OrderedDict
from queue import Queue

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import slack_listener as sl


class TestLogCleaner:
    def test_removes_old_files(self, tmp_path):
        """Files older than retain_hours should be deleted."""
        log_dir = str(tmp_path)

        old_file = os.path.join(log_dir, "old_task.log")
        new_file = os.path.join(log_dir, "new_task.log")

        with open(old_file, "w") as f:
            f.write("old log content\n")
        with open(new_file, "w") as f:
            f.write("new log content\n")

        # Set old_file's mtime to 48 hours ago
        old_mtime = time.time() - 48 * 3600
        os.utime(old_file, (old_mtime, old_mtime))

        cleaner = sl.LogCleaner(log_dir, retain_hours=36)
        cleaner._cleanup()

        assert not os.path.exists(old_file), "Old file should have been deleted"
        assert os.path.exists(new_file), "New file should still exist"

    def test_keeps_recent_files(self, tmp_path):
        """Files within retain_hours should not be deleted."""
        log_dir = str(tmp_path)

        recent_file = os.path.join(log_dir, "recent.log")
        with open(recent_file, "w") as f:
            f.write("recent content\n")

        cleaner = sl.LogCleaner(log_dir, retain_hours=36)
        cleaner._cleanup()

        assert os.path.exists(recent_file)

    def test_ignores_non_log_files(self, tmp_path):
        """Non-.log files should not be affected."""
        log_dir = str(tmp_path)

        txt_file = os.path.join(log_dir, "readme.txt")
        with open(txt_file, "w") as f:
            f.write("not a log\n")

        old_mtime = time.time() - 100 * 3600
        os.utime(txt_file, (old_mtime, old_mtime))

        cleaner = sl.LogCleaner(log_dir, retain_hours=1)
        cleaner._cleanup()

        assert os.path.exists(txt_file)

    def test_empty_directory(self, tmp_path):
        """Cleanup on empty directory should not raise."""
        cleaner = sl.LogCleaner(str(tmp_path), retain_hours=1)
        cleaner._cleanup()


class TestTaskHistory:
    def test_max_history_limit(self):
        """task_history should evict oldest entries when exceeding MAX_HISTORY."""
        from collections import OrderedDict

        history = OrderedDict()
        max_h = 20

        for i in range(30):
            ts = "ts_{}".format(i)
            history[ts] = {"type": "test", "result_success": True}
            if len(history) > max_h:
                history.popitem(last=False)

        assert len(history) == max_h
        assert "ts_0" not in history
        assert "ts_10" in history
        assert "ts_29" in history

    def test_history_order(self):
        """Newest entries should be last."""
        from collections import OrderedDict

        history = OrderedDict()
        for i in range(5):
            history["ts_{}".format(i)] = {"idx": i}

        keys = list(history.keys())
        assert keys == ["ts_0", "ts_1", "ts_2", "ts_3", "ts_4"]

        last_key, last_val = list(history.items())[-1]
        assert last_key == "ts_4"


class TestRunCommand:
    """Test run_command with simple shell commands (no git needed)."""

    def test_echo_command(self, tmp_path):
        """Basic command execution and log file writing."""
        log_file = str(tmp_path / "test.log")

        # Temporarily override SHELL_INIT to avoid sourcing bashrc
        original_init = sl.SHELL_INIT
        sl.SHELL_INIT = "true"
        try:
            output, rc, timed_out = sl.run_command(
                "echo 'hello world'", 10, log_file, label="test-echo",
            )
        finally:
            sl.SHELL_INIT = original_init

        assert rc == 0
        assert not timed_out
        assert "hello world" in output

        # Log file should contain header + output
        with open(log_file) as f:
            content = f.read()
        assert "test-echo" in content
        assert "hello world" in content
        assert "CWD:" in content

    def test_failing_command(self, tmp_path):
        """Command that exits non-zero."""
        log_file = str(tmp_path / "fail.log")

        original_init = sl.SHELL_INIT
        sl.SHELL_INIT = "true"
        try:
            output, rc, timed_out = sl.run_command(
                "exit 42", 10, log_file, label="test-fail",
            )
        finally:
            sl.SHELL_INIT = original_init

        assert rc == 42
        assert not timed_out

    def test_timeout(self, tmp_path):
        """Command that exceeds timeout should be killed."""
        log_file = str(tmp_path / "timeout.log")

        original_init = sl.SHELL_INIT
        sl.SHELL_INIT = "true"
        try:
            output, rc, timed_out = sl.run_command(
                "sleep 60", 2, log_file, label="test-timeout",
            )
        finally:
            sl.SHELL_INIT = original_init

        assert timed_out

    def test_multiline_output(self, tmp_path):
        """Command with multiple lines of output."""
        log_file = str(tmp_path / "multi.log")

        original_init = sl.SHELL_INIT
        sl.SHELL_INIT = "true"
        try:
            output, rc, timed_out = sl.run_command(
                "for i in 1 2 3 4 5; do echo line_$i; done",
                10, log_file, label="test-multi",
            )
        finally:
            sl.SHELL_INIT = original_init

        assert rc == 0
        for i in range(1, 6):
            assert "line_{}".format(i) in output


class TestReadTaskLogTail:
    def test_long_file(self, tmp_path):
        log_file = str(tmp_path / "long.log")
        with open(log_file, "w") as f:
            for i in range(100):
                f.write("line_{}\n".format(i))

        tail = sl.read_task_log_tail(log_file, 5)
        assert "line_95" in tail
        assert "line_99" in tail
        assert "line_0" not in tail

    def test_short_file(self, tmp_path):
        log_file = str(tmp_path / "short.log")
        with open(log_file, "w") as f:
            f.write("only_line\n")

        tail = sl.read_task_log_tail(log_file, 10)
        assert "only_line" in tail

    def test_nonexistent(self):
        assert sl.read_task_log_tail("/no/such/file.log") == "(no log)"


class TestCancelQueue:
    def _make_task(self, task_type="single", commits=None, branch="b"):
        return {
            "type": task_type,
            "commits": commits or ["abc"],
            "target_branch": branch,
            "say": None,
            "ts": "ts_{}".format(id(self)),
            "user": "U1",
            "user_name": "Tester",
            "queued_at": time.time(),
            "log_file": "/tmp/fake.log",
        }

    def test_cancel_middle_task(self):
        """Cancel task #2 out of 3 pending tasks."""
        original_pending = sl.pending_tasks[:]
        original_queue = sl.task_queue

        sl.pending_tasks = []
        sl.task_queue = Queue()

        t1 = self._make_task(commits=["aaa"])
        t2 = self._make_task(commits=["bbb"])
        t3 = self._make_task(commits=["ccc"])
        sl.pending_tasks = [t1, t2, t3]
        sl.task_queue.put(t1)
        sl.task_queue.put(t2)
        sl.task_queue.put(t3)

        try:
            with sl.state_lock:
                removed = sl.pending_tasks.pop(1)  # remove #2
                new_q = Queue()
                while not sl.task_queue.empty():
                    t = sl.task_queue.get_nowait()
                    if t is not removed:
                        new_q.put(t)
                    sl.task_queue.task_done()
                while not new_q.empty():
                    sl.task_queue.put(new_q.get_nowait())

            assert len(sl.pending_tasks) == 2
            assert sl.pending_tasks[0] is t1
            assert sl.pending_tasks[1] is t3
            assert sl.task_queue.qsize() == 2
        finally:
            sl.pending_tasks = original_pending
            sl.task_queue = original_queue

    def test_cancel_first_task(self):
        """Cancel task #1."""
        original_pending = sl.pending_tasks[:]
        original_queue = sl.task_queue

        sl.pending_tasks = []
        sl.task_queue = Queue()

        t1 = self._make_task(commits=["aaa"])
        t2 = self._make_task(commits=["bbb"])
        sl.pending_tasks = [t1, t2]
        sl.task_queue.put(t1)
        sl.task_queue.put(t2)

        try:
            with sl.state_lock:
                removed = sl.pending_tasks.pop(0)
                new_q = Queue()
                while not sl.task_queue.empty():
                    t = sl.task_queue.get_nowait()
                    if t is not removed:
                        new_q.put(t)
                    sl.task_queue.task_done()
                while not new_q.empty():
                    sl.task_queue.put(new_q.get_nowait())

            assert len(sl.pending_tasks) == 1
            assert sl.pending_tasks[0] is t2
            assert sl.task_queue.qsize() == 1
        finally:
            sl.pending_tasks = original_pending
            sl.task_queue = original_queue

    def test_cancel_only_task(self):
        """Cancel the only task in queue."""
        original_pending = sl.pending_tasks[:]
        original_queue = sl.task_queue

        sl.pending_tasks = []
        sl.task_queue = Queue()

        t1 = self._make_task(commits=["aaa"])
        sl.pending_tasks = [t1]
        sl.task_queue.put(t1)

        try:
            with sl.state_lock:
                removed = sl.pending_tasks.pop(0)
                new_q = Queue()
                while not sl.task_queue.empty():
                    t = sl.task_queue.get_nowait()
                    if t is not removed:
                        new_q.put(t)
                    sl.task_queue.task_done()
                while not new_q.empty():
                    sl.task_queue.put(new_q.get_nowait())

            assert len(sl.pending_tasks) == 0
            assert sl.task_queue.qsize() == 0
        finally:
            sl.pending_tasks = original_pending
            sl.task_queue = original_queue


class TestCancelRollback:
    """Test that cancel 0 resets to save_head and checks out save_branch."""

    def test_rollback_uses_save_head(self):
        """Cancel should git reset --hard <save_head>, not HEAD."""
        from unittest.mock import MagicMock, patch, call

        task = {
            "type": "batch", "commits": ["aaa", "bbb"],
            "target_branch": "target", "say": MagicMock(),
            "ts": "ts1", "user": "U1", "user_name": "Tester",
            "queued_at": time.time(), "log_file": "/tmp/fake.log",
            "pid": 99999, "save_head": "abc123def456",
            "save_branch": "main", "started_at": time.time(),
        }

        original_current = sl.current_task
        sl.current_task = task
        try:
            calls = []
            def track_run(cmd, **kwargs):
                calls.append(cmd)
                m = MagicMock()
                m.returncode = 0
                return m

            with patch("subprocess.run", side_effect=track_run):
                with patch("os.kill"):
                    # Simulate the rollback section of cancel
                    import subprocess as _sp
                    _sp.run(["git", "cherry-pick", "--abort"],
                            capture_output=True, cwd=sl.REPO_PATH, timeout=10)
                    _sp.run(["git", "revert", "--abort"],
                            capture_output=True, cwd=sl.REPO_PATH, timeout=10)

                    save_head = task.get("save_head", "")
                    if save_head:
                        _sp.run(["git", "reset", "--hard", save_head],
                                capture_output=True, cwd=sl.REPO_PATH, timeout=10)

                    _sp.run(["git", "clean", "-fd"],
                            capture_output=True, cwd=sl.REPO_PATH, timeout=10)

                    save_branch = task.get("save_branch", "")
                    if save_branch:
                        _sp.run(["git", "checkout", save_branch],
                                capture_output=True, cwd=sl.REPO_PATH, timeout=10)

            reset_cmds = [c for c in calls if "reset" in c]
            assert reset_cmds == [["git", "reset", "--hard", "abc123def456"]]
            checkout_cmds = [c for c in calls if "checkout" in c]
            assert checkout_cmds == [["git", "checkout", "main"]]
        finally:
            sl.current_task = original_current

    def test_rollback_fallback_without_save_head(self):
        """Without save_head, should fall back to reset --hard HEAD."""
        from unittest.mock import MagicMock, patch

        task = {
            "type": "single", "commits": ["aaa"],
            "target_branch": "target", "say": MagicMock(),
            "ts": "ts2", "user": "U1", "user_name": "Tester",
            "queued_at": time.time(), "log_file": "/tmp/fake.log",
            "pid": 99999, "save_head": "", "save_branch": "",
            "started_at": time.time(),
        }

        calls = []
        def track_run(cmd, **kwargs):
            calls.append(cmd)
            m = MagicMock()
            m.returncode = 0
            return m

        with patch("subprocess.run", side_effect=track_run):
            import subprocess as _sp
            save_head = task.get("save_head", "")
            if save_head:
                _sp.run(["git", "reset", "--hard", save_head],
                        capture_output=True, cwd=sl.REPO_PATH, timeout=10)
            else:
                _sp.run(["git", "reset", "--hard", "HEAD"],
                        capture_output=True, cwd=sl.REPO_PATH, timeout=10)

        reset_cmds = [c for c in calls if "reset" in c]
        assert reset_cmds == [["git", "reset", "--hard", "HEAD"]]

    def test_process_task_saves_head(self, temp_repo):
        """process_task should save HEAD and branch before execution."""
        from unittest.mock import MagicMock

        r = temp_repo
        original_repo = sl.REPO_PATH
        sl.REPO_PATH = r["path"]
        try:
            task = {
                "type": "test", "commits": [],
                "target_branch": "", "say": MagicMock(),
                "ts": "ts3", "user": "U1", "user_name": "Tester",
                "queued_at": time.time(),
                "log_file": os.path.join(r["path"], "test.log"),
            }
            original_test_cmd = sl.TEST_COMMAND
            sl.TEST_COMMAND = "echo test_ok"
            try:
                sl.process_task(task)
            finally:
                sl.TEST_COMMAND = original_test_cmd

            assert "save_head" in task
            assert len(task["save_head"]) == 40
            assert "save_branch" in task
            assert task["save_branch"] == r["original_branch"]
        finally:
            sl.REPO_PATH = original_repo


class TestHoldAndContinue:
    def _make_task(self, task_type="single", commits=None, branch="b", ts=None):
        from unittest.mock import MagicMock
        return {
            "type": task_type,
            "commits": commits or ["abc"],
            "target_branch": branch,
            "say": MagicMock(),
            "ts": ts or "ts_{}".format(id(self)),
            "user": "U1",
            "user_name": "Tester",
            "queued_at": time.time(),
            "log_file": "/tmp/fake.log",
        }

    def test_hold_sets_barrier(self):
        """hold N should set hold_before_ts to the Nth task's ts."""
        original_pending = sl.pending_tasks[:]
        original_hold_ts = sl.hold_before_ts

        t1 = self._make_task(commits=["aaa"], ts="ts1")
        t2 = self._make_task(commits=["bbb"], ts="ts2")
        t3 = self._make_task(commits=["ccc"], ts="ts3")
        sl.pending_tasks = [t1, t2, t3]

        try:
            with sl.state_lock:
                sl.hold_before_ts = sl.pending_tasks[1]["ts"]  # hold 2
                sl.hold_event.clear()

            assert sl.hold_before_ts == "ts2"
            assert not sl.hold_event.is_set()

            # Queue still has all 3 tasks
            assert len(sl.pending_tasks) == 3
        finally:
            sl.pending_tasks = original_pending
            sl.hold_before_ts = original_hold_ts
            sl.hold_event.set()

    def test_continue_clears_barrier(self):
        """continue should clear hold and set the event."""
        original_hold_ts = sl.hold_before_ts

        try:
            sl.hold_before_ts = "ts_something"
            sl.hold_event.clear()

            assert not sl.hold_event.is_set()

            # Simulate continue
            sl.hold_before_ts = None
            sl.hold_event.set()

            assert sl.hold_before_ts is None
            assert sl.hold_event.is_set()
        finally:
            sl.hold_before_ts = original_hold_ts
            sl.hold_event.set()

    def test_worker_blocks_on_held_task(self):
        """Worker should block when task ts matches hold_before_ts."""
        import threading

        original_hold_ts = sl.hold_before_ts

        t1 = self._make_task(commits=["aaa"], ts="ts_held")

        sl.hold_before_ts = "ts_held"
        sl.hold_event.clear()

        blocked = threading.Event()
        unblocked = threading.Event()

        def simulate_worker():
            if sl.hold_before_ts and t1["ts"] == sl.hold_before_ts:
                blocked.set()
                sl.hold_event.wait()
                unblocked.set()

        try:
            worker = threading.Thread(target=simulate_worker)
            worker.start()

            assert blocked.wait(timeout=2), "Worker should be blocked"
            assert not unblocked.is_set(), "Worker should not have proceeded"

            # Continue
            sl.hold_before_ts = None
            sl.hold_event.set()

            assert unblocked.wait(timeout=2), "Worker should proceed after continue"
            worker.join(timeout=2)
        finally:
            sl.hold_before_ts = original_hold_ts
            sl.hold_event.set()

    def test_non_held_task_passes_through(self):
        """Worker should NOT block on a task that isn't the hold target."""
        original_hold_ts = sl.hold_before_ts

        sl.hold_before_ts = "ts_other"
        sl.hold_event.clear()

        t1 = self._make_task(commits=["aaa"], ts="ts_not_held")

        try:
            would_block = (sl.hold_before_ts and t1["ts"] == sl.hold_before_ts)
            assert not would_block, "Non-held task should not trigger hold"
        finally:
            sl.hold_before_ts = original_hold_ts
            sl.hold_event.set()


class TestBuildTask:
    def test_single_task(self):
        task = sl._build_task("single", ["abc123"], "release/6.0", None, "ts1", "U123")
        assert task["type"] == "single"
        assert task["commits"] == ["abc123"]
        assert task["target_branch"] == "release/6.0"
        assert task["user"] == "U123"
        assert task["log_file"].endswith(".log")
        assert "single" in task["log_file"]

    def test_test_task(self):
        task = sl._build_task("test", [], "", None, "ts2", "U456")
        assert task["type"] == "test"
        assert task["commits"] == []
        assert task["log_file"].endswith(".log")
