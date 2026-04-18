#!/bin/bash
# bootstrap.sh — one-shot monitor bootstrap. Run this FIRST when starting.
#
# Emits a single comprehensive briefing the monitor treats as its full context.
# The monitor should NEVER need to read files outside this output. Everything
# needed to understand the struggle is inlined here.
#
# Sections (in this order):
#   1. Identity (session id, JSONL path, pid dir, commands)
#   2. Monitor's own state (mind map, rules, prior session.md)
#   3. Project retros (project-level self-knowledge)
#   4. Project MIND_MAP.md (institutional memory)
#   5. Recent closed tasks (Intent + Debrief of last 5)
#   6. Active task.md (full content — the current struggle)
#   7. User steering patterns (grep chat_log.md for corrections)
#   8. Recent events (last hour of JSONL, compact extraction)
#   9. Commands (nudge, session.md append, WAIT)
#
# Usage:  bash .agent/monitor/bootstrap.sh [--session-id pid-XXX]

set -e

MONITOR_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$MONITOR_DIR/../.." && pwd)"

# ── Session ID resolution (no fallback — see T119 Thread 4) ─────────────
SESSION_ID="${PLAYBOOK_SESSION_ID:-}"
for arg in "$@"; do
    case "$arg" in --session-id=*) SESSION_ID="${arg#*=}";; esac
done
if [ -z "$SESSION_ID" ]; then
    echo "bootstrap.sh: error: no session id set" >&2
    echo "  Pass one of:" >&2
    echo "    bootstrap.sh --session-id=<front-agent-pid-or-uuid>" >&2
    echo "    PLAYBOOK_SESSION_ID=<front-agent-pid-or-uuid> bootstrap.sh" >&2
    exit 2
fi

# Validate SESSION_ID — prevents path traversal + sandbox profile injection.
# Playbook uses pid-based session IDs only (T112). pid-<digits>.
if ! echo "$SESSION_ID" | grep -Eq '^pid-[0-9]+$'; then
    echo "bootstrap.sh: error: invalid SESSION_ID '$SESSION_ID'" >&2
    echo "  Expected: pid-<digits>" >&2
    exit 2
fi

SLUG=$(echo "$PROJECT_ROOT" | tr '/' '-')
JSONL=$(ls -t ~/.claude/projects/$SLUG/*.jsonl 2>/dev/null | head -1)
PID_DIR="$MONITOR_DIR/pids/$SESSION_ID"
mkdir -p "$PID_DIR"

# ── 1. Identity ──────────────────────────────────────────────────────────
cat <<HEADER
# MONITOR BOOTSTRAP

You are the conversation monitor for the project at:
  $PROJECT_ROOT

Front agent session ID: $SESSION_ID
Front agent JSONL:      $JSONL
Your pid dir:           $PID_DIR

This briefing is complete. Do not read any files outside what's inlined below.
Form an initial judgment, then run the WAIT COMMAND at the bottom.

---

HEADER

# ── 2. Monitor's own state ───────────────────────────────────────────────
cat <<EOF
## YOUR STATE (persistent monitor memory)

### MONITOR_MIND_MAP.md
EOF
cat "$MONITOR_DIR/MONITOR_MIND_MAP.md" 2>/dev/null || echo "(empty)"
echo ""
echo "### rules.md"
cat "$MONITOR_DIR/rules.md" 2>/dev/null || echo "(empty)"
echo ""
echo "### pids/$SESSION_ID/session.md (your prior judgments on this session)"
cat "$PID_DIR/session.md" 2>/dev/null || echo "(empty — first wake for this session)"
echo ""

# Pending proposals across all PID dirs — surface them so sibling monitors
# see each other's learnings even before the human merge step runs.
# (Finding 5 from plan-review: non-graceful monitor exits leave proposals
# stranded; bootstrap surfacing makes them visible to the next monitor.)
PROPOSE_FILES=$(ls "$MONITOR_DIR"/pids/*/rules.propose.md "$MONITOR_DIR"/pids/*/mindmap.propose.md 2>/dev/null | while read f; do [ -s "$f" ] && echo "$f"; done)
if [ -n "$PROPOSE_FILES" ]; then
    PROPOSE_COUNT=$(echo "$PROPOSE_FILES" | wc -l | tr -d ' ')
    echo "### Pending proposals from sibling monitors ($PROPOSE_COUNT unmerged)"
    echo ""
    echo "> Warning: $PROPOSE_COUNT proposal(s) pending merge. Run \`.agent/monitor/merge-proposals\` to review."
    echo ""
    for f in $PROPOSE_FILES; do
        rel=$(echo "$f" | sed "s|$MONITOR_DIR/||")
        echo "#### $rel"
        cat "$f"
        echo ""
    done
fi

echo "---"
echo ""

# ── 3. Project retros ────────────────────────────────────────────────────
echo "## PROJECT RETROS (what the project has learned about itself)"
echo ""
RETRO_FILES=$(ls -t "$PROJECT_ROOT"/.agent/retro-*.md 2>/dev/null | head -2)
if [ -n "$RETRO_FILES" ]; then
    for f in $RETRO_FILES; do
        echo "### $(basename "$f")"
        cat "$f"
        echo ""
    done
else
    echo "(no retros yet)"
fi
echo "---"
echo ""

# ── 4. MIND_MAP.md ──────────────────────────────────────────────────────
echo "## MIND_MAP.md (project institutional memory)"
echo ""
if [ -f "$PROJECT_ROOT/MIND_MAP.md" ]; then
    cat "$PROJECT_ROOT/MIND_MAP.md"
else
    echo "(no MIND_MAP.md)"
fi
echo ""
echo "---"
echo ""

# ── 5. Recent closed tasks ───────────────────────────────────────────────
echo "## RECENT CLOSED TASKS (what got worked on lately)"
echo ""
# Find last 5 task.md files where Status is "done", extract Intent + Debrief
python3 - "$PROJECT_ROOT" <<'PYEOF'
import sys, re, glob, os
root = sys.argv[1]
tasks = sorted(glob.glob(os.path.join(root, '.agent/tasks/*/task.md')),
               key=os.path.getmtime, reverse=True)
