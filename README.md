# Slack Cherry-Pick Bot

Trigger Git Cherry-Pick + Test + Push via Slack @Bot

## Features

- **Slack Socket Mode** — Receive cherry-pick requests via `@Bot` commands
- **Async Queue** — All tasks queued, processed by a background daemon worker thread
- **Auto Test** — Run tests after each cherry-pick
- **Auto Rollback** — Conflict or test failure automatically rolls back and switches to original branch
- **AI Analysis** — On failure, Cursor Agent provides fix suggestions
- **Batch/Step Mode** — Batch or step-by-step cherry-pick support
- **Live Status** — Real-time queue, process status, and command output
- **Structured Logging** — Event log + per-task log files, auto-cleanup
- **Gerrit Support** — Accepts commit hashes, Change-Ids, and Gerrit change numbers
- **Cancel** — Cancel running or queued tasks

## Quick Start

### 1. Install dependencies
```bash
cd /path/to/cursorAgent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env: fill in Slack tokens and REPO_PATH
```

### 3. Start Bot

**Foreground (for debugging):**
```bash
cd /path/to/cursorAgent
source venv/bin/activate
sg docker -c "python3 slack_listener.py"
```

**Background (survives SSH disconnect):**
```bash
cd /path/to/cursorAgent
nohup sg docker -c "cd $(pwd) && venv/bin/python3 slack_listener.py" > logs/bot_stdout.log 2>&1 &
echo $!  # print PID
```

Stop: `kill $(pgrep -f slack_listener.py)`

## Slack Commands

| Command | Description |
|---------|-------------|
| `@Bot cherry-pick <ref> <branch>` | Single cherry-pick |
| `@Bot batch-cp <ref1,ref2,...> <branch>` | Batch cherry-pick (test once after all) |
| `@Bot step-cp <ref1,ref2,...> <branch>` | Step cherry-pick (test each individually) |
| `@Bot run-test [branch]` | Run tests (on specified or current branch) |
| `@Bot cancel 0` | Cancel current running task (kill + rollback) |
| `@Bot cancel <N>` | Cancel queued task #N |
| `@Bot status` | Queue status + live output |
| `@Bot help` | Show help |

_All commands accept: commit hash (full or abbreviated), Gerrit Change-Id (I...), or Gerrit change number (e.g. 766210)_

## Ref Resolution

The bot automatically resolves different input formats to full commit hashes:

| Input Format | Example | How it resolves |
|-------------|---------|-----------------|
| Full commit hash | `3e25c12fc4d70c1f...` (40 hex) | Pass through |
| Abbreviated hash | `195473c5dd65` (7+ hex) | `git rev-parse` locally, fetch if needed |
| Gerrit Change-Id | `I259af1231c590e2c...` (I + 40 hex) | `git log --grep`, fetch if needed |
| Gerrit change# | `766210` (4-8 digits) | `git ls-remote refs/changes/...`, picks latest patchset |

If a ref is not found locally, the bot automatically runs `git fetch` and retries.

## Async Queue

All commands (single / batch / step / test) enter a FIFO task queue, consumed by a single daemon worker thread:

1. User sends command -> Task queued, immediate acknowledgment with queue position
2. Worker picks task -> Execute cherry-pick + test
3. Success -> Push; Failure -> Rollback to original branch
4. Send result to Slack with @mention to task owner

`@Bot status` shows:
- Current running task + owner + PID + process alive status
- Live command output (last 10 lines)
- Pending queue with each task's details
- Recent completed tasks (last 5, with duration)

## Execution Flow

All commands run through `run_command()` for consistent environment:

```
slack_listener.py
  -> run_command(cmd_str, timeout, task_log_path)
    -> assert REPO_PATH exists
    -> subprocess.Popen(
        ["bash", "--login", "-c",
         "cd $REPO_PATH && source ~/.bashrc && actual_command"],
        cwd=REPO_PATH
      )
    -> stdout/stderr -> logs/tasks/{timestamp}_{type}_{user}_{info}.log
    -> PID stored in current_task (for status queries)
    -> proc.wait(timeout) -> auto-kill on timeout
```

**Triple cwd guarantee:**
1. Startup `validate_repo_path()` — verify REPO_PATH is a valid git repo
2. `Popen(cwd=REPO_PATH)` — process-level cwd
3. `cd $REPO_PATH && ...` — shell-level cd

**Environment:** `bash --login` + `source ~/.bashrc` (configurable via `SHELL_INIT`) ensures PATH, dazel, etc. are available.

## Logging

### Directory Structure

```
logs/
├── bot.log                                        # Event log (10MB rotating, 5 backups)
└── tasks/
    ├── 20260311_143052_single_AlvinChen_abc123.log # Per-task full command output
    ├── 20260311_150230_batch_JohnDoe_def456.log
    └── 20260311_160012_test_AlvinChen.log
```

### bot.log — Event Timeline

All events logged for easy grep:

```
2026-03-11 14:30:52 | INFO  | [MSG] user=Alvin Chen(U07ABC) channel=C01XYZ text=cherry-pick abc123 release/6.0
2026-03-11 14:30:52 | INFO  | [QUEUE] type=single commit=abc123 branch=release/6.0 user=Alvin Chen
2026-03-11 14:30:53 | INFO  | [TASK] START type=single commits=['abc123'] branch=release/6.0 user=Alvin Chen
2026-03-11 14:30:53 | INFO  | [CMD] label=cherry-pick abc123 -> release/6.0 | timeout=3720s
2026-03-11 14:30:53 | INFO  | [CMD] started pid=54321 log=logs/tasks/20260311_143052_single_AlvinChen_abc123.log
2026-03-11 14:35:10 | INFO  | [CMD] finished pid=54321 rc=0 timed_out=False output_len=12345
2026-03-11 14:35:10 | INFO  | [TASK] END type=single success=True user=Alvin Chen
```

