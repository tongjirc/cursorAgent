#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Slack Socket Mode Listener - Async queue + structured logging + AI analysis
"""

import glob
import json
import logging
import os
import re
import sys
import subprocess
import threading
import time
from collections import OrderedDict
from logging.handlers import RotatingFileHandler
from queue import Queue

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from dotenv import load_dotenv

load_dotenv()

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN", "")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
REPO_PATH = os.environ.get("REPO_PATH", "")

# ======================== Test command config ========================
TEST_COMMAND = os.environ.get(
    "TEST_COMMAND",
    "dazel test"
    " --config=drive-qnx_6_0_8_0"
    " --cache_test_results=0"
    " --remote_download_outputs=all"
    " --test_env=TEST_UNDECLARED_OUTPUTS_FORCE_UPLOAD=1"
    " --config=remote_exec"
    " //tests/apps/roadrunner/RR2/System/RR_l2pp_dag_tests:Roadrunner_2_0.l2pp_amo"
    " 2>&1",
)
TEST_TIMEOUT = int(os.environ.get("TEST_TIMEOUT", "3600"))
SHELL_INIT = os.environ.get("SHELL_INIT", "source ~/.bashrc")
LOG_RETAIN_HOURS = int(os.environ.get("LOG_RETAIN_HOURS", "36"))
ADMIN_USER_IDS = [x.strip() for x in os.environ.get("ADMIN_USER_IDS", "").split(",") if x.strip()]
# =====================================================================

if not REPO_PATH:
    print("REPO_PATH not configured in .env")
    sys.exit(1)

REPO_PATH = os.path.abspath(REPO_PATH)

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(BOT_DIR, "scripts")
LOG_DIR = os.path.join(BOT_DIR, "logs")
TASK_LOG_DIR = os.path.join(LOG_DIR, "tasks")

os.makedirs(TASK_LOG_DIR, exist_ok=True)

# ======================== Logging ========================

log = logging.getLogger("cpbot")
log.setLevel(logging.DEBUG)

_fmt = logging.Formatter(
    "%(asctime)s | %(levelname)-5s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_fh = RotatingFileHandler(
    os.path.join(LOG_DIR, "bot.log"),
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(_fmt)
log.addHandler(_fh)

_ch = logging.StreamHandler(sys.stdout)
_ch.setLevel(logging.INFO)
_ch.setFormatter(_fmt)
log.addHandler(_ch)

# ======================== Global state ========================

task_queue = Queue()
pending_tasks = []
state_lock = threading.Lock()
current_task = None
task_history = OrderedDict()
MAX_HISTORY = 20
hold_event = threading.Event()
hold_event.set()  # open by default (not paused)
hold_before_ts = None  # ts of the task to pause before
hold_requested_by = None  # user_id who requested the hold
app = App(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)
BOT_ID = None

# ======================== User name cache ========================

_user_name_cache = {}


def resolve_user_name(user_id):
    """Resolve Slack user ID to real name via API, with cache."""
    if not user_id or user_id == "unknown":
        return "unknown"
    if user_id in _user_name_cache:
        return _user_name_cache[user_id]
    try:
        resp = app.client.users_info(user=user_id)
        if resp.get("ok"):
            profile = resp["user"].get("profile", {})
            name = profile.get("real_name") or profile.get("display_name") or user_id
            _user_name_cache[user_id] = name
            return name
    except Exception as e:
        log.debug("Failed to resolve user name for %s: %s", user_id, e)
    _user_name_cache[user_id] = user_id
    return user_id


def get_bot_id():
    global BOT_ID
    if BOT_ID:
        return BOT_ID
    try:
        resp = app.client.auth_test()
        if resp.get("ok"):
            BOT_ID = resp.get("user_id")
            return BOT_ID
    except Exception as e:
        log.error("Failed to get Bot ID: %s", e)
    return None


# ======================== REPO_PATH validation ========================

def validate_repo_path():
    if not os.path.isdir(REPO_PATH):
        log.error("REPO_PATH does not exist: %s", REPO_PATH)
        sys.exit(1)
    git_dir = os.path.join(REPO_PATH, ".git")
    if not os.path.exists(git_dir):
        log.error("REPO_PATH is not a git repo (no .git): %s", REPO_PATH)
        sys.exit(1)
    log.info("REPO_PATH validated: %s", REPO_PATH)


def assert_cwd_is_repo():
    if not os.path.isdir(REPO_PATH):
        raise RuntimeError("REPO_PATH disappeared: {}".format(REPO_PATH))


# ======================== Task log files ========================

def make_task_log_path(task_type, user_name="", info=""):
    """Generate a unique log file path per task, including user name."""
    ts = time.strftime("%Y%m%d_%H%M%S")
    safe_user = re.sub(r"[^a-zA-Z0-9_\-]", "", user_name)[:20]
    safe_info = re.sub(r"[^a-zA-Z0-9_\-]", "_", info)[:30]
    parts = [ts, task_type]
    if safe_user:
        parts.append(safe_user)
    if safe_info:
        parts.append(safe_info)
    name = "_".join(parts) + ".log"
    return os.path.join(TASK_LOG_DIR, name)


# ======================== Log cleanup thread ========================

class LogCleaner(threading.Thread):
    def __init__(self, directory, retain_hours):
        super().__init__(daemon=True, name="log-cleaner")
        self.directory = directory
        self.retain_seconds = retain_hours * 3600

    def run(self):
        while True:
            time.sleep(3600)
            self._cleanup()

    def _cleanup(self):
        cutoff = time.time() - self.retain_seconds
        removed = 0
        for path in glob.glob(os.path.join(self.directory, "*.log")):
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except OSError:
                pass
        if removed:
            log.info("Log cleanup: removed %d files older than %dh",
                     removed, self.retain_seconds // 3600)


# ======================== Unified command execution ========================

def run_command(cmd_str, timeout, task_log_path, label=""):
    """
    Execute command via bash login shell.
    stdout streams to task_log_path in real time (line-buffered).
    Returns (output, returncode, timed_out).
    """
    assert_cwd_is_repo()

    full_cmd = "cd {repo} && {{ {init} || true; }} && {cmd}".format(
        repo=_quote(REPO_PATH), init=SHELL_INIT, cmd=cmd_str,
    )

    log.info("[CMD] label=%s | timeout=%ds", label, timeout)
    log.debug("[CMD] full_cmd=%s", full_cmd)

    with open(task_log_path, "w") as f:
        f.write("=== {} | {} ===\n".format(label, time.strftime("%Y-%m-%d %H:%M:%S")))
        f.write("CMD: {}\n".format(cmd_str))
        f.write("CWD: {}\n".format(REPO_PATH))
        f.write("TIMEOUT: {}s\n".format(timeout))
        f.write("=" * 60 + "\n")

    log_f = open(task_log_path, "a", buffering=1)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    try:
        proc = subprocess.Popen(
            ["bash", "--login", "-c", full_cmd],
            stdout=log_f,
            stderr=subprocess.STDOUT,
            cwd=REPO_PATH,
            text=True,
            env=env,
        )
    except Exception as e:
        log_f.close()
        log.error("[CMD] Popen failed: %s", e)
        return str(e), -1, False

    with state_lock:
        if current_task:
            current_task["pid"] = proc.pid
            current_task["log_file"] = task_log_path

    log.info("[CMD] started pid=%d log=%s", proc.pid, task_log_path)

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        timed_out = True
        log.warning("[CMD] TIMEOUT pid=%d after %ds", proc.pid, timeout)

    log_f.close()

    with open(task_log_path, "r") as f:
        output = f.read()

    rc = proc.returncode
    log.info("[CMD] finished pid=%d rc=%d timed_out=%s output_len=%d",
             proc.pid, rc, timed_out, len(output))

    return output, rc, timed_out


def read_task_log_tail(task_log_path, lines=15):
    try:
        with open(task_log_path, "r") as f:
            all_lines = f.readlines()
        tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
        return "".join(tail).strip()
    except (FileNotFoundError, IOError):
        return "(no log)"


# ======================== AI analysis (Cursor Agent CLI) ========================

AGENT_BIN = os.environ.get("AGENT_BIN", os.path.expanduser("~/.local/bin/agent"))
AGENT_TIMEOUT = int(os.environ.get("AGENT_TIMEOUT", "300"))
AGENT_MODEL = os.environ.get("AGENT_MODEL", "")  # e.g. gpt-5.3-codex-fast


def analyze_with_ai(prompt):
    """
    Call Cursor Agent CLI in headless mode for analysis.
    agent -p --trust --workspace $REPO_PATH --mode ask "prompt"
    """
    if not os.path.isfile(AGENT_BIN):
        log.debug("agent binary not found at %s, skipping AI analysis", AGENT_BIN)
        return None

    try:
        cmd = [
            AGENT_BIN, "-p",
            "--trust",
            "--workspace", REPO_PATH,
            "--mode", "ask",
        ]
        if AGENT_MODEL:
            cmd.extend(["--model", AGENT_MODEL])
        cmd.append(prompt)

        log.info("[AI] calling agent model=%s prompt_len=%d",
                 AGENT_MODEL or "default", len(prompt))
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=AGENT_TIMEOUT,
            cwd=REPO_PATH,
        )
        if result.returncode == 0 and result.stdout:
            output = result.stdout.strip()
            log.info("[AI] agent responded, len=%d", len(output))
            return output
        if result.stderr:
            log.warning("[AI] agent stderr: %s", result.stderr[:500])
    except subprocess.TimeoutExpired:
        log.warning("[AI] agent timed out after %ds", AGENT_TIMEOUT)
    except Exception as e:
        log.error("[AI] agent failed: %s", e)
    return None


def _extract_git_output(full_output):
    """Strip task log header (CMD/CWD/TIMEOUT lines), keep only git/test output."""
    lines = (full_output or "").splitlines()
    start = 0
    for i, line in enumerate(lines):
        if line.startswith("=" * 20):
            start = i + 1
            break
    return "\n".join(lines[start:])


def _extract_conflict_diff(full_output):
    """Extract the conflict diff between CONFLICT_DIFF_START and CONFLICT_DIFF_END markers."""
    lines = (full_output or "").splitlines()
    capturing = False
    diff_lines = []
    for line in lines:
        if "CONFLICT_DIFF_START" in line:
            capturing = True
            continue
        if "CONFLICT_DIFF_END" in line:
            capturing = False
            continue
        if capturing:
            diff_lines.append(line)
    return "\n".join(diff_lines) if diff_lines else ""


def analyze_conflict(conflict_files, output_tail):
    diff = _extract_conflict_diff(output_tail)
    if not diff:
        clean_output = _extract_git_output(output_tail)
        diff = "\n".join(clean_output.splitlines()[-40:])

    if len(diff) > 12000:
        diff = diff[:12000] + "\n... (truncated)"

    prompt = (
        "Cherry-pick conflict in: {files}\n\n"
        "git diff output showing conflict markers:\n{diff}\n\n"
        "For each conflict hunk, tell me:\n"
        "- File and hunk location\n"
        "- Accept Current, Accept Incoming, or Accept Combination\n"
        "- If Combination: one sentence on what to keep from each side\n\n"
        "No git commands. No bash. No background explanation."
    ).format(files=conflict_files, diff=diff)
    return analyze_with_ai(prompt)


def _is_infra_failure(output):
    """Check if output is an infrastructure/auth error (not a real test failure)."""
    infra_indicators = [
        "static-login.nvidia.com",
        "buildauth COMMAND FAILED",
        "http status 401",
        "Please login",
        "login process",
        "user_code=",
        "Ensure that you are on the NVIDIA network",
    ]
    return any(ind in (output or "") for ind in infra_indicators)


def _is_real_test_failure(output):
    """Check if output looks like an actual test failure (not infra/auth/build error)."""
    if _is_infra_failure(output):
        return False
    indicators = [
        "FAILED", "FAIL ", "AssertionError", "assert ",
        "test_", "pytest", "PASSED in", " passed", "test passes",
        "test fails", "BUILD FAILED",
    ]
    return any(ind in (output or "") for ind in indicators)


def analyze_test_failure(test_output_tail):
    if not _is_real_test_failure(test_output_tail):
        log.info("[AI] skipping analysis: output does not look like a test failure")
        return None

    clean_output = _extract_git_output(test_output_tail)
    tail = "\n".join(clean_output.splitlines()[-40:])
    prompt = (
        "Test failed after cherry-pick:\n\n"
        "{tail}\n\n"
        "Tell me which file and line to change, and show the fix as a short code diff. "
        "No git commands, no bash, no background explanation."
    ).format(tail=tail)
    return analyze_with_ai(prompt)


def get_output_tail(output, lines=20):
    all_lines = (output or "").strip().splitlines()
    return "\n".join(all_lines[-lines:])


# ======================== Change-Id / Commit resolution ========================

def is_gerrit_change_id(ref):
    """Check if ref looks like a Gerrit Change-Id (I + 40 hex chars)."""
    return bool(ref and len(ref) == 41 and ref[0] == "I" and all(
        c in "0123456789abcdef" for c in ref[1:]))


def is_gerrit_change_number(ref):
    """Check if ref looks like a Gerrit change number (pure digits, 5-7 digits)."""
    return bool(ref and ref.isdigit() and 5 <= len(ref) <= 7)


def _git_fetch(refspec="", timeout=180):
    """
    Fetch from origin.
    If refspec given, fetch that specific ref.
    Otherwise, fetch ALL remote branches (not just tracked ones).
    """
    if refspec:
        cmd = ["git", "fetch", "origin", refspec]
    else:
        cmd = ["git", "fetch", "origin", "+refs/heads/*:refs/remotes/origin/*", "--no-tags"]
    try:
        log.info("[FETCH] %s", " ".join(cmd))
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=REPO_PATH, timeout=timeout,
        )
        return result.returncode == 0
    except Exception as e:
        log.warning("[FETCH] failed: %s", e)
        return False


def _resolve_commit_locally(ref):
    """
    Try to resolve a ref (full or abbreviated hash) to a full commit hash locally.
    Returns the full 40-char hash, or None if not found.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "{}^{{commit}}".format(ref)],
            capture_output=True, text=True, cwd=REPO_PATH, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None


