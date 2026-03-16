# Slack Cherry-Pick Bot

Manage cherry-pick queues, automated CP/revert, and manual handoff via Slack `@Bot` commands.

> **For end users:** see [USER_GUIDE.md](USER_GUIDE.md) for how to interact with the bot (bilingual EN/CN).

---

## Deployment Guide

### Prerequisites

| Requirement | Why |
|------------|-----|
| Linux machine (Ubuntu 20.04+) | Bot runs as a background service |
| Python 3.10+ | Runtime |
| Git | Cherry-pick operations |
| Docker group membership | `dazel` (the test tool) requires Docker |
| Network access to Gerrit + Slack | Git fetch/push, Slack WebSocket |
| Slack App with Socket Mode | Bot token, App token, Signing secret |

### Step 1: Clone and install

```bash
git clone <repo-url> ~/cursorAgent
cd ~/cursorAgent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 2: Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your values:

```bash
# === Required ===
SLACK_BOT_TOKEN=xoxb-xxx           # Bot User OAuth Token (Slack App settings → OAuth)
SLACK_APP_TOKEN=xapp-xxx           # App-Level Token (Slack App settings → Basic Information → App-Level Tokens)
SLACK_SIGNING_SECRET=xxx           # Signing Secret (Slack App settings → Basic Information)
REPO_PATH=/path/to/ndas            # Absolute path to the git repo (must be cloned already)

# === Recommended ===
ADMIN_USER_IDS=U090D47C140         # Comma-separated Slack user IDs — receive DM on bot start/stop
BRANCH_PREFIX=sandbox/             # Target branch must start with this (safety guard)
SHELL_INIT=source /path/to/repo/scripts/envsetup.sh 2>/dev/null  # Shell init for dazel, PATH, etc.

# === Optional ===
TEST_COMMAND=dazel test --config=drive-qnx_6_0_8_0 ...  # Override default test command
TEST_TIMEOUT=3600                  # Test timeout (seconds, default 3600)
MANUAL_TIMEOUT=1800                # Manual mode work phase (seconds, default 1800 = 30min)
FAILURE_WAIT_TIMEOUT=300           # Takeover claim wait (seconds, default 300 = 5min)
MAX_INFRA_RETRIES=2                # Auto-retry on auth/network errors (default 2)
LOG_RETAIN_HOURS=36                # Task log retention (default 36h)
NOTIFY_CHANNEL=C01ABCDEF           # Optional: also post startup/shutdown to this channel

# === AI Analysis (optional) ===
AGENT_BIN=/home/user/.local/bin/agent   # Cursor Agent CLI path
AGENT_TIMEOUT=300
AGENT_MODEL=
```

### Step 3: Prepare the git repo

```bash
cd /path/to/ndas
git remote -v                       # Verify origin points to Gerrit
git fetch origin                    # Make sure remote refs are available
```

The bot needs:
- Push access to `origin` (for `sandbox/` branches)
- SSH key configured for Gerrit (for `git push`, `ssh gerrit query`)

### Step 4: Set up the Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → Create New App (or use existing)
2. **Socket Mode**: Enable it, create an App-Level Token (`xapp-...`)
3. **Event Subscriptions**: Subscribe to `app_mention` event
4. **OAuth Scopes** (Bot Token Scopes):
   - `app_mentions:read` — receive @mentions
   - `chat:write` — send messages
   - `users:read` — resolve user names
   - `im:write` — send DMs to admins
5. Install to workspace, copy Bot User OAuth Token (`xoxb-...`)

### Step 5: Install systemd service (recommended)

```bash
mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/cherry-pick-bot.service << 'EOF'
[Unit]
Description=Slack Cherry-Pick Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/YOUR_USER/cursorAgent
ExecStart=/usr/bin/sg docker -c "/home/YOUR_USER/cursorAgent/venv/bin/python3 /home/YOUR_USER/cursorAgent/slack_listener.py"
Restart=on-failure
RestartSec=10
StandardOutput=append:/home/YOUR_USER/cursorAgent/logs/bot_stdout.log
StandardError=append:/home/YOUR_USER/cursorAgent/logs/bot_stdout.log