shown = 0
for t in tasks:
    if shown >= 5:
        break
    try:
        text = open(t).read()
    except OSError:
        continue
    # Extract Status
    m = re.search(r'## Status\s*\n\s*(\w+)', text)
    status = m.group(1).strip() if m else 'unknown'
    if status != 'done':
        continue
    # Get task name from directory
    name = os.path.basename(os.path.dirname(t))
    print(f'### {name}  (status: {status})')
    # Intent
    im = re.search(r'## Intent\s*\n(.*?)(?=\n## |\Z)', text, re.DOTALL)
    intent = im.group(1).strip() if im else '(no intent)'
    print(f'**Intent:** {intent[:500]}')
    print()
    # Debrief (if present, short)
    dm = re.search(r'## Debrief\s*\n(.*?)(?=\n## |\Z)', text, re.DOTALL)
    if dm:
        debrief = dm.group(1).strip()
        print(f'**Debrief:** {debrief[:500]}')
        print()
    print()
    shown += 1
if shown == 0:
    print('(no closed tasks found)')
PYEOF
echo "---"
echo ""

# ── 6. Active task.md (current struggle) ─────────────────────────────────
echo "## ACTIVE TASK (the current struggle)"
echo ""
# Find the active task: most-recently-modified task.md whose Status is in_progress or pending
ACTIVE_TASK=$(python3 - "$PROJECT_ROOT" <<'PYEOF'
import sys, re, glob, os
root = sys.argv[1]
tasks = sorted(glob.glob(os.path.join(root, '.agent/tasks/*/task.md')),
               key=os.path.getmtime, reverse=True)
for t in tasks:
    try:
        text = open(t).read()
    except OSError:
        continue
    m = re.search(r'## Status\s*\n\s*(\w+)', text)
    if m and m.group(1).strip() in ('in_progress', 'pending'):
        print(t)
        break
PYEOF
)
if [ -n "$ACTIVE_TASK" ] && [ -f "$ACTIVE_TASK" ]; then
    echo "### $(basename "$(dirname "$ACTIVE_TASK")")"
    cat "$ACTIVE_TASK"
else
    echo "(no active task found — front agent may be working freehand)"
fi
echo ""
echo "---"
echo ""

