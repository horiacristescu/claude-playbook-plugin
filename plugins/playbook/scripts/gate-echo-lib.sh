#!/bin/bash
# gate-echo-lib.sh
# Shared logic for hooks: project root detection + gate parsing.

# find_project_root
# Walk up from $PWD looking for .agent/tasks/ (the definitive playbook marker).
# CLAUDE.md and MIND_MAP.md alone are NOT sufficient — they exist in non-playbook
# projects and would cause hooks to fire where they shouldn't.
# Outputs the project root path, or empty string if not found.
find_project_root() {
    local dir="$PWD"
    while true; do
        if [ -d "$dir/.agent/tasks" ]; then
            echo "$dir"
            return 0
        fi
        local parent=$(dirname "$dir")
        if [ "$parent" = "$dir" ]; then
            break
        fi
        dir="$parent"
    done
    echo ""
    return 0  # "not found" communicated via empty output, not exit code (set -e safe)
}

# find_agent_root_pid
# Walk parent process tree. Output PID of the highest ancestor whose
# `comm` is claude/codex/gemini, or empty if none found within 20 hops.
# Mirrors `find_agent_root_pid()` in src/tasks/core.py — both walk the
# same `ps` tree and converge on the same PID. Used as fallback when
# PLAYBOOK_SESSION_ID env var isn't propagated.
find_agent_root_pid() {
    local pid=$PPID
    local last_agent=""
    local count=0
    local info ppid comm
    while [ -n "$pid" ] && [ "$pid" != "0" ] && [ "$pid" != "1" ] && [ "$count" -lt 20 ]; do
        info=$(ps -p "$pid" -o ppid=,comm= 2>/dev/null) || break
        [ -z "$info" ] && break
        ppid=$(echo "$info" | awk '{print $1}')
        comm=$(echo "$info" | awk '{$1=""; sub(/^ +/, ""); print}')
        comm="${comm##*/}"  # parameter expansion: strip path; safe for "-zsh" (basename would error)
        case "$comm" in
            claude|codex|gemini) last_agent=$pid ;;
        esac
        [ "$ppid" = "$pid" ] && break
        pid=$ppid
        count=$((count + 1))
    done
    echo "$last_agent"
}

# resolve_session_id
# Returns the session_id used to namespace .agent/sessions/<id>/.
# Order: PLAYBOOK_SESSION_ID env → ancestor scan (root agent PID) →
# immediate-parent PID. Mirrors resolve_session_id() in src/tasks/core.py
# — Python and bash converge on the same value when env var is unset.
resolve_session_id() {
    if [ -n "${PLAYBOOK_SESSION_ID:-}" ]; then
        echo "$PLAYBOOK_SESSION_ID"
        return 0
    fi
    local agent_pid
    agent_pid=$(find_agent_root_pid)
    if [ -n "$agent_pid" ]; then
        echo "pid-$agent_pid"
    else
        echo "pid-$PPID"
    fi
}

# agent_dir_writable PROJECT_DIR
# Returns 0 if .agent/ exists and is writable, 1 otherwise.
# Use this before any hook that writes to .agent/ — in sandbox mode
# the directory may exist but be read-only.
agent_dir_writable() {
    local agent_dir="$1/.agent"
    [ -d "$agent_dir" ] && [ -w "$agent_dir" ]
}

# get_gate_info TASK_FILE
# Outputs: done_count total_count gate_line gate_text
# If all done: gate_line and gate_text are empty
get_gate_info() {
    local task_file="$1"

    if [ ! -f "$task_file" ]; then
        echo "0 0 0 ''"
        return 1
    fi

    # Count total and done checkboxes (only at line start, not in backticks)
    # Pattern: only match [ ], [x], [X] — not [8] or [40] (reference links)
    local total
    total=$(grep -cE '^[[:space:]]*- \[( |x|X)\]' "$task_file" 2>/dev/null) || total=0
    local done
    done=$(grep -cE '^[[:space:]]*- \[[xX]\]' "$task_file" 2>/dev/null) || done=0

    # Find first unchecked gate
    local gate_line=""
    local gate_text=""

    while IFS= read -r line; do
        local lineno="${line%%:*}"
        local content="${line#*:}"
        if echo "$content" | grep -qE '^[[:space:]]*- \[ \]'; then
            gate_line="$lineno"
            gate_text=$(echo "$content" | sed 's/^[[:space:]]*- \[ \] *//')
            break
        fi
    done < <(grep -nE '^[[:space:]]*- \[ \]' "$task_file" 2>/dev/null)

    echo "$done $total $gate_line $gate_text"
}

# read_counter FILE KEY
# Read a key=value from the counter file. Outputs the value, or empty if missing.
read_counter() {
    local file="$1" key="$2"
    if [ -f "$file" ]; then
        sed -n "s/^${key}=//p" "$file" 2>/dev/null | head -1
    fi
}

