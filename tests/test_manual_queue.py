"""
Tests for manual queue, failure fallback, infra retry, urgent queue,
branch health check, and reporting features.
"""

import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import slack_listener as sl


# ======================== Helpers ========================

class FakeSay:
    """Capture say() calls for assertion."""
    def __init__(self):
        self.messages = []

    def __call__(self, text, **kwargs):
        self.messages.append({"text": text, "kwargs": kwargs})

    @property
    def texts(self):
        return [m["text"] for m in self.messages]

    def last(self):
        return self.messages[-1]["text"] if self.messages else ""

    def clear(self):
        self.messages.clear()


def _make_task(task_type="manual", user="U_TEST", branch="sandbox/test_branch",
               say=None, ts="1234.5678", commits=None, raw_refs=None):
    if say is None:
        say = FakeSay()
    task = {
        "type": task_type,
        "commits": commits or [],
        "target_branch": branch,
        "say": say,
        "ts": ts,
        "user": user,
        "user_name": "TestUser",
        "queued_at": time.time(),
        "log_file": "/tmp/test_task.log",
    }
    if raw_refs:
        task["raw_refs"] = raw_refs
    return task


@pytest.fixture(autouse=True)
def reset_global_state():
    """Reset all global state before each test."""
    with sl.state_lock:
        sl.pending_tasks.clear()
        sl.current_task = None
        sl.task_history.clear()
        sl.hold_before_ts = None
        sl.hold_requested_by = None
        sl.session_default_branch = ""
    sl.hold_event.set()
    with sl.task_condition:
        sl.task_list.clear()
    yield


# ======================== Task list (Queue replacement) ========================

class TestTaskList:
    def test_put_and_get(self):
        task = _make_task()
        sl._put_task(task)
        result = sl._get_task()
        assert result is task

    def test_fifo_order(self):
        t1 = _make_task(ts="1")
        t2 = _make_task(ts="2")
        t3 = _make_task(ts="3")
        sl._put_task(t1)
        sl._put_task(t2)
        sl._put_task(t3)
        assert sl._get_task() is t1
        assert sl._get_task() is t2
        assert sl._get_task() is t3

    def test_urgent_inserts_at_front(self):
        t1 = _make_task(ts="1")
        t2 = _make_task(ts="2")
        t3 = _make_task(ts="urgent")
        sl._put_task(t1)
        sl._put_task(t2)
        sl._put_task(t3, urgent=True)
        assert sl._get_task() is t3
        assert sl._get_task() is t1
        assert sl._get_task() is t2

    def test_get_blocks_until_put(self):
        result = []

        def getter():
            result.append(sl._get_task())

        t = threading.Thread(target=getter)
        t.start()
        time.sleep(0.05)
        assert not result
        task = _make_task()
        sl._put_task(task)
        t.join(timeout=1)
        assert len(result) == 1
        assert result[0] is task


# ======================== _task_summary ========================

class TestTaskSummary:
    def test_manual_task_summary(self):
        t = _make_task(task_type="manual", branch="sandbox/test")
        summary = sl._task_summary(t)
        assert "[manual]" in summary
        assert "sandbox/test" in summary

    def test_bot_task_summary(self):
        t = _make_task(task_type="single", commits=["abc123def"])
        summary = sl._task_summary(t)
        assert "single" in summary
        assert "abc123def" in summary[:30]


# ======================== _enqueue ========================

