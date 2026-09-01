#!/usr/bin/env bash
# Find your Telegram chat ID and store it as a repository secret.
#
# Your bot token is read from a hidden prompt, used only for direct calls to
# api.telegram.org, and never written to disk, to your shell history, or to
# this repository.
#
#   ./setup_telegram.sh

set -euo pipefail

command -v gh >/dev/null || { echo "gh CLI not found — install it first." >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 not found." >&2; exit 1; }

# gh prefers $GITHUB_TOKEN / $GH_TOKEN over its own stored login, so a stale
# one exported from a shell profile makes every call fail with "Bad
# credentials" even though `gh auth login` is perfectly healthy. If that is
# happening, fall back to the stored login for the calls we make here.
GH=(gh)
if ! gh auth status >/dev/null 2>&1; then
    if env -u GITHUB_TOKEN -u GH_TOKEN gh auth status >/dev/null 2>&1; then
        GH=(env -u GITHUB_TOKEN -u GH_TOKEN gh)
        echo "  Note: ignoring a stale GITHUB_TOKEN/GH_TOKEN from your environment."
        echo "        Your stored gh login works; consider removing that export."
        echo
    else
        echo "gh is not authenticated. Run: gh auth login -h github.com" >&2
        exit 1
    fi
fi

api() { curl -sS --max-time 20 "https://api.telegram.org/bot${TOKEN}/$1" "${@:2}"; }

printf 'Telegram bot token (input hidden): '
read -rs TOKEN
printf '\n\n'
[ -n "$TOKEN" ] || { echo "No token entered." >&2; exit 1; }

# 1. Is the token itself valid?
if ! api getMe | python3 -c '
import json,sys
d=json.load(sys.stdin)
if not d.get("ok"):
    print("  Telegram rejected this token:", d.get("description","unknown error")); sys.exit(1)
print("  Bot: @%s (%s)" % (d["result"]["username"], d["result"]["first_name"]))
'; then
    echo
    echo "That token is not valid. Get a fresh one from @BotFather (/mybots ->" >&2
    echo "your bot -> API Token), then run this again." >&2
    exit 1
fi

# 2. Which chats has the bot actually seen?
echo
echo "Looking for chats that have messaged your bot..."
CHATS=$(api getUpdates | python3 -c '
import json,sys
d=json.load(sys.stdin)
seen={}
for u in d.get("result",[]):
    msg=u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
    c=msg.get("chat")
    if c: seen[c["id"]]=c
for cid,c in seen.items():
    label=c.get("title") or " ".join(filter(None,[c.get("first_name"),c.get("last_name")])) or c.get("username") or "?"
    print("%s\t%s\t%s" % (cid, c.get("type","?"), label))
')

if [ -z "$CHATS" ]; then
    cat >&2 <<'MSG'

  No chats found. This is the usual cause of "chat not found": a bot cannot
  open a conversation with you, so you have to message it first.

  Open Telegram, find your bot, send it any message (e.g. "hi"), then run
  this script again. If you want alerts in a group, add the bot to the group
  and send a message there instead.
MSG
    exit 1
fi

echo
echo "  ID                    TYPE       NAME"
echo "$CHATS" | awk -F'\t' '{printf "  %-21s %-10s %s\n", $1, $2, $3}'
echo

CHAT_ID=$(echo "$CHATS" | head -1 | cut -f1)
COUNT=$(echo "$CHATS" | wc -l | tr -d ' ')
if [ "$COUNT" -gt 1 ]; then
    printf 'Chat ID to send alerts to [%s]: ' "$CHAT_ID"
    read -r PICK
    [ -n "$PICK" ] && CHAT_ID="$PICK"
fi

# 3. Prove it works before saving it.
echo "Sending a test message to $CHAT_ID ..."
if ! api sendMessage -d "chat_id=${CHAT_ID}" \
        -d "text=bb-job-tracker is wired up. Job alerts will arrive here." \
        | python3 -c '
import json,sys
d=json.load(sys.stdin)
if not d.get("ok"):
    print("  Failed:", d.get("description","unknown error")); sys.exit(1)
print("  Delivered — check your phone.")
'; then
    exit 1
fi

# 4. Save it. The chat ID is an address, not a credential; the token below is
#    only re-sent if you ask for it.
echo
printf '%s' "$CHAT_ID" | "${GH[@]}" secret set TELEGRAM_CHAT_ID
echo "  TELEGRAM_CHAT_ID updated."

printf '\nAlso re-save the bot token as TELEGRAM_BOT_TOKEN? [y/N] '
read -r ANSWER
if [ "$ANSWER" = "y" ] || [ "$ANSWER" = "Y" ]; then
    printf '%s' "$TOKEN" | "${GH[@]}" secret set TELEGRAM_BOT_TOKEN
    echo "  TELEGRAM_BOT_TOKEN updated."
fi

unset TOKEN
echo
echo "Done. Trigger a run to confirm — two Target listings are still queued:"
echo "  gh workflow run \"Job tracker\" --ref main"