# ── 7. User steering patterns ────────────────────────────────────────────
echo "## USER STEERING PATTERNS (where the user has been correcting the front agent)"
echo ""
echo "Grep of chat_log.md for correction/redirection phrases, most recent first:"
echo ""
CHAT_LOG="$PROJECT_ROOT/.agent/chat_log.md"
if [ -f "$CHAT_LOG" ]; then
    grep -in -E '^(wait|stop|no,|no ,|no .|not what i meant|i thought you|don'"'"'t|why did|that is not|why was|you didn'"'"'t listen|we often|we don'"'"'t|we never)\b' "$CHAT_LOG" | tail -15 || echo "(no steering phrases matched)"
else
    echo "(no chat_log.md found)"
fi
echo ""
echo "---"
echo ""

# ── 8. Recent events (compact extraction) ────────────────────────────────
echo "## RECENT EVENTS (last hour of JSONL — compact extraction)"
echo ""
LOOKBACK="${MONITOR_LOOKBACK_SECONDS:-3600}"
if [ -n "$JSONL" ] && [ -f "$JSONL" ]; then
    RECENT_FILE=$(mktemp)
    python3 - "$JSONL" "$LOOKBACK" > "$RECENT_FILE" <<'PYEOF'
import json, sys, datetime as dt
path, lookback = sys.argv[1], int(sys.argv[2])
cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=lookback)
with open(path) as f:
    for line in f:
        try:
            d = json.loads(line)
            ts = d.get("timestamp", "")
            if ts:
                parsed = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if parsed >= cutoff:
                    sys.stdout.write(line)
        except Exception:
            continue
PYEOF
    LINES=$(wc -l < "$RECENT_FILE" | tr -d ' ')
    echo "(filtered to last $LOOKBACK seconds: $LINES JSONL lines)"
    echo ""
    if [ "$LINES" -gt 0 ]; then
        EPHEMERAL_OFFSET=$(mktemp)
        python3 "$MONITOR_DIR/sensor.py" "$RECENT_FILE" --from-start --offset-file "$EPHEMERAL_OFFSET" 2>/dev/null || echo "(sensor error)"
        rm -f "$EPHEMERAL_OFFSET"
    else
        echo "(no events in the last $LOOKBACK seconds — agent is idle)"
    fi
    rm -f "$RECENT_FILE"

    # Set offset to current EOF ONLY on first bootstrap for this session.
    # If offset already exists, preserve it — otherwise a re-bootstrap during
    # an active monitor session would silently skip every turn that arrived
    # since the last wake. (Impl-review Finding #5.)
    if [ ! -f "$PID_DIR/.offset" ]; then
        CURRENT_SIZE=$(stat -f%z "$JSONL" 2>/dev/null || stat -c%s "$JSONL" 2>/dev/null || echo 0)
        echo "$CURRENT_SIZE" > "$PID_DIR/.offset"
    fi
else
    echo "(no JSONL found)"
fi
echo ""
echo "---"
echo ""

# ── 9. Commands ──────────────────────────────────────────────────────────
cat <<COMMANDS
## COMMANDS

### WRITE A NUDGE (one-sentence, to steer the front agent)
cat > $PID_DIR/nudge.md <<'NUDGE'
<your one-sentence nudge, referencing specific evidence>
NUDGE

### APPEND TO SESSION.MD (required each wake — your running judgment)
cat >> $PID_DIR/session.md <<'JUDGMENT'
## Wake <N> — <short title>
<brief state: what's happening, what pattern you see or don't, nudge Y/N and why>
JUDGMENT

### WAIT FOR NEXT TURN (block until agent hits stop_reason=end_turn)
python3 $MONITOR_DIR/sensor.py $JSONL --wait-once --offset-file $PID_DIR/.offset --trace-file $PID_DIR/trace.md

---

# END BOOTSTRAP

# YOUR TASK:
# 1. Read the briefing above. Form an initial judgment.
# 2. Append to session.md (use the command above, required each wake).
# 3. Write a nudge ONLY if you see specific trouble — otherwise stay silent.
# 4. Run the WAIT COMMAND. It blocks until the agent finishes a turn.
# 5. When wait returns: reason, session.md append, maybe nudge, wait again.
#
# Your nudges should reference specific evidence (quote a user phrase,
# name a message ID or span). Category names alone ("accommodation") are
# not enough. If you can't point at the specific thing, stay silent.
COMMANDS