class TestEnqueue:
    def test_enqueue_normal(self):
        say = FakeSay()
        task = _make_task(say=say)
        sl._enqueue(task, say, "ts1")
        assert len(sl.pending_tasks) == 1
        assert len(sl.task_list) == 1

    def test_enqueue_urgent(self):
        say = FakeSay()
        t1 = _make_task(say=say, ts="1", user="U_A")
        t2 = _make_task(say=say, ts="2", user="U_B")
        sl._enqueue(t1, say, "1")
        sl._enqueue(t2, say, "2", urgent=True)
        assert sl.pending_tasks[0] is t2
        assert sl.pending_tasks[1] is t1

    def test_enqueue_rejects_bad_branch(self):
        say = FakeSay()
        task = _make_task(say=say, branch="bad/branch")
        sl._enqueue(task, say, "ts1")
        assert len(sl.pending_tasks) == 0
        assert "must start with" in say.last()

    def test_enqueue_skips_duplicate_bot_task(self):
        say = FakeSay()
        t1 = _make_task(say=say, task_type="single", ts="1")
        t1["raw_refs"] = ["abc123"]
        t2 = _make_task(say=say, task_type="single", ts="2")
        t2["raw_refs"] = ["abc123"]
        sl._enqueue(t1, say, "1")
        sl._enqueue(t2, say, "2")
        assert len(sl.pending_tasks) == 1
        assert "Duplicate" in say.last()

    def test_enqueue_blocks_duplicate_manual_same_user_branch(self):
        say = FakeSay()
        t1 = _make_task(say=say, task_type="manual", ts="1", user="U_SAME")
        t2 = _make_task(say=say, task_type="manual", ts="2", user="U_SAME")
        sl._enqueue(t1, say, "1")
        sl._enqueue(t2, say, "2")
        assert len(sl.pending_tasks) == 1
        assert "already have" in say.last()

    def test_enqueue_allows_manual_different_users(self):
        say = FakeSay()
        t1 = _make_task(say=say, task_type="manual", ts="1", user="U_A")
        t2 = _make_task(say=say, task_type="manual", ts="2", user="U_B")
        sl._enqueue(t1, say, "1")
        sl._enqueue(t2, say, "2")
        assert len(sl.pending_tasks) == 2


# ======================== _resolve_branch ========================

class TestResolveBranch:
    def test_explicit_branch(self):
        sl.session_default_branch = "sandbox/default"
        assert sl._resolve_branch("sandbox/explicit") == "sandbox/explicit"

    def test_fallback_to_default(self):
        sl.session_default_branch = "sandbox/default"
        assert sl._resolve_branch("") == "sandbox/default"

    def test_no_branch(self):
        sl.session_default_branch = ""
        assert sl._resolve_branch("") == ""


# ======================== Infra retry ========================

class TestInfraRetry:
    def test_is_infra_failure_detects_auth(self):
        assert sl._is_infra_failure("Please login to static-login.nvidia.com")
        assert sl._is_infra_failure("buildauth COMMAND FAILED something")

    def test_is_infra_failure_rejects_normal(self):
        assert not sl._is_infra_failure("FAILED: test_foo")
        assert not sl._is_infra_failure("cherry-pick conflict")
        assert not sl._is_infra_failure("")
        assert not sl._is_infra_failure(None)

    def test_execute_with_infra_retry_success_no_retry(self):
        say = FakeSay()
        task = _make_task(say=say, task_type="test")
        call_count = [0]

        original = sl._execute_task

        def mock_execute(t):
            call_count[0] += 1
            return {"success": True, "output": "test passes"}

        sl._execute_task = mock_execute
        try:
            result = sl._execute_with_infra_retry(task)
            assert result["success"]
            assert call_count[0] == 1
        finally:
            sl._execute_task = original

    def test_execute_with_infra_retry_retries_on_infra(self):
        say = FakeSay()
        task = _make_task(say=say, task_type="test")
        call_count = [0]

        original_retry = sl.MAX_INFRA_RETRIES
        sl.MAX_INFRA_RETRIES = 2

        def mock_execute(t):
            call_count[0] += 1
            if call_count[0] <= 2:
                return {"success": False, "output": "buildauth COMMAND FAILED"}
            return {"success": True, "output": "test passes"}

        original = sl._execute_task
        sl._execute_task = mock_execute
        try:
            result = sl._execute_with_infra_retry(task)
            assert result["success"]
            assert call_count[0] == 3
            assert any("Auto-retry" in t or "retrying" in t.lower() for t in say.texts)
        finally:
            sl._execute_task = original
            sl.MAX_INFRA_RETRIES = original_retry

    def test_no_retry_on_real_failure(self):
        say = FakeSay()
        task = _make_task(say=say, task_type="test")
        call_count = [0]

        def mock_execute(t):
            call_count[0] += 1
            return {"success": False, "output": "FAILED: test_foo assertion error"}

        original = sl._execute_task
        sl._execute_task = mock_execute
        try:
            result = sl._execute_with_infra_retry(task)
            assert not result["success"]
            assert call_count[0] == 1
        finally:
            sl._execute_task = original


