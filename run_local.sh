#!/bin/bash
# Local runner, invoked by the launchd agent every 15 minutes.
#
# launchd gives a process almost no environment, so everything here is an
# absolute path and the PATH is set explicitly. Credentials are read from
# ~/.config/bb-job-tracker/env, which setup_local.sh writes at mode 600.

export PATH="/opt/homebrew/bin:/opt/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# Derive the repo from this script's own location, so the same script works
# wherever the clone lives.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$REPO/.venv/bin/python"
ENV_FILE="$HOME/.config/bb-job-tracker/env"

# Logs and the lock live under $HOME, never in the repo: macOS denies launchd
# agents write access to USB-attached volumes, so a repo on an external disk
# would fail here even though it can be read and executed.
STATE_DIR="$HOME/Library/Logs/bb-job-tracker"
mkdir -p "$STATE_DIR"
LOG="$STATE_DIR/run.log"
LOCK="$STATE_DIR/run.lock"
MAX_LOG_LINES=2000

cd "$REPO" || exit 1

say() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# Only one run at a time — a slow run must not overlap the next tick.
if ! mkdir "$LOCK" 2>/dev/null; then
    if [ -f "$LOCK/pid" ] && ! kill -0 "$(cat "$LOCK/pid")" 2>/dev/null; then
        say "clearing a stale lock from pid $(cat "$LOCK/pid")"
        rm -rf "$LOCK"; mkdir "$LOCK" 2>/dev/null || exit 0
    else
        say "previous run still going; skipping this tick"
        exit 0
    fi
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

say "--- run start ---"

# Telegram credentials.
if [ -f "$ENV_FILE" ]; then
    set -a; . "$ENV_FILE"; set +a
else
    say "WARNING: $ENV_FILE missing — new listings will be found but not alerted."
    say "         Run ./setup_local.sh to create it."
fi

# Pick up anything a GitHub Actions run committed, so the two never diverge.
if ! git pull --rebase --autostash --quiet 2>>"$LOG"; then
    say "git pull failed; continuing with local state"
fi

"$PY" main.py >> "$LOG" 2>&1
STATUS=$?
say "main.py exited $STATUS"

if [ -n "$(git status --porcelain jobs.json geocache.json)" ]; then
    git add jobs.json geocache.json
    git commit -q -m "Update seen jobs ($(date -u '+%Y-%m-%d %H:%M UTC'), local)" 2>>"$LOG"
    if git push --quiet 2>>"$LOG"; then
        say "state committed and pushed"
    else
        say "push failed; the commit is local and will go up next run"
    fi
else
    say "no state change"
fi

# Keep the log from growing without bound.
if [ "$(wc -l < "$LOG")" -gt "$MAX_LOG_LINES" ]; then
    tail -n "$MAX_LOG_LINES" "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

say "--- run end ---"
exit $STATUS