| Tag | Content |
|-----|---------|
| `[MSG]` | User message: user_id, channel, text |
| `[QUEUE]` | Task queued: type, commits, branch, user |
| `[TASK]` | Task lifecycle: START/END, success, user |
| `[CMD]` | Command execution: label, timeout, PID, return code, output length |
| `[RESOLVE]` | Ref resolution: Change-Id/change#/commit -> full hash |
| `[FETCH]` | Git fetch operations |
| `[AI]` | AI analysis calls and responses |
| `[CANCEL]` | Task cancellation |
| `[WORKER]` | Worker exceptions: full traceback |

### Auto Cleanup

`LogCleaner` daemon thread checks every hour, deletes task log files older than the retention period.

```bash
LOG_RETAIN_HOURS=36  # Default 36 hours, configurable in .env
```

`bot.log` uses `RotatingFileHandler`: 10MB per file, 5 backups (~50MB max).

## Scripts

### execute_cherry_pick.sh

**Single Cherry-Pick script**

```bash
bash execute_cherry_pick.sh <commit> <target-branch> [repo-path] [test-command]
```

**Flow:**
1. Record original branch
2. Clean workspace
3. Checkout target branch
4. Cherry-pick commit (preserves original message)
5. Run tests (real-time output via tee)
6. Success -> Push (`--force-with-lease`); Failure -> Rollback + switch back
7. Push failure -> Revert local commit

**Return codes:** `0` = success, `1` = test/push fail, `2` = conflict

---

### batch_cherry_pick.sh

**Batch Cherry-Pick script** — each commit is an individual cherry-pick (no squash)

```bash
bash batch_cherry_pick.sh "c1,c2,c3" <target-branch> [repo-path] [test-command]
```

- All commits cherry-picked individually (original messages preserved)
- Tests run once after all commits
- Failure (test or push) -> All rolled back

---

### step_cherry_pick.sh

**Step-by-Step Cherry-Pick script** — cherry-pick, test, and push each commit individually

```bash
bash step_cherry_pick.sh "c1,c2,c3" <target-branch> [repo-path] [test-command]
```

- Each commit: cherry-pick -> test -> push immediately
- Failure -> Rollback that commit, continue to next
- Push failure -> Revert that commit, continue to next
- Final summary: passed (pushed), failed, conflict, push-failed

---

## Test Command Config

Default test command:
```bash
dazel test --config=drive-qnx_6_0_8_0 --cache_test_results=0 --remote_download_outputs=all --test_env=TEST_UNDECLARED_OUTPUTS_FORCE_UPLOAD=1 --config=remote_exec //tests/apps/roadrunner/RR2/System/RR_l2pp_dag_tests:Roadrunner_2_0.l2pp_amo
```

Override via `.env`:
```bash
TEST_COMMAND=dazel test --config=... 2>&1
TEST_TIMEOUT=3600
```

Or via script argument:
```bash
bash scripts/execute_cherry_pick.sh <commit> <branch> <repo> "your test command"
```

`@Bot run-test [branch]` runs the test command on the specified (or current) branch without cherry-picking.

---

## Directory Structure

```
cursorAgent/
├── slack_listener.py           # Main bot (async queue + AI + logging)
├── scripts/
│   ├── execute_cherry_pick.sh  # Single cherry-pick
│   ├── batch_cherry_pick.sh    # Batch cherry-pick
│   └── step_cherry_pick.sh     # Step cherry-pick
├── tests/
│   ├── conftest.py             # Shared test fixtures (temp git repo)
│   ├── test_unit.py            # Unit tests for pure functions
│   ├── test_git_scripts.py     # Integration tests for shell scripts
│   ├── test_log_and_queue.py   # Log, queue, and cancel tests
│   ├── test_ai_handlers.py     # AI suggestion handler tests
│   └── test_changeid_and_runtest.py  # Change-Id, change#, run-test tests
├── logs/                       # Log directory (auto-created, gitignored)
│   ├── bot.log                 # Event log
│   └── tasks/                  # Per-task logs
├── .env                        # Environment variables (sensitive, gitignored)
├── .env.example                # Environment variable template
├── requirements.txt            # Python dependencies
└── README.md
```

## Environment Variables (.env)

```bash
# Required
SLACK_BOT_TOKEN=xoxb-xxx
SLACK_APP_TOKEN=xapp-xxx
SLACK_SIGNING_SECRET=xxx
REPO_PATH=/path/to/repo

# Optional
TEST_COMMAND=dazel test --config=... 2>&1  # Test command
TEST_TIMEOUT=3600                           # Test timeout (seconds)
SHELL_INIT=source ~/.bashrc                 # Shell init command
LOG_RETAIN_HOURS=36                         # Task log retention (hours)
AGENT_BIN=/path/to/agent                    # Cursor Agent CLI path
AGENT_TIMEOUT=300                           # Agent timeout (seconds)
AGENT_MODEL=gpt-5.3-codex-fast             # Agent model
```

---

## License

MIT