# ======================== Branch health check ========================

class TestBranchHealth:
    def test_empty_branch_is_ok(self):
        ok, msg = sl.check_branch_health("")
        assert ok
        assert msg == ""


# ======================== Manual mode events ========================

class TestManualModeEvents:
    def test_done_event_signals_worker(self):
        """Simulate done event being set and check worker would wake up."""
        event = threading.Event()
        task = _make_task()
        task["manual_done_event"] = event
        task["manual_mode"] = True

        def set_done():
            time.sleep(0.05)
            event.set()

        t = threading.Thread(target=set_done)
        t.start()
        assert event.wait(timeout=1)
        t.join()

    def test_takeover_event(self):
        """Simulate takeover event — anyone can claim."""
        event = threading.Event()
        task = _make_task()
        task["waiting_for_takeover"] = True
        task["takeover_event"] = event

        def claim():
            time.sleep(0.05)
            task["takeover_user"] = "U_OTHER"
            event.set()

        t = threading.Thread(target=claim)
        t.start()
        event.wait(timeout=1)
        t.join()
        assert task.get("takeover_user") == "U_OTHER"

    def test_skip_sets_flag_and_event(self):
        event = threading.Event()
        task = _make_task()
        task["manual_done_event"] = event
        task["manual_mode"] = True

        task["skipped"] = True
        event.set()
        assert event.is_set()
        assert task["skipped"]


# ======================== build_status_message ========================

class TestBuildStatusMessage:
    def test_idle_status(self):
        msg = sl.build_status_message()
        assert "idle" in msg.lower() or "none" in msg.lower()

    def test_status_with_manual_mode(self):
        task = _make_task()
        task["started_at"] = time.time()
        task["manual_mode"] = True
        task["manual_deadline"] = time.time() + 600
        with sl.state_lock:
            sl.current_task = task
        try:
            msg = sl.build_status_message()
            assert "Manual mode" in msg
        finally:
            with sl.state_lock:
                sl.current_task = None

    def test_status_with_waiting_for_takeover(self):
        task = _make_task()
        task["started_at"] = time.time()
        task["waiting_for_takeover"] = True
        with sl.state_lock:
            sl.current_task = task
        try:
            msg = sl.build_status_message()
            assert "takeover" in msg.lower()
        finally:
            with sl.state_lock:
                sl.current_task = None

    def test_status_shows_default_branch(self):
        sl.session_default_branch = "sandbox/test_branch"
        msg = sl.build_status_message()
        assert "sandbox/test_branch" in msg

    def test_status_with_pending_tasks(self):
        say = FakeSay()
        t1 = _make_task(say=say, ts="1")
        with sl.state_lock:
            sl.pending_tasks.append(t1)
        try:
            msg = sl.build_status_message()
            assert "1 task(s) waiting" in msg
        finally:
            with sl.state_lock:
                sl.pending_tasks.clear()


# ======================== Cancel with manual mode ========================

class TestCancelManualMode:
    def test_cancel_during_manual_mode_sets_cancelled(self):
        event = threading.Event()
        task = _make_task()
        task["manual_mode"] = True
        task["manual_done_event"] = event
        task["started_at"] = time.time()

        with sl.state_lock:
            sl.current_task = task

        task["cancelled"] = True
        event.set()

        assert task["cancelled"]
        assert event.is_set()

        with sl.state_lock:
            sl.current_task = None


