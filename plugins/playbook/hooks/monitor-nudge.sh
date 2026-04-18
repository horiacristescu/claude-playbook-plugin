#!/bin/bash
# monitor-nudge.sh — hook for injecting monitor nudges into agent context
#
# Registered as a NON-PLUGIN hook in .claude/settings.json.
# Works for both PostToolUse and UserPromptSubmit (reads event name from stdin).
# Current CC: PostToolUse additionalContext works, UserPromptSubmit is broken (bug #12151).
#
# Reads .agent/monitor/pids/$SESSION_ID/nudge.md
# If non-empty: atomic claim, emit additionalContext, log to chat_log
# If empty or missing: exit 0 silently (no-op)

set -e

# Find project root (walk up looking for .agent/tasks/)
find_root() {
    local dir="$PWD"
    while [ "$dir" != "/" ]; do
        [ -d "$dir/.agent/tasks" ] && echo "$dir" && return
        dir="$(dirname "$dir")"
    done
}

PROJECT_DIR=$(find_root)
[ -z "$PROJECT_DIR" ] && exit 0

# Read stdin to extract hook_event_name (PostToolUse or UserPromptSubmit)
INPUT=$(cat)
export EVENT_NAME=$(echo "$INPUT" | python3 -c "import sys,json; print(json.loads(sys.stdin.read() or '{}').get('hook_event_name','UserPromptSubmit'))" 2>/dev/null || echo "UserPromptSubmit")

SESSION_ID="${PLAYBOOK_SESSION_ID:-pid-$PPID}"
NUDGE_FILE="$PROJECT_DIR/.agent/monitor/pids/$SESSION_ID/nudge.md"

# No nudge file or empty — silent exit
[ -f "$NUDGE_FILE" ] || exit 0
[ -s "$NUDGE_FILE" ] || exit 0

# Atomic claim: mv to .delivering so monitor can't overwrite mid-read
DELIVERING="$NUDGE_FILE.delivering"
mv "$NUDGE_FILE" "$DELIVERING" 2>/dev/null || exit 0

# Read content
NUDGE_CONTENT=$(cat "$DELIVERING")
rm -f "$DELIVERING"

# Skip if content is empty after read
[ -z "$NUDGE_CONTENT" ] && exit 0

# Emit additionalContext — this is what gets injected into the agent's context
python3 -c "
import json, sys, os
nudge = sys.stdin.read().strip()
event = os.environ.get('EVENT_NAME', 'UserPromptSubmit')
out = {
    'hookSpecificOutput': {
        'hookEventName': event,
        'additionalContext': '[MONITOR] ' + nudge
    }
}
print(json.dumps(out))
" <<< "$NUDGE_CONTENT"

# Log to chat_log
LOCAL_LOG="$PROJECT_DIR/.agent/chat_log.md"
if [ -f "$LOCAL_LOG" ]; then
    TIMESTAMP=$(date -u +"%Y-%m-%d %H:%M:%S UTC")
    {
        echo "---"
        echo ""
        echo "**[MONITOR→$SESSION_ID]** [$TIMESTAMP] ($EVENT_NAME)"
        echo ""
        echo "$NUDGE_CONTENT"
        echo ""
    } >> "$LOCAL_LOG"
fi