# write_counter FILE KEY VALUE
# Set a key=value in the counter file. Creates file if missing, updates in-place if key exists.
# Uses grep-filter-append instead of sed to avoid delimiter collisions with gate text
# containing |, backticks, or other special characters.
write_counter() {
    local file="$1" key="$2" value="$3"
    local tmp="${file}.tmp.$$"
    if [ -f "$file" ]; then
        grep -v "^${key}=" "$file" > "$tmp" 2>/dev/null || true
    fi
    printf '%s=%s\n' "$key" "$value" >> "$tmp"
    mv "$tmp" "$file"
}

# reset_counters FILE
# Reset tools=0 and writes=0, preserving gate_* fields. Creates file if missing.
reset_counters() {
    local file="$1"
    if [ -f "$file" ]; then
        # Preserve gate_* lines, reset tools/writes
        local gate_lines
        gate_lines=$(grep '^gate_' "$file" 2>/dev/null || true)
        printf 'tools=0\nwrites=0\n' > "$file"
        if [ -n "$gate_lines" ]; then
            echo "$gate_lines" >> "$file"
        fi
    else
        printf 'tools=0\nwrites=0\n' > "$file"
    fi
}

# format_context TASK_NUM DONE TOTAL GATE_TEXT GATE_LINE REL_PATH
# Outputs the formatted context string for the hook
format_context() {
    local task_num="$1"
    local done="$2"
    local total="$3"
    local gate_text="$4"
    local gate_line="$5"
    local rel_path="$6"

    if [ -z "$gate_line" ]; then
        echo "# [${task_num}] — all gates done. Stay for follow-up. Auto-closes on task switch."
    else
        echo "# Working on task [${task_num}] gate (${done}/${total}) -> [ ] ${gate_text}
# Done? Check the box: ${rel_path}:${gate_line}"
    fi
}

# write_log_append INPUT_JSON PROJECT_DIR
# Appends the written file's content to the persistent write log.
# Called from PostToolUse for Write/Edit tools. Extracts file_path from
# the tool input JSON, reads the file, appends with timestamp.
# Log lives at ~/.local/share/playbook/<project-slug>/write_log
# — outside the project tree so agent can't accidentally delete it.
write_log_append() {
    local input="$1" project_dir="$2"
    local file_path
    file_path=$(echo "$input" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('file_path',''))" 2>/dev/null || echo "")
    if [ -z "$file_path" ] || [ ! -f "$file_path" ]; then
        return 0
    fi
    # Project slug: absolute path with / replaced by -
    local slug
    slug=$(echo "$project_dir" | sed 's|^/||; s|/|-|g')
    local log_dir="$HOME/.local/share/playbook/$slug"
    mkdir -p "$log_dir" 2>/dev/null || return 0
    local log_file="$log_dir/write_log"
    local ts
    ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    local size
    size=$(wc -c < "$file_path" 2>/dev/null | tr -d ' ')
    {
        printf '=== %s %s (%s bytes) ===\n' "$ts" "$file_path" "$size"
        cat "$file_path"
        printf '\n'
    } >> "$log_file" 2>/dev/null || true
}

# create_wrapper PROJECT_DIR WRAPPER_NAME
# Creates .claude/bin/<WRAPPER_NAME> as a find-based wrapper that locates
# the plugin's scripts/<WRAPPER_NAME> and execs into it.
# - Skips if file exists without "# playbook-managed" marker (custom wrapper)
# - Overwrites if file has the marker (stale playbook wrapper)
# - Creates .claude/bin/ directory if needed
create_wrapper() {
    local project_dir="$1"
    local wrapper_name="$2"
    local wrapper_path="$project_dir/.claude/bin/$wrapper_name"

    # Skip custom wrappers (no playbook-managed marker)
    # Empty files are NOT custom — overwrite them (self-healing)
    if [ -f "$wrapper_path" ] && [ -s "$wrapper_path" ]; then
        if ! grep -q '# playbook-managed' "$wrapper_path" 2>/dev/null; then
            return 0
        fi
    fi

    mkdir -p "$project_dir/.claude/bin"

    cat > "$wrapper_path" <<'WRAPPER'
#!/bin/bash
# playbook-managed — do not edit; regenerated by playbook plugin
SCRIPT="$(find ~/.claude/plugins -path "*/playbook/scripts/WRAPPER_NAME" -type f 2>/dev/null | head -1)"
if [ -z "$SCRIPT" ]; then
    echo "Error: playbook plugin not found." >&2
    echo "Install: claude plugin marketplace add horiacristescu/claude-playbook-plugin" >&2
    exit 1
fi
exec "$SCRIPT" "$@"
WRAPPER

    # Replace placeholder with actual wrapper name
    sed -i.bak "s|WRAPPER_NAME|$wrapper_name|g" "$wrapper_path" && rm -f "$wrapper_path.bak"
    chmod +x "$wrapper_path"
}