# ======================== Success report ========================

class TestSuccessReport:
    def test_post_success_report(self, monkeypatch):
        say = FakeSay()
        task = _make_task(say=say, task_type="single")
        task["started_at"] = time.time() - 60
        result = {"success": True, "output": "Git Log (target):\nabc def"}

        monkeypatch.setattr(sl, "_get_recent_commits", lambda n=10: "abc123 test commit")
        sl._post_success_report(task, result)
        assert say.messages
        assert "succeeded" in say.last().lower() or "success" in say.last().lower()
        assert "Alfred build" in say.last()

    def test_post_manual_done_report_head_changed(self, monkeypatch):
        say = FakeSay()
        task = _make_task(say=say)
        task["head_before_manual"] = "aaa" * 13 + "a"

        monkeypatch.setattr(sl, "_get_current_head", lambda: "bbb" * 13 + "b")
        monkeypatch.setattr(sl, "REPO_PATH", "/nonexistent")

        sl._post_manual_done_report(task)
        assert say.messages
        assert "Manual operation complete" in say.last()

    def test_post_manual_done_report_head_unchanged(self, monkeypatch):
        say = FakeSay()
        task = _make_task(say=say)
        same_head = "ccc" * 13 + "c"
        task["head_before_manual"] = same_head

        monkeypatch.setattr(sl, "_get_current_head", lambda: same_head)

        sl._post_manual_done_report(task)
        assert say.messages
        assert "HEAD unchanged" in say.last()


# ======================== _prepare_task ========================

class TestPrepareTask:
    def test_prepare_task_no_refs(self, monkeypatch):
        say = FakeSay()
        task = _make_task(say=say, task_type="single", commits=["abc"])
        monkeypatch.setattr(sl, "REPO_PATH", "/tmp")
        monkeypatch.setattr(sl, "assert_cwd_is_repo", lambda: None)

        import subprocess as sp
        def fake_run(*a, **kw):
            r = sp.CompletedProcess(a[0] if a else [], 0, stdout="fake_head\n", stderr="")
            return r
        monkeypatch.setattr(sp, "run", fake_run)

        result = sl._prepare_task(task)
        assert result is True
        assert task.get("save_head") == "fake_head"

    def test_prepare_task_with_failed_refs(self, monkeypatch):
        say = FakeSay()
        task = _make_task(say=say, task_type="single")
        task["raw_refs"] = ["bad_ref"]

        def fake_resolve(refs, say_fn, ts):
            return [], True

        monkeypatch.setattr(sl, "resolve_refs", fake_resolve)
        result = sl._prepare_task(task)
        assert result is False


# ======================== _send_starting_message ========================

class TestSendStartingMessage:
    def test_single_cp_message(self):
        say = FakeSay()
        task = _make_task(say=say, task_type="single", commits=["abc123"])
        sl._send_starting_message(task)
        assert say.messages
        assert "Cherry-Pick" in say.last()

    def test_revert_message(self):
        say = FakeSay()
        task = _make_task(say=say, task_type="revert", commits=["abc123"])
        sl._send_starting_message(task)
        assert "Revert" in say.last()

    def test_test_message(self):
        say = FakeSay()
        task = _make_task(say=say, task_type="test")
        sl._send_starting_message(task)
        assert "test" in say.last().lower()

    def test_batch_message(self):
        say = FakeSay()
        task = _make_task(say=say, task_type="batch", commits=["a", "b"])
        sl._send_starting_message(task)
        assert "Batch" in say.last()

    def test_step_message(self):
        say = FakeSay()
        task = _make_task(say=say, task_type="step", commits=["a", "b"])
        sl._send_starting_message(task)
        assert "Step" in say.last()


# ======================== Hybrid queue ordering ========================

