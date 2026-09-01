#!/usr/bin/env bash
# Set up the 15-minute local schedule on this Mac.
#
# Stores your Telegram credentials in ~/.config/bb-job-tracker/env at mode 600
# (readable only by you), installs a launchd agent, and starts it. The token is
# read from a hidden prompt — it never reaches your shell history.
#
#   ./setup_local.sh

set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.ataghipourfard.bb-job-tracker"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
ENV_DIR="$HOME/.config/bb-job-tracker"
ENV_FILE="$ENV_DIR/env"
INTERVAL=900   # seconds — 15 minutes

api() { curl -sS --max-time 20 "https://api.telegram.org/bot${TOKEN}/$1" "${@:2}"; }

echo "bb-job-tracker — local schedule setup"
echo

[ -x "$REPO/.venv/bin/python" ] || { echo "Missing $REPO/.venv — run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2; exit 1; }

# ---- credentials -----------------------------------------------------------
printf 'Telegram bot token (input hidden): '
read -rs TOKEN; printf '\n'
[ -n "$TOKEN" ] || { echo "No token entered." >&2; exit 1; }

api getMe | /usr/bin/python3 -c '
import json,sys
d=json.load(sys.stdin)
if not d.get("ok"): print("  Telegram rejected this token:",d.get("description")); sys.exit(1)
print("  Bot: @%s" % d["result"]["username"])
' || { echo "Get a valid token from @BotFather and try again." >&2; exit 1; }

CHATS=$(api getUpdates | /usr/bin/python3 -c '
import json,sys
d=json.load(sys.stdin); seen={}
for u in d.get("result",[]):
    m=u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
    c=m.get("chat")
    if c: seen[c["id"]]=c
for cid,c in seen.items():
    label=c.get("title") or " ".join(filter(None,[c.get("first_name"),c.get("last_name")])) or "?"
    print("%s\t%s" % (cid,label))
')

if [ -n "$CHATS" ]; then
    echo "  Chats that have messaged this bot:"
    echo "$CHATS" | awk -F'\t' '{printf "    %-18s %s\n",$1,$2}'
    CHAT_ID=$(echo "$CHATS" | head -1 | cut -f1)
else
    CHAT_ID=""
fi
printf 'Chat ID to alert%s: ' "${CHAT_ID:+ [$CHAT_ID]}"
read -r PICK
[ -n "$PICK" ] && CHAT_ID="$PICK"
[ -n "$CHAT_ID" ] || { echo "No chat ID. Message your bot first, then re-run." >&2; exit 1; }

echo "  Sending a test message..."
api sendMessage -d "chat_id=${CHAT_ID}" -d "text=bb-job-tracker: local 15-minute schedule is active." \
  | /usr/bin/python3 -c '
import json,sys
d=json.load(sys.stdin)
if not d.get("ok"): print("  Failed:",d.get("description")); sys.exit(1)
print("  Delivered.")
' || exit 1

# ---- write the env file ----------------------------------------------------
mkdir -p "$ENV_DIR"; chmod 700 "$ENV_DIR"
umask 077
cat > "$ENV_FILE" <<ENVEOF
TELEGRAM_BOT_TOKEN=$TOKEN
TELEGRAM_CHAT_ID=$CHAT_ID
ENVEOF
chmod 600 "$ENV_FILE"
unset TOKEN
echo "  Credentials written to $ENV_FILE (mode 600)."

# ---- install and load the launchd agent ------------------------------------
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/bb-job-tracker"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$REPO/run_local.sh</string>
    </array>

    <!-- Run once as soon as the agent loads, i.e. every time you log in. -->
    <key>RunAtLoad</key>
    <true/>

    <!-- ...and every 15 minutes thereafter. -->
    <key>StartInterval</key>
    <integer>$INTERVAL</integer>

    <key>WorkingDirectory</key>
    <string>$REPO</string>

    <key>StandardOutPath</key>
    <string>$HOME/Library/Logs/bb-job-tracker/launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/Library/Logs/bb-job-tracker/launchd.err.log</string>

    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
PLISTEOF

plutil -lint "$PLIST" >/dev/null || { echo "Generated plist is invalid." >&2; exit 1; }

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/$LABEL"

echo "  Agent installed at $PLIST and started."
echo
echo "It now runs every 15 minutes, and again automatically each time you log in."
echo
echo "  Status:  launchctl print gui/$(id -u)/$LABEL | head -20"
echo "  Log:     tail -f $HOME/Library/Logs/bb-job-tracker/run.log"
echo "  Run now: launchctl kickstart -k gui/$(id -u)/$LABEL"
echo "  Stop:    launchctl bootout gui/$(id -u)/$LABEL"