[Install]
WantedBy=default.target
EOF
```

Replace `YOUR_USER` with your actual username. Then:

```bash
systemctl --user daemon-reload
systemctl --user enable cherry-pick-bot    # auto-start on login
```

> **Note:** To keep the service running after SSH disconnect, enable lingering:
> ```bash
> sudo loginctl enable-linger $(whoami)
> ```

---

## Service Management

### Start / Stop / Restart

```bash
systemctl --user start cherry-pick-bot      # start
systemctl --user stop cherry-pick-bot       # stop
systemctl --user restart cherry-pick-bot    # restart (after code changes)
```

### Check status

```bash
systemctl --user status cherry-pick-bot     # service status + recent logs
```

### View logs

```bash
# Systemd journal (all output)
journalctl --user -u cherry-pick-bot -f               # follow live
journalctl --user -u cherry-pick-bot --since "1h ago"  # last hour

# Bot event log (structured)
tail -f ~/cursorAgent/logs/bot.log

# Specific task log
ls ~/cursorAgent/logs/tasks/                           # list all task logs
tail ~/cursorAgent/logs/tasks/20260316_*.log           # view specific task
```

### After code changes

```bash
cd ~/cursorAgent
git pull                                               # or apply your changes
systemctl --user restart cherry-pick-bot
systemctl --user status cherry-pick-bot                # verify it started
```

### Troubleshooting

| Symptom | Check |
|---------|-------|
| Bot not responding in Slack | `systemctl --user status cherry-pick-bot` — is it running? |
| Service won't start | `journalctl --user -u cherry-pick-bot -n 50` — check error |
| Bot responds but CP fails | `tail ~/cursorAgent/logs/tasks/*.log` — check task output |
| "docker" permission error | Verify user is in docker group: `id -Gn \| grep docker` |
| SSH/Gerrit auth error | Verify SSH key: `ssh -p 29418 user@git-av.nvidia.com gerrit version` |
| Service dies after SSH disconnect | `sudo loginctl enable-linger $(whoami)` |

---

## Deploying to a New Machine

### Quick checklist

```
[ ] 1. Linux machine with Python 3.10+, git, docker group
[ ] 2. Clone repo: git clone <url> ~/cursorAgent
[ ] 3. Python venv: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
[ ] 4. Clone the ndas repo (or whatever REPO_PATH points to)
[ ] 5. Configure SSH key for Gerrit (git push access)
[ ] 6. Copy .env from existing deployment, update REPO_PATH
[ ] 7. Configure dazel / build environment (SHELL_INIT in .env)
[ ] 8. Install systemd service (see Step 5 above)
[ ] 9. Enable lingering: sudo loginctl enable-linger $(whoami)
[ ] 10. Start: systemctl --user enable --now cherry-pick-bot
[ ] 11. Test: @Bot status in Slack
```

### Detailed steps for new machine

**1. System packages**

```bash
sudo apt update && sudo apt install -y python3 python3-venv git
sudo usermod -aG docker $(whoami)   # add to docker group (relogin required)
```

**2. SSH key for Gerrit**

```bash
ssh-keygen -t ed25519 -C "cherry-pick-bot@$(hostname)"
cat ~/.ssh/id_ed25519.pub
# Add this public key to your Gerrit account → Settings → SSH Keys
# Test: ssh -p 29418 your_user@git-av.nvidia.com gerrit version
```

**3. Clone repos**

```bash
git clone ssh://your_user@git-av.nvidia.com:29418/ndas ~/ndas
git clone <bot-repo-url> ~/cursorAgent
```

**4. Set up bot**

```bash
cd ~/cursorAgent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env: set REPO_PATH=~/ndas, paste Slack tokens, set ADMIN_USER_IDS, etc.
```

**5. Set up build environment**

Make sure `dazel` works from the repo:

```bash
cd ~/ndas
source scripts/envsetup.sh
dazel version   # should print dazel version
```

Set `SHELL_INIT` in `.env` to the init command:

```bash
SHELL_INIT=source /home/YOUR_USER/ndas/scripts/envsetup.sh 2>/dev/null
```

**6. Install and start service**

```bash
# Create service file (see Step 5 in Deployment Guide above)
# Edit paths to match this machine
systemctl --user daemon-reload
systemctl --user enable --now cherry-pick-bot
sudo loginctl enable-linger $(whoami)
```

**7. Verify**

```bash
systemctl --user status cherry-pick-bot   # should show "active (running)"
tail -5 ~/cursorAgent/logs/bot_stdout.log # should show "Bolt app is running!"
# In Slack: @Bot status
```

### Migrating from another machine

If the bot was running on machine A and you want to move to machine B:

1. On machine A: `systemctl --user stop cherry-pick-bot`
2. On machine B: Follow "Deploying to a New Machine" above
3. Copy the `.env` file from A to B (contains Slack tokens)
4. Start on B: `systemctl --user start cherry-pick-bot`

> **Important:** Only one instance of the bot should run at a time. Running two instances will cause duplicate messages and race conditions on the git repo.

---

## Command Reference

### Cherry-Pick / Revert (automated)

| Command | Description |
|---------|-------------|
| `cherry-pick <ref> [branch]` | Single cherry-pick + test + push |
| `batch-cp <ref1,ref2,...> [branch]` | Batch cherry-pick, test once after all |
| `step-cp <ref1,ref2,...> [branch]` | Step cherry-pick, test each (recommended for multiple CLs) |
| `revert <ref> [branch]` | Revert a single commit |
| `batch-revert <ref1,ref2,...> [branch]` | Revert multiple commits |
| `run-test [branch]` | Run tests only |

### Manual Queue

| Command | Description |
|---------|-------------|
| `queue [branch]` | Reserve a spot. Bot notifies you when it's your turn |
| `takeover` | Claim the current slot — **anyone** can, not just the original user |
| `done` | Signal manual work is complete. Bot detects HEAD changes and reports |
| `skip` | Yield your turn, re-queue at the end |

### Queue Control

| Command | Description |
|---------|-------------|
| `urgent <any command>` | Insert at front of queue |
| `cancel 0` | Cancel current task (kill + rollback) |
| `cancel <N>` | Cancel queued task #N |
| `hold <N>` / `continue` | Pause / resume queue |
| `set-branch <branch>` | Set default branch (commands can omit branch after) |
| `status` | Queue status + live output |
| `help` | Show all commands |

### Ref Formats

All commands accept: full commit hash, abbreviated hash (7+ hex), Gerrit Change-Id (`I...`), or Gerrit change number (e.g. `766210`). Branch can be omitted if `set-branch` is configured.

---

## How Manual Mode Works

```
Phase 1: CLAIM (5min timeout)
  Bot @mentions user → anyone replies "takeover" → Phase 2
  No response after 5min → skip to next task

Phase 2: WORK (30min + auto-extend)
  User works locally (cherry-pick, resolve, test, push)
  Bot polls remote HEAD every 30s via git ls-remote
  If HEAD changes → timer auto-resets to 30min
  User replies "done" → Bot reports HEAD changes + git log
  Timer expires with no activity → skip to next task
```

- **Anyone can claim** — teammates can help.
- **No need to extend** — timer auto-extends on push activity.
- **Same flow everywhere** — manual queue, conflict, test failure all use takeover → done.

---

## Infrastructure Failure Auto-Retry

**Auto-retried** (up to 2 times, 10s delay): auth errors, network timeouts, build system login failures.

**NOT retried:** Real cherry-pick conflicts, real test failures, push failures.

---

## Bot Guidance System

After every failure, Bot provides contextual next-step suggestions:

| Situation | Bot Suggests |
|-----------|-------------|
| Cherry-pick conflict | `takeover` to resolve, `cancel 0` to skip, or fix & re-submit |
| Test failure | `takeover` to debug, `cancel 0` to skip, check flaky/infra |
| Push failure | `takeover` to handle, or re-send command to retry |
| Batch conflict | Use `step-cp` instead, or `takeover` |
| Revert conflict | `takeover`, consider reverting dependent commits |

---

## Architecture

```
cursorAgent/
├── slack_listener.py           # Main bot (Slack Socket Mode + queue worker)
├── watchdog.sh                 # Auto-restart watchdog (alternative to systemd)
├── scripts/
│   ├── cp_common.sh            # Shared shell helpers
│   ├── execute_cherry_pick.sh  # Single cherry-pick script
│   ├── batch_cherry_pick.sh    # Batch cherry-pick script
│   ├── step_cherry_pick.sh     # Step cherry-pick script
│   └── revert.sh              # Revert script
├── tests/                      # 216 tests
├── logs/
│   ├── bot.log                 # Event log (rotating, 10MB x 5)
│   └── tasks/                  # Per-task logs (auto-cleanup after 36h)
├── .env                        # Environment variables (not committed)
├── .env.example                # Template
├── requirements.txt
├── USER_GUIDE.md               # End-user guide (bilingual EN/CN)
└── README.md                   # This file (deployment + operations)
```

## License

MIT