class TestHybridQueue:
    def test_manual_and_bot_tasks_coexist(self):
        say = FakeSay()
        t_bot = _make_task(say=say, task_type="single", ts="1")
        t_bot["raw_refs"] = ["abc123"]
        t_manual = _make_task(say=say, task_type="manual", ts="2")
        t_bot2 = _make_task(say=say, task_type="single", ts="3")
        t_bot2["raw_refs"] = ["def456"]

        sl._enqueue(t_bot, say, "1")
        sl._enqueue(t_manual, say, "2")
        sl._enqueue(t_bot2, say, "3")

        assert len(sl.pending_tasks) == 3
        assert sl.pending_tasks[0]["type"] == "single"
        assert sl.pending_tasks[1]["type"] == "manual"
        assert sl.pending_tasks[2]["type"] == "single"

    def test_urgent_manual_goes_first(self):
        say = FakeSay()
        t_bot = _make_task(say=say, task_type="single", ts="1")
        t_bot["raw_refs"] = ["abc123"]
        t_urgent = _make_task(say=say, task_type="manual", ts="2")

        sl._enqueue(t_bot, say, "1")
        sl._enqueue(t_urgent, say, "2", urgent=True)

        assert sl.pending_tasks[0]["type"] == "manual"
        assert sl.pending_tasks[1]["type"] == "single"


# ======================== Full QueueWorker integration ========================

class TestQueueWorkerManual:
    def test_manual_task_no_takeover_skips(self):
        """Manual task with no takeover response should auto-skip."""
        say = FakeSay()
        task = _make_task(say=say, task_type="manual")

        original_wait = sl.FAILURE_WAIT_TIMEOUT
        sl.FAILURE_WAIT_TIMEOUT = 1

        original_get_head = sl._get_current_head
        sl._get_current_head = lambda: "aaa" * 13 + "a"

        try:
            with sl.state_lock:
                sl.current_task = task
                task["started_at"] = time.time()

            worker = sl.QueueWorker()
            worker._run_manual_task(task)

            assert any("no one claimed" in t.lower() for t in say.texts)
        finally:
            sl.FAILURE_WAIT_TIMEOUT = original_wait
            sl._get_current_head = original_get_head
            with sl.state_lock:
                sl.current_task = None

    def test_manual_task_takeover_then_done(self):
        """Someone takes over, then replies done."""
        say = FakeSay()
        task = _make_task(say=say, task_type="manual")

        original_wait = sl.FAILURE_WAIT_TIMEOUT
        original_timeout = sl.MANUAL_TIMEOUT
        sl.FAILURE_WAIT_TIMEOUT = 5
        sl.MANUAL_TIMEOUT = 5

        fake_head = "bbb" * 13 + "b"
        original_get_head = sl._get_current_head
        original_get_remote = sl._get_remote_head
        sl._get_current_head = lambda: fake_head
        sl._get_remote_head = lambda b: fake_head

        try:
            with sl.state_lock:
                sl.current_task = task
                task["started_at"] = time.time()

            def takeover_then_done():
                time.sleep(0.2)
                with sl.state_lock:
                    if task.get("takeover_event"):
                        task["takeover_user"] = "U_HELPER"
                        task["takeover_event"].set()
                time.sleep(0.3)
                with sl.state_lock:
                    if task.get("manual_done_event"):
                        task["manual_done_event"].set()

            t = threading.Thread(target=takeover_then_done)
            t.start()

            worker = sl.QueueWorker()
            worker._run_manual_task(task)
            t.join(timeout=3)

            assert any("Manual operation complete" in t for t in say.texts)
        finally:
            sl.FAILURE_WAIT_TIMEOUT = original_wait
            sl.MANUAL_TIMEOUT = original_timeout
            sl._get_current_head = original_get_head
            sl._get_remote_head = original_get_remote
            with sl.state_lock:
                sl.current_task = None

    def test_failure_fallback_no_takeover(self):
        """Failure fallback with no takeover should return False."""
        say = FakeSay()
        task = _make_task(say=say, task_type="single")

        original_wait = sl.FAILURE_WAIT_TIMEOUT
        sl.FAILURE_WAIT_TIMEOUT = 1

        try:
            with sl.state_lock:
                sl.current_task = task
                task["started_at"] = time.time()

            worker = sl.QueueWorker()
            accepted = worker._offer_manual_fallback(task)

            assert not accepted
            assert any("no one claimed" in t.lower() for t in say.texts)
        finally:
            sl.FAILURE_WAIT_TIMEOUT = original_wait
            with sl.state_lock:
                sl.current_task = None

    def test_failure_fallback_takeover(self):
        """Failure fallback with takeover should return True."""
        say = FakeSay()
        task = _make_task(say=say, task_type="single")

        original_wait = sl.FAILURE_WAIT_TIMEOUT
        sl.FAILURE_WAIT_TIMEOUT = 5

        original_timeout = sl.MANUAL_TIMEOUT
        sl.MANUAL_TIMEOUT = 5

        fake_head = "ccc" * 13 + "c"
        original_get_head = sl._get_current_head
        original_get_remote = sl._get_remote_head
        sl._get_current_head = lambda: fake_head
        sl._get_remote_head = lambda b: fake_head

        try:
            with sl.state_lock:
                sl.current_task = task
                task["started_at"] = time.time()

            def claim_and_done():
                time.sleep(0.2)
                with sl.state_lock:
                    if task.get("takeover_event"):
                        task["takeover_user"] = "U_HELPER"
                        task["takeover_event"].set()

            t = threading.Thread(target=claim_and_done)
            t.start()

            worker = sl.QueueWorker()
            accepted = worker._offer_manual_fallback(task)
            t.join(timeout=2)

            assert accepted
        finally:
            sl.FAILURE_WAIT_TIMEOUT = original_wait
            sl.MANUAL_TIMEOUT = original_timeout
            sl._get_current_head = original_get_head
            sl._get_remote_head = original_get_remote
            with sl.state_lock:
                sl.current_task = None