def resolve_change_id(change_id):
    """
    Resolve a Gerrit Change-Id to a commit hash.
    Tries local git log first; if not found, fetches and retries.
    """
    def _search():
        try:
            result = subprocess.run(
                ["git", "log", "--all", "--format=%H",
                 "--grep=Change-Id: {}".format(change_id), "-1"],
                capture_output=True, text=True, cwd=REPO_PATH, timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip().splitlines()[0]
        except Exception as e:
            log.error("[RESOLVE] Change-Id search failed for %s: %s", change_id[:16], e)
        return None

    commit = _search()
    if commit:
        log.info("[RESOLVE] Change-Id %s -> %s (local)", change_id[:16], commit[:12])
        return commit

    log.info("[RESOLVE] Change-Id %s not found locally, fetching...", change_id[:16])
    _git_fetch()
    commit = _search()
    if commit:
        log.info("[RESOLVE] Change-Id %s -> %s (after fetch)", change_id[:16], commit[:12])
    return commit


def _get_gerrit_ssh_info():
    """Parse Gerrit SSH host/port/user from git push remote URL."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "--push", "origin"],
            capture_output=True, text=True, cwd=REPO_PATH, timeout=10,
        )
        url = result.stdout.strip()
        m = re.match(r"ssh://([^@]+)@([^:]+):(\d+)/", url)
        if m:
            return m.group(1), m.group(2), int(m.group(3))
    except Exception:
        pass
    return None, None, None


def _resolve_change_via_gerrit_ssh(change_num):
    """Resolve change number via Gerrit SSH query API (fast, ~2-3s)."""
    user, host, port = _get_gerrit_ssh_info()
    if not host:
        return None, None
    try:
        result = subprocess.run(
            ["ssh", "-p", str(port), "-o", "StrictHostKeyChecking=no",
             "{}@{}".format(user, host),
             "gerrit", "query", "--current-patch-set",
             "change:{}".format(change_num), "--format=JSON"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None, None
        first_line = result.stdout.strip().splitlines()[0]
        data = json.loads(first_line)
        ps = data.get("currentPatchSet", {})
        return ps.get("revision"), ps.get("ref")
    except Exception as e:
        log.warning("[RESOLVE] Gerrit SSH query failed for %s: %s", change_num, e)
    return None, None


def _resolve_change_via_ls_remote(change_num):
    """Resolve change number via git ls-remote (slow fallback)."""
    suffix = change_num[-2:] if len(change_num) >= 2 else change_num
    ref_pattern = "refs/changes/{}/{}/*".format(suffix, change_num)
    result = subprocess.run(
        ["git", "ls-remote", "origin", ref_pattern],
        capture_output=True, text=True, cwd=REPO_PATH, timeout=120,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None, None

    best_ps, best_commit, best_ref = 0, None, None
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        commit_hash, ref_path = parts
        ref_parts = ref_path.split("/")
        if len(ref_parts) < 5 or not ref_parts[-1].isdigit():
            continue
        ps_num = int(ref_parts[-1])
        if ps_num > best_ps:
            best_ps, best_commit, best_ref = ps_num, commit_hash, ref_path
    return best_commit, best_ref


def resolve_change_number(change_num):
    """
    Resolve a Gerrit change number to the latest patchset commit hash.
    Tries Gerrit SSH query API first (fast), falls back to git ls-remote.
    """
    best_commit, best_ref = _resolve_change_via_gerrit_ssh(change_num)

    if not best_commit:
        log.info("[RESOLVE] Gerrit SSH unavailable, falling back to ls-remote for %s", change_num)
        try:
            best_commit, best_ref = _resolve_change_via_ls_remote(change_num)
        except Exception as e:
            log.error("[RESOLVE] change# failed for %s: %s", change_num, e)
            return None

    if not best_commit:
        log.warning("[RESOLVE] change# %s: not found", change_num)
        return None

    log.info("[RESOLVE] change# %s -> %s (ref=%s)", change_num, best_commit[:12], best_ref)

    if not _resolve_commit_locally(best_commit):
        log.info("[RESOLVE] commit %s not local, fetching %s...", best_commit[:12], best_ref)
        _git_fetch(best_ref)

    return best_commit


def resolve_refs(refs, say, ts):
    """
    Resolve a list of refs to commit hashes. Supports:
    - Commit hashes — pass through (fetch if not found locally)
    - Gerrit Change-Ids (I + 40 hex) — resolve via git log --grep (fetch if needed)
    - Gerrit change numbers (4-8 digits) — resolve via git ls-remote + fetch
    Reports resolution to Slack. Returns (resolved_commits, had_errors).
    """
    resolved = []
    errors = []
    resolution_lines = []

    for ref in refs:
        is_hex = len(ref) >= 7 and all(c in "0123456789abcdef" for c in ref)
        has_alpha = any(c in "abcdef" for c in ref)

        if is_gerrit_change_id(ref):
            commit = resolve_change_id(ref)
            if commit:
                resolved.append(commit)
                resolution_lines.append("  Change-Id `{}` -> `{}`".format(ref[:20], commit))
            else:
                errors.append(ref)
        elif is_hex and has_alpha:
            # Contains a-f -> treat as commit hash (abbreviated or full)
            full = _resolve_commit_locally(ref)
            if not full:
                log.info("[RESOLVE] commit %s not local, fetching...", ref[:12])
                _git_fetch()
                full = _resolve_commit_locally(ref)

            if full:
                if full != ref:
                    resolution_lines.append("  `{}` -> `{}`".format(ref, full))
                resolved.append(full)
            else:
                log.warning("[RESOLVE] commit %s not found after fetch", ref[:12])
                errors.append(ref)
        elif is_gerrit_change_number(ref):
            # Pure digits 4-8 chars -> Gerrit change number
            commit = resolve_change_number(ref)
            if commit:
                resolved.append(commit)
                resolution_lines.append("  Change #{} -> `{}`".format(ref, commit))
            else:
                errors.append(ref)
        elif is_hex:
            # Pure digit hex >= 7 chars but not 4-8 range -> try as commit
            full = _resolve_commit_locally(ref)
            if not full:
                _git_fetch()
                full = _resolve_commit_locally(ref)
            if full:
                if full != ref:
                    resolution_lines.append("  `{}` -> `{}`".format(ref, full))
                resolved.append(full)
            else:
                errors.append(ref)
        else:
            resolved.append(ref)

    if resolution_lines:
        say("🔍 Resolved:\n{}".format("\n".join(resolution_lines)), thread_ts=ts)

    if errors:
        say("❌ Could not resolve (not found after fetch): {}".format(
            ", ".join("`{}`".format(e) for e in errors)), thread_ts=ts)
        return [], True

    return resolved, False


# ======================== Shell script execution ========================

def _quote(s):
    return "'{}'".format(s.replace("'", "'\\''"))


def run_cherry_pick(commit_id, target_branch, task_log_path):
    script_path = os.path.join(SCRIPTS_DIR, "execute_cherry_pick.sh")
    cmd_str = "bash {script} {commit} {branch} {repo} {test}".format(
        script=_quote(script_path), commit=_quote(commit_id),
        branch=_quote(target_branch), repo=_quote(REPO_PATH),
        test=_quote(TEST_COMMAND),
    )
    label = "cherry-pick {} -> {}".format(commit_id[:12], target_branch)
    output, rc, timed_out = run_command(cmd_str, TEST_TIMEOUT + 120, task_log_path, label=label)
    if timed_out:
        return _error_result("Cherry-pick timed out")
    return {
        "success": rc == 0 and "NO_CHANGE" not in output and "PUSH_FAIL" not in output,
        "output": output, "returncode": rc,
        "is_conflict": rc == 2 or "CONFLICT" in output,
        "is_test_fail": "TEST_FAIL" in output,
        "is_no_change": "NO_CHANGE" in output,
        "is_push_fail": "PUSH_FAIL" in output,
    }


def run_batch_cherry_pick(commits, target_branch, task_log_path):
    script_path = os.path.join(SCRIPTS_DIR, "batch_cherry_pick.sh")
    commits_str = ",".join(commits)
    cmd_str = "bash {script} {commits} {branch} {repo} {test}".format(
        script=_quote(script_path), commits=_quote(commits_str),
        branch=_quote(target_branch), repo=_quote(REPO_PATH),
        test=_quote(TEST_COMMAND),
    )
    label = "batch-cp {} -> {}".format(commits_str[:30], target_branch)
    output, rc, timed_out = run_command(cmd_str, TEST_TIMEOUT + 300, task_log_path, label=label)
    if timed_out:
        return _error_result("Batch cherry-pick timed out")
    return {
        "success": rc == 0 and "SUCCESS" in output and "PUSH_FAIL" not in output,
        "output": output, "returncode": rc,
        "is_conflict": "CONFLICT" in output,
        "is_test_fail": "TEST_FAIL" in output,
        "is_push_fail": "PUSH_FAIL" in output,
        "passed_commits": extract_commits(output, "COMMITS:"),
        "failed_commit": extract_single_value(output, "COMMIT:"),
    }


def run_step_cherry_pick(commits, target_branch, task_log_path):
    script_path = os.path.join(SCRIPTS_DIR, "step_cherry_pick.sh")
    commits_str = ",".join(commits)
    cmd_str = "bash {script} {commits} {branch} {repo} {test}".format(
        script=_quote(script_path), commits=_quote(commits_str),
        branch=_quote(target_branch), repo=_quote(REPO_PATH),
        test=_quote(TEST_COMMAND),
    )
    label = "step-cp {} -> {}".format(commits_str[:30], target_branch)
    output, rc, timed_out = run_command(
        cmd_str, TEST_TIMEOUT * len(commits) + 300, task_log_path, label=label,
    )
    if timed_out:
        return _error_result("Step cherry-pick timed out")
    return {
        "success": "STEP_SUCCESS" in output,
        "output": output, "returncode": rc,
        "is_partial": "STEP_PARTIAL" in output,
        "passed_commits": extract_commits(output, "PASSED:"),
        "failed_commits": extract_commits(output, "FAILED:"),
        "conflict_commits": extract_commits(output, "CONFLICT:"),
        "push_failed_commits": extract_commits(output, "PUSH_FAILED:"),
    }


def run_revert(commits, target_branch, task_log_path):
    script_path = os.path.join(SCRIPTS_DIR, "revert.sh")
    commits_str = ",".join(commits)
    cmd_str = "bash {script} {commits} {branch} {repo} {test}".format(
        script=_quote(script_path), commits=_quote(commits_str),
        branch=_quote(target_branch), repo=_quote(REPO_PATH),
        test=_quote(TEST_COMMAND),
    )
    label = "revert {} on {}".format(commits_str[:30], target_branch)
    output, rc, timed_out = run_command(cmd_str, TEST_TIMEOUT + 300, task_log_path, label=label)
    if timed_out:
        return _error_result("Revert timed out")
    return {
        "success": rc == 0 and "SUCCESS" in output and "PUSH_FAIL" not in output,
        "output": output, "returncode": rc,
        "is_conflict": "CONFLICT" in output,
        "is_test_fail": "TEST_FAIL" in output,
        "is_push_fail": "PUSH_FAIL" in output,
        "passed_commits": extract_commits(output, "COMMITS:"),
        "failed_commit": extract_single_value(output, "COMMIT:"),
    }


def run_test_only(task_log_path, target_branch=""):
    """
    Run tests. If target_branch is given, checkout that branch first,
    run tests, then switch back to the original branch.
    """
    orig_result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, cwd=REPO_PATH,
    )
    original_branch = orig_result.stdout.strip() if orig_result.returncode == 0 else ""

    if target_branch and target_branch != original_branch:
        cmd_str = (
            "git reset --hard HEAD 2>/dev/null || true && "
            "git checkout {branch} && "
            "{test}"
        ).format(branch=_quote(target_branch), test=TEST_COMMAND)
        test_branch = target_branch
    else:
        cmd_str = TEST_COMMAND
        test_branch = original_branch or "unknown"

    output, rc, timed_out = run_command(
        cmd_str, TEST_TIMEOUT, task_log_path,
        label="run-test on {}".format(test_branch),
    )

    if target_branch and target_branch != original_branch and original_branch:
        subprocess.run(
            ["git", "checkout", original_branch],
            capture_output=True, cwd=REPO_PATH, timeout=30,
        )

    if timed_out:
        return {
            "success": False,
            "output": output + "\n\nTIMEOUT ({}s)".format(TEST_TIMEOUT),
            "returncode": -1, "branch": test_branch,
        }
    return {"success": rc == 0, "output": output, "returncode": rc, "branch": test_branch}


def _extract_git_log(output):
    """Extract the 'Git Log (...)' section from script output."""
    lines = (output or "").splitlines()
    capturing = False
    log_lines = []
    for line in lines:
        if line.startswith("Git Log (") or line.startswith("Git Log("):
            capturing = True
            log_lines.append(line)
            continue
        if capturing:
            if line.strip() == "" and log_lines:
                break
            if line.startswith("STEP_") or line.startswith("SUCCESS") or line.startswith("PASSED:"):
                break
            log_lines.append(line)
    return "\n".join(log_lines) if log_lines else ""


def _error_result(msg):
    return {
        "success": False, "output": msg, "returncode": -1,
        "is_conflict": False, "is_test_fail": False,
        "is_no_change": False, "is_partial": False,
        "passed_commits": [], "failed_commits": [],
        "conflict_commits": [], "failed_commit": None,
    }


def extract_commits(output, prefix):
    pattern = r"{}([^\n]+)".format(re.escape(prefix))
    match = re.search(pattern, output or "")
    if match:
        raw = match.group(1).strip()
        parts = re.split(r"[,\s]+", raw)
        return [c.strip() for c in parts if c.strip()]
    return []


def extract_single_value(output, prefix):
    pattern = r"{}([^\n]+)".format(re.escape(prefix))
    match = re.search(pattern, output or "")
    if match:
        return match.group(1).strip()
    return None


# ======================== Task result handlers ========================

def _mention(task):
    """Return Slack @mention for the task owner."""
    uid = task.get("user", "")
    return "<@{}>".format(uid) if uid and uid != "unknown" else ""


def _admin_mentions():
    """Return Slack @mentions for all admin users."""
    return " ".join("<@{}>".format(uid) for uid in ADMIN_USER_IDS)


def _handle_test_or_infra_fail(say, ts, output):
    """Send AI analysis for real test failures, or @admin for infra errors."""
    if _is_infra_failure(output):
        admins = _admin_mentions()
        say("{} ⚠️ *Infrastructure/auth error detected.* Please check build environment.".format(
            admins) if admins else "⚠️ *Infrastructure/auth error detected.* Please check build environment.",
            thread_ts=ts)
    else:
        ai = analyze_test_failure(output)
        if ai:
            say("🤖 *AI suggestion:*\n\n{}".format(ai), thread_ts=ts)


def _task_elapsed(task):
    started = task.get("started_at")
    if not started:
        return ""
    return _fmt_elapsed(time.time() - started)


def handle_single_result(result, task):
    say, ts = task["say"], task["ts"]
    commit_id, target_branch = task["commits"][0], task["target_branch"]
    m = _mention(task)
    elapsed = _task_elapsed(task)

    if result.get("is_no_change"):
        say("{} ℹ️ *Cherry-Pick produced no changes*\n\n"
            "Commit `{}` may already exist on `{}`.".format(m, commit_id, target_branch), thread_ts=ts)
        return
    if result.get("success"):
        git_log = _extract_git_log(result["output"])
        msg = "{} ✅ *Cherry-Pick succeeded!* ({}) Please trigger alfred build manually.\n`{}` → `{}`".format(m, elapsed, commit_id, target_branch)
        if git_log:
            msg += "\n\n```{}```".format(git_log)
        say(msg, thread_ts=ts)
        return
    if result.get("is_conflict"):
        conflict_files = extract_single_value(result["output"], "FILES:") or "unknown"
        say("{} ⚠️ *Cherry-Pick conflict*\nConflict files: `{}`\n\n```{}```".format(
            m, conflict_files, get_output_tail(result["output"])), thread_ts=ts)
        ai = analyze_conflict(conflict_files, result["output"])
        if ai:
            say("🤖 *AI suggestion:*\n\n{}".format(ai), thread_ts=ts)
        return
    if result.get("is_test_fail"):
        say("{} ⚠️ *Cherry-Pick test failed* (rolled back)\n\n```{}```".format(
            m, get_output_tail(result["output"])), thread_ts=ts)
        _handle_test_or_infra_fail(say, ts, result["output"])
        return
    if result.get("is_push_fail"):
        say("{} ❌ *Push failed* (local commit reverted)\n\n```{}```".format(
            m, get_output_tail(result["output"])), thread_ts=ts)
        return
    say("{} ❌ *Failed:*\n\n```{}```".format(m, get_output_tail(result["output"])), thread_ts=ts)


def handle_batch_result(result, task):
    say, ts, commits = task["say"], task["ts"], task["commits"]
    m = _mention(task)
    elapsed = _task_elapsed(task)

    if result.get("success"):
        passed = result.get("passed_commits", [])
        git_log = _extract_git_log(result["output"])
        msg = "{} ✅ *Batch Cherry-Pick succeeded!* ({}) Please trigger alfred build manually.\n\nPassed: `{}`".format(
            m, elapsed, ", ".join(passed) if passed else ", ".join(commits))
        if git_log:
            msg += "\n\n```{}```".format(git_log)
        say(msg, thread_ts=ts)
        return
    if result.get("is_conflict"):
        fc = result.get("failed_commit") or "unknown"
        conflict_files = extract_single_value(result["output"], "FILES:") or "unknown"
        say("{} ⚠️ *Batch Cherry-Pick conflict!*\nConflict commit: `{}`\n"
            "Conflict files: `{}`\n\n```{}```\n\nAll rolled back".format(
                m, fc, conflict_files, get_output_tail(result["output"])), thread_ts=ts)
        ai = analyze_conflict(conflict_files, result["output"])
        if ai:
            say("🤖 *AI suggestion:*\n\n{}".format(ai), thread_ts=ts)
        return
    if result.get("is_test_fail"):
        say("{} ❌ *Batch test failed!* All rolled back\n\n```{}```".format(
            m, get_output_tail(result["output"])), thread_ts=ts)
        _handle_test_or_infra_fail(say, ts, result["output"])
        return
    if result.get("is_push_fail"):
        say("{} ❌ *Batch push failed!* All local commits reverted\n\n```{}```".format(
            m, get_output_tail(result["output"])), thread_ts=ts)
        return
    say("{} ❌ *Batch failed:*\n\n```{}```".format(m, get_output_tail(result["output"])), thread_ts=ts)


def handle_step_result(result, task):
    say, ts = task["say"], task["ts"]
    m = _mention(task)
    passed = result.get("passed_commits", [])
    failed = result.get("failed_commits", [])
    conflict = result.get("conflict_commits", [])
    push_failed = result.get("push_failed_commits", [])
    git_log = _extract_git_log(result["output"])

    elapsed = _task_elapsed(task)

    if result.get("success"):
        msg = "{} ✅ *All succeeded + pushed!* ({}) Please trigger alfred build manually.\n\nPassed: `{}`".format(m, elapsed, ", ".join(passed))
        if git_log:
            msg += "\n\n```{}```".format(git_log)
        say(msg, thread_ts=ts)
        return

    lines = []
    if result.get("is_partial"):
        lines.append("{} ⚠️ *Partial success*\n".format(m))
    else:
        lines.append("{} ❌ *Step failed*\n".format(m))
    if passed:
        lines.append("✅ Passed + pushed: `{}`".format(", ".join(passed)))
    if failed:
        lines.append("❌ Test failed: `{}`".format(", ".join(failed)))
    if conflict:
        lines.append("⚠️ Conflict: `{}`".format(", ".join(conflict)))
    if push_failed:
        lines.append("❌ Push failed (reverted): `{}`".format(", ".join(push_failed)))
    if git_log and passed:
        lines.append("\n```{}```".format(git_log))
    say("\n".join(lines), thread_ts=ts)

    if conflict:
        ai = analyze_conflict(", ".join(conflict), result["output"])
        if ai:
            say("🤖 *AI conflict suggestion:*\n\n{}".format(ai), thread_ts=ts)
    if failed:
        _handle_test_or_infra_fail(say, ts, result["output"])


def handle_revert_result(result, task):
    say, ts, commits = task["say"], task["ts"], task["commits"]
    m = _mention(task)
    elapsed = _task_elapsed(task)

    if result.get("success"):
        passed = result.get("passed_commits", [])
        git_log = _extract_git_log(result["output"])
        msg = "{} ✅ *Revert succeeded!* ({}) Please trigger alfred build manually.\n\nReverted: `{}`".format(
            m, elapsed, ", ".join(passed) if passed else ", ".join(commits))
        if git_log:
            msg += "\n\n```{}```".format(git_log)
        say(msg, thread_ts=ts)
        return
    if result.get("is_conflict"):
        fc = result.get("failed_commit") or "unknown"
        conflict_files = extract_single_value(result["output"], "FILES:") or "unknown"
        say("{} ⚠️ *Revert conflict!*\nConflict commit: `{}`\n"
            "Conflict files: `{}`\n\n```{}```\n\nAll rolled back".format(
                m, fc, conflict_files, get_output_tail(result["output"])), thread_ts=ts)
        ai = analyze_conflict(conflict_files, result["output"])
        if ai:
            say("🤖 *AI suggestion:*\n\n{}".format(ai), thread_ts=ts)
        return
    if result.get("is_test_fail"):
        say("{} ❌ *Revert test failed!* All rolled back\n\n```{}```".format(
            m, get_output_tail(result["output"])), thread_ts=ts)
        _handle_test_or_infra_fail(say, ts, result["output"])
        return
    if result.get("is_push_fail"):
        say("{} ❌ *Revert push failed!* All local reverts undone\n\n```{}```".format(
            m, get_output_tail(result["output"])), thread_ts=ts)
        return
    say("{} ❌ *Revert failed:*\n\n```{}```".format(m, get_output_tail(result["output"])), thread_ts=ts)


def handle_test_result(result, task):
    say, ts = task["say"], task["ts"]
    m = _mention(task)
    elapsed = _task_elapsed(task)
    branch = result.get("branch", "unknown")
    if result.get("success"):
        say("{} ✅ *Test passed!* ({})\n\nBranch: `{}`\n\n```{}```".format(
            m, elapsed, branch, get_output_tail(result["output"], 15)), thread_ts=ts)
    else:
        say("{} ❌ *Test failed* ({})\n\nBranch: `{}`\n\n```{}```".format(
            m, elapsed, branch, get_output_tail(result["output"])), thread_ts=ts)
        _handle_test_or_infra_fail(say, ts, result["output"])


def process_task(task):
    task_type = task["type"]
    say, ts = task["say"], task["ts"]
    target_branch = task.get("target_branch", "")
    task_log_path = task["log_file"]

    # Resolve refs now (in worker thread, preserving queue order)
    raw_refs = task.get("raw_refs", [])
    if raw_refs:
        resolved, had_errors = resolve_refs(raw_refs, say, ts)
        if had_errors:
            say("❌ Ref resolution failed, skipping task", thread_ts=ts)
            return {"success": False, "output": "ref resolution failed"}
        task["commits"] = resolved

    commits = task.get("commits", [])

    try:
        save_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=REPO_PATH, timeout=10,
        ).stdout.strip()
        save_branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, cwd=REPO_PATH, timeout=10,
        ).stdout.strip()
    except Exception:
        save_head, save_branch = "", ""
    task["save_head"] = save_head
    task["save_branch"] = save_branch

    log.info("[TASK] START type=%s commits=%s branch=%s user=%s(%s) log=%s",
             task_type, commits, target_branch,
             task.get("user_name", "?"), task.get("user", "?"), task_log_path)

    if task_type == "test":
        if target_branch:
            say("🧪 Running tests on branch `{}`...".format(target_branch), thread_ts=ts)
        else:
            say("🧪 Running tests on current branch...", thread_ts=ts)
        result = run_test_only(task_log_path, target_branch)
        handle_test_result(result, task)
    elif task_type == "single":
        say("🍒 Starting Cherry-Pick: `{}` → `{}`".format(commits[0], target_branch), thread_ts=ts)
        result = run_cherry_pick(commits[0], target_branch, task_log_path)
        handle_single_result(result, task)
    elif task_type == "batch":
        say("📦 Starting Batch Cherry-Pick: {} → `{}`".format(
            ", ".join("`{}`".format(c) for c in commits), target_branch), thread_ts=ts)
        result = run_batch_cherry_pick(commits, target_branch, task_log_path)
        handle_batch_result(result, task)
    elif task_type == "step":
        say("👣 Starting Step Cherry-Pick: {} → `{}`".format(
            ", ".join("`{}`".format(c) for c in commits), target_branch), thread_ts=ts)
        result = run_step_cherry_pick(commits, target_branch, task_log_path)
        handle_step_result(result, task)
    elif task_type == "revert":
        say("⏪ Starting Revert: {} on `{}`".format(
            ", ".join("`{}`".format(c) for c in commits), target_branch), thread_ts=ts)
        result = run_revert(commits, target_branch, task_log_path)
        handle_revert_result(result, task)
    else:
        say("❌ Unknown task type: {}".format(task_type), thread_ts=ts)
        result = {"success": False, "output": "unknown type"}

    log.info("[TASK] END type=%s success=%s user=%s",
             task_type, result.get("success", False), task.get("user_name", "?"))
    return result


# ======================== Background worker thread ========================

class QueueWorker(threading.Thread):
    def __init__(self, queue):
        super().__init__(daemon=True, name="cherry-pick-worker")
        self.queue = queue

    def run(self):
        while True:
            task = self.queue.get()
            with state_lock:
                if task in pending_tasks:
                    pending_tasks.remove(task)

            # Check hold: if this task is the hold target, pause
            if hold_before_ts and task.get("ts") == hold_before_ts:
                try:
                    mentions = []
                    if hold_requested_by:
                        mentions.append("<@{}>".format(hold_requested_by))
                    for admin_id in ADMIN_USER_IDS:
                        if admin_id != hold_requested_by:
                            mentions.append("<@{}>".format(admin_id))
                    mention_str = " ".join(mentions)
                    task["say"]("{} ⏸️ Queue paused before this task. Waiting for `continue`...".format(
                        mention_str).strip(), thread_ts=task.get("ts"))
                except Exception:
                    pass
                log.info("[HOLD] paused before task: %s", _task_summary(task))
                hold_event.wait()
                log.info("[HOLD] resumed")

            try:
                with state_lock:
                    global current_task
                    current_task = task
                    task["started_at"] = time.time()
                    task["pid"] = None

                result = process_task(task)

                with state_lock:
                    task["finished_at"] = time.time()
                    task["result_success"] = result.get("success", False)
                    task_history[task["ts"]] = task
                    if len(task_history) > MAX_HISTORY:
                        task_history.popitem(last=False)
                    current_task = None

            except Exception as e:
                log.exception("[WORKER] exception processing task")
                try:
                    task["say"]("❌ Task exception: {}".format(e), thread_ts=task.get("ts"))
                except Exception:
                    pass
                with state_lock:
                    current_task = None
            finally:
                self.queue.task_done()


# ======================== Slack event handlers ========================

def _fmt_elapsed(seconds):
    if seconds < 60:
        return "{:.0f}s".format(seconds)
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return "{}m{}s".format(m, s)
    h, m = divmod(m, 60)
    return "{}h{}m".format(h, m)


def _is_process_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _task_summary(t):
    """One-line task description."""
    user_name = t.get("user_name", "?")
    if t["type"] == "test":
        branch = t.get("target_branch", "")
        if branch:
            return "`run-test` on `{}` by {}".format(branch, user_name)
        return "`run-test` by {}".format(user_name)
    commits = t.get("commits", [])
    raw_refs = t.get("raw_refs", [])
    refs = commits if commits else raw_refs
    shown = ", ".join(c[:10] for c in refs)
    return "`{}` {} → `{}` by {}".format(
        t["type"], shown, t.get("target_branch", ""), user_name)


def _parse_refs_and_branch(text):
    """
    Parse 'ref1,ref2, ref3 branch_name' into ([refs], branch).
    Handles spaces around commas: 'a, b, c branch' -> (['a','b','c'], 'branch')
    The last token that doesn't look like part of a comma-list is the branch.
    """
    # Normalize: collapse "ref1, ref2" into "ref1,ref2"
    normalized = re.sub(r'\s*,\s*', ',', text.strip())
    parts = normalized.split()
    if len(parts) < 2:
        return [], ""
    # Last part is branch, everything before is comma-separated refs
    target_branch = parts[-1]
    refs_str = " ".join(parts[:-1])
    # If there are still spaces (shouldn't after normalization, but safety):
    refs_str = refs_str.replace(" ", ",")
    raw_refs = [r.strip() for r in refs_str.split(",") if r.strip()]
    return raw_refs, target_branch


def build_status_message():
    with state_lock:
        lines = ["*📊 Cherry-Pick Bot Status*\n"]

        if current_task:
            elapsed = time.time() - current_task.get("started_at", time.time())
            pid = current_task.get("pid")
            alive = _is_process_alive(pid)

            status_icon = "🟢" if alive else "🔴"
            proc_status = "running" if alive else ("not started" if not pid else "ended/error")

            lines.append("▶️ *Current task:* {}".format(_task_summary(current_task)))
            lines.append("   {} Process: {} (PID: {}, elapsed {})".format(
                status_icon, proc_status, pid or "-", _fmt_elapsed(elapsed)))

            log_path = current_task.get("log_file", "")
            log_tail = read_task_log_tail(log_path, 10) if log_path else "(no log)"
            if log_tail and log_tail != "(no log)":
                lines.append("   📋 *Latest output:*")
                lines.append("```{}```".format(log_tail))
        else:
            lines.append("▶️ *Current task:* none (idle)")

        if pending_tasks:
            lines.append("\n⏳ *Queue:* {} task(s) waiting".format(len(pending_tasks)))
            for idx, t in enumerate(pending_tasks, 1):
                wait_time = time.time() - t.get("queued_at", time.time())
                lines.append("  {}. {} (waiting {})".format(
                    idx, _task_summary(t), _fmt_elapsed(wait_time)))
        else:
            lines.append("\n⏳ *Queue:* empty")

        if hold_before_ts:
            hold_task_name = "unknown"
            for t in pending_tasks:
                if t.get("ts") == hold_before_ts:
                    hold_task_name = _task_summary(t)
                    break
            lines.append("\n⏸️ *Hold:* queue will pause before: {}".format(hold_task_name))

        if task_history:
            lines.append("\n*📜 Recent:*")
            for _, t in list(reversed(list(task_history.items())))[:5]:
                icon = "✅" if t.get("result_success") else "❌"
                elapsed_str = ""
                if "started_at" in t and "finished_at" in t:
                    elapsed_str = " ({})".format(
                        _fmt_elapsed(t["finished_at"] - t["started_at"]))
                lines.append("  {} {}{}".format(icon, _task_summary(t), elapsed_str))

        return "\n".join(lines)


def _build_task(task_type, commits, target_branch, say, ts, user_id):
    """Build task dict, resolve user name, generate log file."""
    user_name = resolve_user_name(user_id)
    info = commits[0] if commits else ""
    task_log_path = make_task_log_path(task_type, user_name, info)
    return {
        "type": task_type,
        "commits": commits,
        "target_branch": target_branch,
        "say": say,
        "ts": ts,
        "user": user_id,
        "user_name": user_name,
        "queued_at": time.time(),
        "log_file": task_log_path,
    }


BRANCH_PREFIX = os.environ.get("BRANCH_PREFIX", "sandbox/")


def _check_duplicate(task):
    """Check if the same refs+branch+type combo is already queued or running."""
    raw_refs = set(task.get("raw_refs", []) + task.get("commits", []))
    target = task.get("target_branch", "")
    task_type = task.get("type", "")

    if not raw_refs:
        return False

    with state_lock:
        all_tasks = list(pending_tasks)
        if current_task:
            all_tasks.append(current_task)

    for t in all_tasks:
        if t.get("type") != task_type or t.get("target_branch") != target:
            continue
        existing_refs = set(t.get("raw_refs", []) + t.get("commits", []))
        if raw_refs & existing_refs:
            return True
    return False


def _enqueue(task, say, ts):
    """Enqueue task + update pending_tasks + reply with queue overview."""
    target_branch = task.get("target_branch", "")
    if target_branch and not target_branch.startswith(BRANCH_PREFIX):
        say("❌ Target branch must start with `{}`. Got: `{}`".format(
            BRANCH_PREFIX, target_branch), thread_ts=ts)
        return

    if _check_duplicate(task):
        say("⚠️ Duplicate: same refs already in queue or running. Skipped.", thread_ts=ts)
        return

    with state_lock:
        pending_tasks.append(task)

        lines = []
        is_busy = current_task is not None
        ahead = len(pending_tasks) - 1

        if is_busy:
            elapsed = time.time() - current_task.get("started_at", time.time())
            lines.append("📥 Queued: {}".format(_task_summary(task)))
            lines.append("▶️ Running: {} (elapsed {})".format(
                _task_summary(current_task), _fmt_elapsed(elapsed)))
            if ahead > 0:
                lines.append("⏳ {} task(s) ahead:".format(ahead))
                for idx, pt in enumerate(pending_tasks[:-1], 1):
                    lines.append("  {}. {}".format(idx, _task_summary(pt)))
        else:
            lines.append("📥 Received: {}".format(_task_summary(task)))

    task_queue.put(task)
    say("\n".join(lines), thread_ts=ts)


@app.event("app_mention")
def handle_mention(event, say, logger):
    global BOT_ID

    if not BOT_ID:
        get_bot_id()

    user_id = event.get("user", "unknown")
    text = event.get("text") or ""
    ts = event.get("ts")
    thread_ts = event.get("thread_ts")
    channel = event.get("channel", "?")

    user_name = resolve_user_name(user_id)
    log.info("[MSG] user=%s(%s) channel=%s text=%s", user_name, user_id, channel, text[:200])

    clean_text = text
    if BOT_ID:
        clean_text = text.replace("<@{}>".format(BOT_ID), "").strip()
    clean_text = clean_text.replace("`", "")

    # --- status ---
    if "status" in clean_text.lower():
        say(build_status_message(), thread_ts=ts)
        return

    # --- help ---
    if "help" in clean_text.lower():
        say(
            "*🤖 Cherry-Pick Bot Commands:*\n\n"
            "• `cherry-pick <commit|Change-Id> <branch>` — Single cherry-pick\n"
            "• `batch-cp <ref1,ref2,...> <branch>` — Batch cherry-pick (test once)\n"
            "• `step-cp <ref1,ref2,...> <branch>` — Step cherry-pick (test each)\n"
            "• `revert <ref> <branch>` — Revert a single commit\n"
            "• `batch-revert <ref1,ref2,...> <branch>` — Revert multiple commits\n"
            "• `run-test [branch]` — Run tests (on specified or current branch)\n"
            "• `hold <N>` — Pause queue before task #N (tasks before it finish first)\n"
            "• `continue` — Resume paused queue\n"
            "• `cancel 0` — Cancel current running task (kills + rollback)\n"
            "• `cancel <N>` — Cancel queued task #N\n"
            "• `status` — Queue status + live output + held tasks\n"
            "_All commands accept: commit hash, Change-Id (I...), or Gerrit change# (e.g. 766210)_",
            thread_ts=ts,
        )
        return

    # --- cancel <number> (0 = current task, 1+ = queued) ---
    if "cancel" in clean_text.lower():
        parts = clean_text.lower().split()
        cancel_idx = None
        for i, p in enumerate(parts):
            if p == "cancel" and i + 1 < len(parts) and parts[i + 1].isdigit():
                cancel_idx = int(parts[i + 1])
                break

        if cancel_idx is None:
            say("❌ Format: `cancel <number>` — 0 for current task, 1+ for queued "
                "(use `status` to see numbers)", thread_ts=ts)
        elif cancel_idx == 0:
            with state_lock:
                if not current_task:
                    say("❌ No task is currently running", thread_ts=ts)
                else:
                    pid = current_task.get("pid")
                    cancelled_task = current_task
                    owner_mention = "<@{}>".format(cancelled_task.get("user", "")) if cancelled_task.get("user") else ""

            if cancelled_task:
                log.info("[CANCEL] killing current task pid=%s: %s by %s",
                         pid, _task_summary(cancelled_task), user_name)

                if pid:
                    try:
                        os.kill(pid, 9)
                        log.info("[CANCEL] killed pid %d", pid)
                    except ProcessLookupError:
                        log.info("[CANCEL] pid %d already dead", pid)
                    except Exception as e:
                        log.warning("[CANCEL] failed to kill pid %d: %s", pid, e)

                # Rollback repo: abort cherry-pick + reset to pre-task HEAD
                try:
                    subprocess.run(
                        ["git", "cherry-pick", "--abort"],
                        capture_output=True, cwd=REPO_PATH, timeout=10,
                    )
                    subprocess.run(
                        ["git", "revert", "--abort"],
                        capture_output=True, cwd=REPO_PATH, timeout=10,
                    )
                    save_head = cancelled_task.get("save_head", "")
                    save_branch = cancelled_task.get("save_branch", "")
                    if save_head:
                        subprocess.run(
                            ["git", "reset", "--hard", save_head],
                            capture_output=True, cwd=REPO_PATH, timeout=10,
                        )
                        log.info("[CANCEL] reset to save_head %s", save_head[:12])
                    else:
                        subprocess.run(
                            ["git", "reset", "--hard", "HEAD"],
                            capture_output=True, cwd=REPO_PATH, timeout=10,
                        )
                        log.warning("[CANCEL] no save_head, reset to HEAD (may not fully rollback)")
                    subprocess.run(
                        ["git", "clean", "-fd"],
                        capture_output=True, cwd=REPO_PATH, timeout=10,
                    )
                    if save_branch:
                        subprocess.run(
                            ["git", "checkout", save_branch],
                            capture_output=True, cwd=REPO_PATH, timeout=10,
                        )
                        log.info("[CANCEL] switched back to %s", save_branch)
                    log.info("[CANCEL] repo rolled back")
                except Exception as e:
                    log.warning("[CANCEL] rollback error: %s", e)

                say("✅ Cancelled current task: {} {}\nRepo rolled back.".format(
                    _task_summary(cancelled_task), owner_mention).rstrip(), thread_ts=ts)
        else:
            with state_lock:
                if cancel_idx < 1 or cancel_idx > len(pending_tasks):
                    say("❌ No task #{} in queue ({} task(s) waiting)".format(
                        cancel_idx, len(pending_tasks)), thread_ts=ts)
                else:
                    removed = pending_tasks.pop(cancel_idx - 1)
                    new_q = Queue()
                    while not task_queue.empty():
                        try:
                            t = task_queue.get_nowait()
                            if t is not removed:
                                new_q.put(t)
                            task_queue.task_done()
                        except Exception:
                            break
                    while not new_q.empty():
                        task_queue.put(new_q.get_nowait())

                    log.info("[CANCEL] task #%d cancelled: %s by %s",
                             cancel_idx, _task_summary(removed), user_name)
                    owner_mention = "<@{}>".format(removed.get("user", "")) if removed.get("user") else ""
                    say("✅ Cancelled task #{}: {} {}".format(
                        cancel_idx, _task_summary(removed), owner_mention).rstrip(), thread_ts=ts)
        return

    # --- hold <N> ---
    if "hold" in clean_text.lower() and "continue" not in clean_text.lower():
        parts = clean_text.lower().split()
        hold_idx = None
        for i, p in enumerate(parts):
            if p == "hold" and i + 1 < len(parts) and parts[i + 1].isdigit():
                hold_idx = int(parts[i + 1])
                break

        if hold_idx is None:
            say("❌ Format: `hold <N>` — pause queue before task #N", thread_ts=ts)
        else:
            with state_lock:
                global hold_before_ts, hold_requested_by
                if hold_idx < 1 or hold_idx > len(pending_tasks):
                    say("❌ No task #{} in queue ({} task(s) waiting)".format(
                        hold_idx, len(pending_tasks)), thread_ts=ts)
                else:
                    target_task = pending_tasks[hold_idx - 1]
                    hold_before_ts = target_task["ts"]
                    hold_requested_by = user_id
                    hold_event.clear()
                    log.info("[HOLD] will pause before task #%d: %s requested by %s(%s)",
                             hold_idx, _task_summary(target_task), user_name, user_id)
                    say("⏸️ Queue will pause before task #{}: {}\n"
                        "Requested by {}. Tasks before it will finish. Use `continue` to resume.".format(
                            hold_idx, _task_summary(target_task), user_name), thread_ts=ts)
        return

    # --- continue ---
    if "continue" in clean_text.lower():
        with state_lock:
            if hold_before_ts is None and hold_event.is_set():
                say("❌ Queue is not paused", thread_ts=ts)
            else:
                hold_before_ts = None
                hold_requested_by = None
                hold_event.set()
                log.info("[CONTINUE] queue resumed by %s", user_name)
                say("▶️ Queue resumed", thread_ts=ts)
        return

    # --- run-test [branch] ---
    if "run-test" in clean_text.lower():
        rt_tmp = clean_text.lower().replace("run-test", "", 1).strip()
        rt_branch = rt_tmp.split()[0] if rt_tmp.split() else ""
        if rt_branch:
            rt_branch = clean_text.split()[clean_text.lower().split().index("run-test") + 1]
        task = _build_task("test", [], rt_branch, say, ts, user_id)
        log.info("[QUEUE] type=test branch=%s user=%s", rt_branch or "(current)", user_name)
        _enqueue(task, say, ts)
        return

    # --- cherry-pick ---
    if "cherry-pick" in clean_text.lower():
        parts = clean_text.split()
        cp_idx = None
        for i, p in enumerate(parts):
            if p.lower() in ("cherry-pick", "!cherry-pick"):
                cp_idx = i
                break
        if cp_idx is not None and len(parts) > cp_idx + 2:
            raw_ref = parts[cp_idx + 1]
            target_branch = parts[cp_idx + 2]

            task = _build_task("single", [], target_branch, say, ts, user_id)
            task["raw_refs"] = [raw_ref]
            log.info("[QUEUE] type=single ref=%s branch=%s user=%s",
                     raw_ref, target_branch, user_name)
            _enqueue(task, say, ts)
        else:
            say("❌ Format: `cherry-pick <commit|Change-Id> <branch>`", thread_ts=ts)
        return

    # --- revert / batch-revert ---
    is_revert = "batch-revert" in clean_text.lower() or "revert" in clean_text.lower()
    if is_revert:
        is_batch_revert = "batch-revert" in clean_text.lower()
        tmp = clean_text
        for keyword in ("batch-revert", "revert"):
            tmp = tmp.replace(keyword, "", 1)
        tmp = tmp.strip()
        parts = tmp.split()

        raw_refs, target_branch = _parse_refs_and_branch(tmp)

        if not raw_refs or not target_branch:
            say("❌ Format: `revert <commit> <branch>` or `batch-revert <c1,c2,...> <branch>`",
                thread_ts=ts)
        elif not is_batch_revert and len(raw_refs) > 1:
            say("❌ `revert` takes a single commit. Use `batch-revert` for multiple.", thread_ts=ts)
        else:
            task = _build_task("revert", [], target_branch, say, ts, user_id)
            task["raw_refs"] = raw_refs
            log.info("[QUEUE] type=revert refs=%s branch=%s user=%s",
                     raw_refs, target_branch, user_name)
            _enqueue(task, say, ts)
        return

    # --- batch-cp / step-cp ---
    is_batch = "batch-cp" in clean_text.lower()
    is_step = "step-cp" in clean_text.lower()

    if is_batch or is_step:
        task_type = "step" if is_step else "batch"
        tmp = clean_text
        for keyword in ("!batch-cp", "batch-cp", "!step-cp", "step-cp"):
            tmp = tmp.replace(keyword, "")
        tmp = tmp.strip()
        parts = tmp.split()

        raw_refs, target_branch = _parse_refs_and_branch(tmp)

        if not raw_refs or not target_branch:
            say("❌ Format: `batch-cp <c1,c2,c3> <branch>` or `step-cp <c1,c2,c3> <branch>`",
                thread_ts=ts)
        else:
            task = _build_task(task_type, [], target_branch, say, ts, user_id)
            task["raw_refs"] = raw_refs
            log.info("[QUEUE] type=%s refs=%s branch=%s user=%s",
                     task_type, raw_refs, target_branch, user_name)
            _enqueue(task, say, ts)
        return

    say("🤔 Unrecognized command. Type `help` for available commands.", thread_ts=ts)


# ======================== Startup ========================

if __name__ == "__main__":
    if not SLACK_BOT_TOKEN or not SLACK_APP_TOKEN:
        log.error("Missing SLACK_BOT_TOKEN or SLACK_APP_TOKEN")
        sys.exit(1)

    validate_repo_path()

    bot_id = get_bot_id()

    worker = QueueWorker(task_queue)
    worker.start()

    cleaner = LogCleaner(TASK_LOG_DIR, LOG_RETAIN_HOURS)
    cleaner.start()

    log.info("=" * 50)
    log.info("Slack Cherry-Pick Bot started")
    log.info("  Bot ID: %s", bot_id)
    log.info("  Repo: %s", REPO_PATH)
    log.info("  Test: %s", TEST_COMMAND[:80])
    log.info("  Shell Init: %s", SHELL_INIT)
    log.info("  Timeout: %ds", TEST_TIMEOUT)
    log.info("  Log dir: %s", LOG_DIR)
    log.info("  Log retain: %dh", LOG_RETAIN_HOURS)
    log.info("=" * 50)

    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()
