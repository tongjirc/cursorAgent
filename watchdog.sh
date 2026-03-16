#!/bin/bash
#
# Watchdog: auto-restart cherry-pick bot if it crashes.
# Usage: nohup bash watchdog.sh > logs/watchdog.log 2>&1 &
#

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$BOT_DIR/bot.pid"
LOG_FILE="$BOT_DIR/logs/bot_stdout.log"
VENV_PYTHON="$BOT_DIR/venv/bin/python3"
BOT_SCRIPT="$BOT_DIR/slack_listener.py"
CHECK_INTERVAL=30
MAX_RESTARTS=10
RESTART_WINDOW=3600

restart_count=0
window_start=$(date +%s)

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') | WATCHDOG | $1"
}

is_running() {
    if [ -f "$PID_FILE" ]; then
        pid=$(cat "$PID_FILE" 2>/dev/null)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
    fi
    pgrep -f "python3.*slack_listener.py" > /dev/null 2>&1
}

start_bot() {
    local now=$(date +%s)
    local elapsed=$((now - window_start))

    if [ $elapsed -gt $RESTART_WINDOW ]; then
        restart_count=0
        window_start=$now
    fi

    if [ $restart_count -ge $MAX_RESTARTS ]; then
        log "ERROR: $MAX_RESTARTS restarts in ${RESTART_WINDOW}s. Giving up. Check logs."
        exit 1
    fi

    log "Starting bot (restart #$restart_count)..."
    cd "$BOT_DIR"
    sg docker -c "cd $BOT_DIR && $VENV_PYTHON $BOT_SCRIPT" >> "$LOG_FILE" 2>&1 &
    local new_pid=$!
    echo "$new_pid" > "$PID_FILE"
    log "Bot started with PID $new_pid"
    restart_count=$((restart_count + 1))
    sleep 5

    if ! kill -0 "$new_pid" 2>/dev/null; then
        log "ERROR: Bot died immediately after start. Check $LOG_FILE"
        return 1
    fi
    log "Bot is alive"
    return 0
}

log "Watchdog started. Checking every ${CHECK_INTERVAL}s. Max $MAX_RESTARTS restarts per ${RESTART_WINDOW}s."

if ! is_running; then
    start_bot
fi

while true; do
    sleep "$CHECK_INTERVAL"
    if ! is_running; then
        log "Bot is NOT running. Restarting..."
        start_bot
    fi
done