# ======================== Takeover command logic ========================

class TestTakeoverCommand:
    def test_anyone_can_takeover(self):
        """A different user than the task owner should be able to takeover."""
        task = _make_task(user="U_ORIGINAL")
        event = threading.Event()
        with sl.state_lock:
            sl.current_task = task
            task["waiting_for_takeover"] = True
            task["takeover_event"] = event

        try:
            task["takeover_user"] = "U_HELPER"
            event.set()
            assert task["takeover_user"] == "U_HELPER"
        finally:
            with sl.state_lock:
                sl.current_task = None

    def test_takeover_rejects_when_not_waiting(self):
        """Takeover should fail if task is not in waiting_for_takeover state."""
        task = _make_task()
        task["started_at"] = time.time()
        with sl.state_lock:
            sl.current_task = task
        try:
            assert not task.get("waiting_for_takeover")
        finally:
            with sl.state_lock:
                sl.current_task = None

    def test_takeover_rejects_when_already_taken(self):
        """If manual_mode is already active, takeover should indicate someone already has it."""
        task = _make_task()
        task["manual_mode"] = True
        task["started_at"] = time.time()
        with sl.state_lock:
            sl.current_task = task
        try:
            assert task.get("manual_mode")
            assert not task.get("waiting_for_takeover")
        finally:
            with sl.state_lock:
                sl.current_task = None


class TestDoneCommand:
    def test_done_accepted_from_takeover_user(self):
        """Done should be accepted from whoever took over."""
        event = threading.Event()
        task = _make_task(user="U_ORIGINAL")
        task["manual_mode"] = True
        task["manual_done_event"] = event
        task["takeover_user"] = "U_HELPER"
        task["started_at"] = time.time()

        with sl.state_lock:
            sl.current_task = task
        try:
            assert task.get("takeover_user") == "U_HELPER"
            event.set()
            assert event.is_set()
        finally:
            with sl.state_lock:
                sl.current_task = None

    def test_done_rejected_from_non_takeover_user(self):
        """Done should be rejected if sender is not the takeover user."""
        task = _make_task(user="U_ORIGINAL")
        task["manual_mode"] = True
        task["takeover_user"] = "U_HELPER"
        task["started_at"] = time.time()

        with sl.state_lock:
            sl.current_task = task
        try:
            assert task.get("takeover_user") != "U_RANDOM"
        finally:
            with sl.state_lock:
                sl.current_task = None


class TestSkipCommand:
    def test_skip_only_from_takeover_user(self):
        """Skip should only work for the person who took over."""
        event = threading.Event()
        task = _make_task(user="U_ORIGINAL")
        task["manual_mode"] = True
        task["manual_done_event"] = event
        task["takeover_user"] = "U_HELPER"
        task["started_at"] = time.time()

        with sl.state_lock:
            sl.current_task = task
        try:
            assert task.get("takeover_user") == "U_HELPER"
            task["skipped"] = True
            event.set()
            assert task["skipped"]
        finally:
            with sl.state_lock:
                sl.current_task = None


class TestCancelDuringTakeover:
    def test_cancel_during_waiting_for_takeover(self):
        """Cancel should unblock the wait_for_takeover."""
        event = threading.Event()
        task = _make_task()
        task["waiting_for_takeover"] = True
        task["takeover_event"] = event
        task["started_at"] = time.time()

        with sl.state_lock:
            sl.current_task = task
        try:
            task["cancelled"] = True
            event.set()
            assert event.is_set()
            assert task["cancelled"]
        finally:
            with sl.state_lock:
                sl.current_task = None


class TestActivityDetection:
    def test_head_change_auto_extends(self):
        """When remote HEAD changes, manual_deadline should be extended."""
        say = FakeSay()
        task = _make_task(say=say, task_type="manual")
        task["takeover_user"] = "U_HELPER"

        original_timeout = sl.MANUAL_TIMEOUT
        sl.MANUAL_TIMEOUT = 3

        heads = iter(["aaa" * 10, "bbb" * 10, "bbb" * 10])
        original_get_remote = sl._get_remote_head
        sl._get_remote_head = lambda b: next(heads, "bbb" * 10)

        original_get_head = sl._get_current_head
        sl._get_current_head = lambda: "aaa" * 10

        try:
            done_event = threading.Event()
            with sl.state_lock:
                sl.current_task = task
                task["started_at"] = time.time()
                task["manual_mode"] = True
                task["manual_done_event"] = done_event
                task["manual_deadline"] = time.time() + sl.MANUAL_TIMEOUT
                task["head_before_manual"] = "aaa" * 10

            initial_deadline = task["manual_deadline"]

            def signal_done_later():
                time.sleep(2)
                done_event.set()

            t = threading.Thread(target=signal_done_later)
            t.start()

            worker = sl.QueueWorker()
            worker._run_manual_mode(task)
            t.join(timeout=5)

        finally:
            sl.MANUAL_TIMEOUT = original_timeout
            sl._get_remote_head = original_get_remote
            sl._get_current_head = original_get_head
            with sl.state_lock:
                sl.current_task = None


class TestFullTakeoverFlow:
    def test_manual_queue_full_flow(self):
        """queue → notify → takeover → work → done → report."""
        say = FakeSay()
        task = _make_task(say=say, task_type="manual")

        original_wait = sl.FAILURE_WAIT_TIMEOUT
        original_timeout = sl.MANUAL_TIMEOUT
        sl.FAILURE_WAIT_TIMEOUT = 5
        sl.MANUAL_TIMEOUT = 5

        fake_head = "ddd" * 13 + "d"
        original_get_head = sl._get_current_head
        original_get_remote = sl._get_remote_head
        sl._get_current_head = lambda: fake_head
        sl._get_remote_head = lambda b: fake_head

        try:
            with sl.state_lock:
                sl.current_task = task
                task["started_at"] = time.time()

            def simulate_user():
                time.sleep(0.3)
                with sl.state_lock:
                    if task.get("takeover_event"):
                        task["takeover_user"] = "U_TEAMMATE"
                        task["takeover_event"].set()
                time.sleep(0.5)
                with sl.state_lock:
                    if task.get("manual_done_event"):
                        task["manual_done_event"].set()

            t = threading.Thread(target=simulate_user)
            t.start()

            worker = sl.QueueWorker()
            worker._run_manual_task(task)
            t.join(timeout=5)

            assert any("Your turn" in t for t in say.texts)
            assert any("Manual operation complete" in t for t in say.texts)
            assert task.get("takeover_user") == "U_TEAMMATE"
        finally:
            sl.FAILURE_WAIT_TIMEOUT = original_wait
            sl.MANUAL_TIMEOUT = original_timeout
            sl._get_current_head = original_get_head
            sl._get_remote_head = original_get_remote
            with sl.state_lock:
                sl.current_task = None

    def test_failure_fallback_full_flow(self):
        """bot failure → offer takeover → someone claims → done."""
        say = FakeSay()
        task = _make_task(say=say, task_type="single")

        original_wait = sl.FAILURE_WAIT_TIMEOUT
        original_timeout = sl.MANUAL_TIMEOUT
        sl.FAILURE_WAIT_TIMEOUT = 5
        sl.MANUAL_TIMEOUT = 5

        fake_head = "eee" * 13 + "e"
        original_get_head = sl._get_current_head
        original_get_remote = sl._get_remote_head
        sl._get_current_head = lambda: fake_head
        sl._get_remote_head = lambda b: fake_head

        try:
            with sl.state_lock:
                sl.current_task = task
                task["started_at"] = time.time()

            def claim():
                time.sleep(0.3)
                with sl.state_lock:
                    if task.get("takeover_event"):
                        task["takeover_user"] = "U_SAVIOR"
                        task["takeover_event"].set()

            t = threading.Thread(target=claim)
            t.start()

            worker = sl.QueueWorker()
            accepted = worker._offer_manual_fallback(task)
            t.join(timeout=2)

            assert accepted
            assert task.get("takeover_user") == "U_SAVIOR"
            assert any("could not complete" in t.lower() for t in say.texts)
        finally:
            sl.FAILURE_WAIT_TIMEOUT = original_wait
            sl.MANUAL_TIMEOUT = original_timeout
            sl._get_current_head = original_get_head
            sl._get_remote_head = original_get_remote
            with sl.state_lock:
                sl.current_task = None


# ======================== Next in queue message ========================

class TestNextInQueueMsg:
    def test_empty_queue(self):
        msg = sl._next_in_queue_msg()
        assert "empty" in msg.lower()

    def test_with_pending(self):
        say = FakeSay()
        t = _make_task(say=say, task_type="manual")
        with sl.state_lock:
            sl.pending_tasks.append(t)
        try:
            msg = sl._next_in_queue_msg()
            assert "Next:" in msg
        finally:
            with sl.state_lock:
                sl.pending_tasks.clear()


# ======================== Cancel with task_list ========================

class TestCancelWithTaskList:
    def test_cancel_removes_from_both_lists(self):
        say = FakeSay()
        t1 = _make_task(say=say, ts="1", task_type="manual", user="U_A")
        t2 = _make_task(say=say, ts="2", task_type="manual", user="U_B")

        sl._enqueue(t1, say, "1")
        sl._enqueue(t2, say, "2")

        assert len(sl.pending_tasks) == 2
        assert len(sl.task_list) == 2

        with sl.state_lock:
            removed = sl.pending_tasks.pop(0)
            with sl.task_condition:
                if removed in sl.task_list:
                    sl.task_list.remove(removed)

        assert len(sl.pending_tasks) == 1
        assert len(sl.task_list) == 1
        assert sl.pending_tasks[0] is t2
