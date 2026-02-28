"""CLI entry point for standalone tasks management."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from tasks.core import create_task, list_tasks, task_status, PLAYBOOKS, _find_playbook_skill


def _state_file(project_path: Path) -> Path:
    """Return per-session state file if PLAYBOOK_SESSION_ID is set, else legacy."""
    session_id = os.environ.get("PLAYBOOK_SESSION_ID", "")
    agent_dir = project_path / ".agent"
    if session_id:
        return agent_dir / f"current_state.{session_id}"
    return agent_dir / "current_state"


def _load_mind_map(project_path: Path, max_chars: int = 25000) -> str | None:
    """Load MIND_MAP.md content. If over max_chars, keep head + tail, drop middle.

    Head has overview nodes [1]-[4]; tail has recent additions and roadmap.
    The middle is the most expendable, so we trim there on a line boundary.

    Set PLAYBOOK_MINDMAP_MAX env var to override max_chars (0 = suppress entirely).
    """
    env_max = os.environ.get("PLAYBOOK_MINDMAP_MAX")
    if env_max is not None:
        max_chars = int(env_max)
        if max_chars == 0:
            return None
    mind_map = project_path / "MIND_MAP.md"
    if not mind_map.exists():
        return None
    content = mind_map.read_text()
    if len(content) <= max_chars:
        return content
    # Keep 60% head, 40% tail — overview nodes are denser at the top
    head_budget = int(max_chars * 0.6)
    tail_budget = max_chars - head_budget
    # Snap to line boundaries
    head_end = content.rfind("\n", 0, head_budget)
    if head_end < 0:
        head_end = head_budget
    tail_start = content.find("\n", len(content) - tail_budget)
    if tail_start < 0:
        tail_start = len(content) - tail_budget
    head = content[:head_end]
    tail = content[tail_start:]
    omitted = content[head_end:tail_start].count("\n")
    return f"{head}\n\n[... {omitted} lines omitted ...]\n{tail}"


def find_project_root() -> Path:
    """Find project root by looking for .agent/tasks/, MIND_MAP.md, or CLAUDE.md."""
    cwd = Path.cwd()

    # Walk up from cwd — nearest match wins
    for p in [cwd, *cwd.parents]:
        if (p / ".agent" / "tasks").exists():
            return p
        if (p / "MIND_MAP.md").exists():
            return p
        if (p / "CLAUDE.md").exists():
            return p

    # Fall back to cwd (create_task will make .agent/tasks/)
    return cwd


def print_usage():
    from tasks.template import usage_text
    print(usage_text())


def main():
    # Force utf-8 on Windows where the default console encoding (cp1252) chokes on → and emoji.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help", "help"):
        print_usage()
        return

    cmd = args[0]
    cmd_args = args[1:]

    if cmd == "work":
        if not cmd_args:
            print("Error: 'work' requires a task number or 'done'", file=sys.stderr)
            print("Usage: tasks work <number> | tasks work done", file=sys.stderr)
            sys.exit(1)

        task_num = cmd_args[0]
        if task_num != "done" and task_num.isdigit():
            task_num = task_num.zfill(3)
        project_path = find_project_root()

        # Handle 'tasks work done' - deactivate current task and set Status in task.md
        if task_num == "done":
            agent_dir = project_path / ".agent"
            legacy_state = agent_dir / "current_state"
            session_id = os.environ.get("PLAYBOOK_SESSION_ID", "")
            session_state = agent_dir / f"current_state.{session_id}" if session_id else None

            # Find the active task from whichever state file exists
            prev_task = None
            for sf in [session_state, legacy_state]:
                if sf and sf.exists():
                    prev_task = sf.read_text().strip()
                    break

            if prev_task:
                # Set ## Status to done in task.md
                tasks_dir = project_path / ".agent" / "tasks"
                matches = list(tasks_dir.glob(f"{prev_task}-*/task.md"))
                if matches:
                    task_file = matches[0]
                    lines = task_file.read_text().splitlines(keepends=True)
                    for i, line in enumerate(lines):
                        if line.strip() == "## Status" and i + 1 < len(lines):
                            lines[i + 1] = "done\n"
                            task_file.write_text("".join(lines))
                            break
                # Remove all state files that reference this task (legacy + all per-session).
                # PLAYBOOK_SESSION_ID is not set when called from Bash tool (only in hook
                # context), so we can't rely on session_state alone — scan everything.
                for sf in [legacy_state] + list(agent_dir.glob("current_state.*")):
                    try:
                        if sf.exists() and sf.read_text().strip() == prev_task:
                            sf.unlink()
                    except OSError:
                        pass
                print(f"Task {prev_task} done.")
            else:
                print("No active task.")
            print("Code edits blocked until: tasks work <N>")
            return

        # Verify task exists
        from tasks.core import _find_active_task
        task_file = _find_active_task(project_path, task_num)
        if not task_file:
            tasks_dir = project_path / ".agent" / "tasks"
            matches = list(tasks_dir.glob(f"{task_num}-*/task.md"))
            if matches:
                from tasks.core import _is_done
                tf = matches[0]
                done = _is_done(tf)
                if done:
                    # Reopen: reset Status to in_progress so activation can proceed.
                    lines = tf.read_text().splitlines(keepends=True)
                    for i, line in enumerate(lines):
                        if line.strip() == "## Status" and i + 1 < len(lines):
                            lines[i + 1] = "in_progress\n"
                            tf.write_text("".join(lines))
                            break
                    print(f"Note: task {task_num} was marked done — reopening.")
                    task_file = tf
                    # Fall through to activation below
                else:
                    print(f"Task {task_num} has no open gates.", file=sys.stderr)
                    sys.exit(1)
            else:
                print(f"Task {task_num} not found", file=sys.stderr)
                sys.exit(1)

        # Write task number to current_state
        # Always write plain current_state (hooks fallback), plus per-session if set
        agent_dir = project_path / ".agent"
        agent_dir.mkdir(parents=True, exist_ok=True)
        legacy_state = agent_dir / "current_state"
        legacy_state.write_text(f"{task_num}\n")
        session_id = os.environ.get("PLAYBOOK_SESSION_ID", "")
        if session_id:
            (agent_dir / f"current_state.{session_id}").write_text(f"{task_num}\n")

        # Clean up stale per-session state files older than 24h
        agent_dir = project_path / ".agent"
        cutoff = time.time() - 86400
        for f in agent_dir.glob("current_state.*"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass

        # Workflow rules — deferred from bootstrap to task activation
        from tasks.template import workflow_briefing
        print("=== WORKFLOW ===")
        print(workflow_briefing())
        print()

        # Print the full task file
        print(task_file.read_text().rstrip())


    elif cmd == "new":
        if len(cmd_args) < 2:
            print("Error: 'new' requires a type and a name", file=sys.stderr)
            print("Usage: tasks new <type> <name>", file=sys.stderr)
            print(f"Types: {', '.join(sorted(PLAYBOOKS.keys()))}", file=sys.stderr)
            sys.exit(1)

        task_type = cmd_args[0]
        if task_type not in PLAYBOOKS:
            print(f"Error: unknown type '{task_type}'", file=sys.stderr)
            print(f"Types: {', '.join(sorted(PLAYBOOKS.keys()))}", file=sys.stderr)
            sys.exit(1)

        task_name = " ".join(cmd_args[1:])
        project_path = find_project_root()

        # Check if user included a task number prefix
        import re as _re
        from tasks.core import _next_task_number
        num_match = _re.match(r'^(\d{3})-(.+)$', task_name)
        if num_match:
            provided_num = int(num_match.group(1))
            tasks_dir = project_path / ".agent" / "tasks"
            next_num = _next_task_number(tasks_dir)
            if provided_num == next_num:
                # Matches next number - strip it (user was explicit)
                task_name = num_match.group(2)
            else:
                print(f"Error: provided task number {provided_num:03d} doesn't match next number {next_num:03d}", file=sys.stderr)
                print(f"Usage: tasks new {task_type} {num_match.group(2)}", file=sys.stderr)
                sys.exit(1)
        task_file = create_task(project_path, task_name, task_type=task_type)
        pattern_name = PLAYBOOKS[task_type]

        import re
        task_num_match = re.match(r'^(\d+)-', task_file.parent.name)
        task_num = task_num_match.group(1) if task_num_match else "?"

        print(f"Created: {task_file.relative_to(project_path)}")
        print(f"Pattern: {pattern_name}")
        print(f"Next: fill in task.md gates, then ask user to run: tasks work {task_num}")
        print()

        # Print full playbook so agent has workflow guidance inline
        playbook_path = _find_playbook_skill(project_path)
        if playbook_path:
            playbook_file = Path(playbook_path)
            if playbook_file.exists():
                print("=== PLAYBOOK (task.md design guide) ===")
                print("Use this to improve your task.md: select patterns and gates as appropriate,")
                print("or invent new ones. This is a starting point — expand as needed.")
                print()
                content = playbook_file.read_text()
                # Strip sections not relevant to task design
                for marker in ["## Mind Map", "> Evidence base:"]:
                    idx = content.find(marker)
                    if idx > 0:
                        content = content[:idx]
                print(content.rstrip())
                print()
                print(f"Now fill in {task_file.relative_to(project_path)} — design a good task.md.")

    elif cmd == "init":
        # Target directory: argument or cwd
        target = Path(cmd_args[0]).resolve() if cmd_args else Path.cwd()
        if not target.exists():
            print(f"Error: directory not found: {target}", file=sys.stderr)
            sys.exit(1)

        title = target.name.replace("-", " ").replace("_", " ").title()
        print(f"Initializing project: {target.name}")

        # Create .agent/tasks/
        tasks_dir = target / ".agent" / "tasks"
        existed = tasks_dir.exists()
        tasks_dir.mkdir(parents=True, exist_ok=True)
        print(f"  .agent/tasks/  {'exists' if existed else 'created'}")

        # Create MIND_MAP.md
        mind_map = target / "MIND_MAP.md"
        if not mind_map.exists():
            mind_map.write_text(f"""# {title}

## Architecture

(describe your project architecture here)
""")
            print("  MIND_MAP.md    created")
        else:
            print("  MIND_MAP.md    exists")

        # Create CLAUDE.md
        claude_md = target / "CLAUDE.md"
        if not claude_md.exists():
            from tasks.template import claude_md as claude_md_template
            claude_md.write_text(claude_md_template(title))
            print("  CLAUDE.md      created")
        else:
            print("  CLAUDE.md      exists")

        # Check for duplicate hook registrations
        settings_file = target / ".claude" / "settings.json"
        if settings_file.exists():
            import json
            try:
                settings = json.loads(settings_file.read_text())
                if "hooks" in settings:
                    hook_events = list(settings["hooks"].keys())
                    print(f"  ⚠ .claude/settings.json has local hook registrations: {', '.join(hook_events)}")
                    print(f"    These may duplicate plugin hooks (hooks/hooks.json) — causing double writes.")
                    print(f"    Fix: remove the 'hooks' key from .claude/settings.json")
            except (json.JSONDecodeError, KeyError):
                pass

        # Check for stale .claude/hooks/ directory
        local_hooks = target / ".claude" / "hooks"
        if local_hooks.is_dir():
            hook_files = [f.name for f in local_hooks.iterdir() if f.is_file()]
            if hook_files:
                print(f"  ⚠ .claude/hooks/ contains {len(hook_files)} hook scripts: {', '.join(hook_files)}")
                print(f"    These are stale copies — canonical hooks live in scripts/ (resolved via plugin).")
                print(f"    Fix: remove .claude/hooks/ directory")

    elif cmd == "bootstrap":
        project_path = find_project_root()

        # Identity preamble
        from tasks.template import identity_preamble, mind_map_header
        print(identity_preamble())
        print()

        # Mind Map — full dump with navigation header
        mm_content = _load_mind_map(project_path)
        if mm_content:
            print("=== MIND MAP (MIND_MAP.md) ===")
            print(mind_map_header())
            print()
            print(mm_content.rstrip())
            print()

        # Pending tasks
        print("=== PENDING TASKS ===")
        list_tasks(project_path, pending_only=True)

    elif cmd in ("list", "ls"):
        project_path = find_project_root()
        pending_only = "--pending" in cmd_args
        list_tasks(project_path, pending_only=pending_only)

    elif cmd == "judge":
        if not cmd_args:
            print("Error: 'judge' requires a task number", file=sys.stderr)
            print("Usage: tasks judge <number> [--backend claude|codex]", file=sys.stderr)
            sys.exit(1)

        import shutil
        import subprocess

        # Parse --backend flag (default: claude)
        backend = "claude"
        remaining_args = []
        i = 0
        while i < len(cmd_args):
            if cmd_args[i] == "--backend" and i + 1 < len(cmd_args):
                backend = cmd_args[i + 1]
                i += 2
            else:
                remaining_args.append(cmd_args[i])
                i += 1

        if backend not in ("claude", "codex"):
            print(f"Error: unknown backend '{backend}'", file=sys.stderr)
            print("Supported: claude (default), codex", file=sys.stderr)
            sys.exit(1)

        if not remaining_args:
            print("Error: 'judge' requires a task number", file=sys.stderr)
            sys.exit(1)

        task_num = remaining_args[0]
        if task_num.isdigit():
            task_num = task_num.zfill(3)
        project_path = find_project_root()
        tasks_dir = project_path / ".agent" / "tasks"
        matches = list(tasks_dir.glob(f"{task_num}-*/task.md"))
        if not matches:
            print(f"Task {task_num} not found", file=sys.stderr)
            sys.exit(1)

        task_file = matches[0]
        task_path = str(task_file.relative_to(project_path))

        from tasks.template import judge_prompt

        # Build context: mind map + task content (bounded to avoid argv/context limits)
        MAX_CONTEXT_CHARS = 100_000
        context_parts = []
        mm_content = _load_mind_map(project_path)
        if mm_content:
            context_parts.append(f"=== MIND_MAP.md ===\n{mm_content}")
        task_content = task_file.read_text()
        if len(task_content) > MAX_CONTEXT_CHARS // 2:
            task_content = task_content[:MAX_CONTEXT_CHARS // 2] + "\n\n[... truncated for context budget ...]"
        context_parts.append(f"=== {task_path} ===\n{task_content}")
        system_context = "\n\n".join(context_parts)
        if len(system_context) > MAX_CONTEXT_CHARS:
            system_context = system_context[:MAX_CONTEXT_CHARS] + "\n\n[... truncated for context budget ...]"

        from tasks.core import _extract_status
        judge_mode = "impl" if _extract_status(task_file).startswith("done") else "plan"

        if backend == "claude":
            claude_bin = shutil.which("claude")
            if not claude_bin:
                print("Error: 'claude' not found on PATH", file=sys.stderr)
                sys.exit(1)

            prompt = judge_prompt(task_path, mode=judge_mode)
            env = os.environ.copy()
            env["CLAUDECODE"] = ""
            env.pop("CLAUDE_CODE_SSE_PORT", None)
            env.pop("CLAUDE_CODE_ENTRYPOINT", None)
            env["PLAYBOOK_SESSION_ID"] = "judge"

            claude_args = [
                claude_bin, "-p",
                "--dangerously-skip-permissions",
                "--max-budget-usd", "2",
                "--append-system-prompt", system_context,
                prompt,
            ]

            # Wrap in seatbelt sandbox on macOS (write containment)
            import platform
            if platform.system() == "Darwin" and shutil.which("sandbox-exec"):
                git_dir = subprocess.run(
                    ["git", "rev-parse", "--git-dir"],
                    cwd=str(project_path), capture_output=True, text=True,
                ).stdout.strip()
                if git_dir:
                    git_dir = str(Path(git_dir).resolve())
                    proj_dir = str(project_path.resolve())
                    home_dir = str(Path.home())
                    profile = (
                        '(version 1)\n(allow default)\n'
                        '(deny file-write*\n'
                        '    (require-all\n'
                        f'        (require-not (subpath "{proj_dir}"))\n'
                        '        (require-not (subpath "/tmp"))\n'
                        '        (require-not (subpath "/private/tmp"))\n'
                        '        (require-not (subpath "/var/folders"))\n'
                        '        (require-not (subpath "/private/var/folders"))\n'
                        f'        (require-not (regex #"^{home_dir}/\\.claude"))\n'
                        f'        (require-not (subpath "{home_dir}/.cache"))\n'
                        f'        (require-not (subpath "{home_dir}/.local"))\n'
                        f'        (require-not (subpath "{home_dir}/Library"))\n'
                        '        (require-not (subpath "/dev"))\n'
                        '    )\n'
                        ')\n'
                        f'(deny file-write* (subpath "{git_dir}"))'
                    )
                    cmd_list = ["sandbox-exec", "-p", profile] + claude_args
                else:
                    cmd_list = claude_args
            else:
                cmd_list = claude_args

            print(f"Running blind judge (claude) on {task_path}...", flush=True)
            result = subprocess.run(
                cmd_list,
                cwd=str(project_path),
                env=env,
                capture_output=True,
                text=True,
            )

        else:  # codex
            codex_bin = shutil.which("codex")
            if not codex_bin:
                print("Error: 'codex' not found on PATH", file=sys.stderr)
                print("Install: https://github.com/openai/codex", file=sys.stderr)
                sys.exit(1)

            prompt = judge_prompt(task_path, inline_context=True, mode=judge_mode)
            # Codex has no system prompt — inline context into the user prompt
            full_prompt = f"{system_context}\n\n---\n\n{prompt}"

            codex_log = task_file.parent / "judge-codex.log"
            cmd_list = [
                codex_bin, "exec",
                "-s", "workspace-write",
                "--ephemeral",
                "-C", str(project_path),
                "-o", str(codex_log),
                "-",  # read prompt from stdin
            ]

            print(f"Running blind judge (codex) on {task_path}...", flush=True)
            result = subprocess.run(
                cmd_list,
                cwd=str(project_path),
                input=full_prompt,
                capture_output=True,
                text=True,
            )

        if result.stdout:
            print(result.stdout, end="", flush=True)
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr, flush=True)

        # Save judge output — backend-specific log files
        log_name = "judge.log" if backend == "claude" else "judge-codex.log"
        judge_log = task_file.parent / log_name
        output = (result.stdout or "").strip()
        if result.returncode != 0 and not output:
            if judge_log.exists():
                print(f"\nJudge failed (exit {result.returncode}); kept previous {judge_log.relative_to(project_path)}", flush=True)
            else:
                print(f"\nJudge failed (exit {result.returncode}); no output to save", flush=True)
        else:
            if backend == "claude":
                judge_log.write_text(result.stdout or "")
            # codex: -o already writes the file; write stdout as fallback
            elif not judge_log.exists() or not judge_log.read_text().strip():
                judge_log.write_text(result.stdout or "")
            print(f"\nSaved: {judge_log.relative_to(project_path)}", flush=True)

        sys.exit(result.returncode)

    elif cmd == "context":
        if not cmd_args:
            print("Error: 'context' requires a task number", file=sys.stderr)
            print("Usage: tasks context <number>", file=sys.stderr)
            sys.exit(1)

        task_num = cmd_args[0]
        if task_num.isdigit():
            task_num = task_num.zfill(3)
        project_path = find_project_root()

        chat_log = project_path / ".agent" / "chat_log.md"
        if not chat_log.exists():
            print("No .agent/chat_log.md found.", file=sys.stderr)
            sys.exit(1)

        import re
        open_tag = re.compile(r'^<!--\s*T' + re.escape(task_num) + r'\s*-->$')
        close_tag = re.compile(r'^<!--\s*/T' + re.escape(task_num) + r'\s*-->$')

        spans = []
        current_span = []
        inside = False
        for line in chat_log.read_text().splitlines():
            stripped = line.strip()
            if not inside and open_tag.match(stripped):
                inside = True
                continue
            elif inside and close_tag.match(stripped):
                spans.append("\n".join(current_span))
                current_span = []
                inside = False
                continue
            if inside:
                current_span.append(line)

        # Handle unclosed span at end of file
        if inside and current_span:
            spans.append("\n".join(current_span))

        if not spans:
            print(f"No attributed messages for task {task_num}.", file=sys.stderr)
            sys.exit(1)

        # Token-efficient output: strip markdown boilerplate, one line per message
        import re as _re
        max_line = 200
        msg_header = _re.compile(r'^\*\*\[(M\d+)\]\*\*.*')
        gate_header = _re.compile(r'^\*\*\[G\d+:\d+\]\*\*.*')
        for span in spans:
            msg_id = None
            msg_lines = []
            in_gate = False
            for line in span.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped == "---":
                    in_gate = False
                    continue
                if gate_header.match(stripped):
                    in_gate = True
                    continue
                if in_gate:
                    continue
                m = msg_header.match(stripped)
                if m:
                    # Flush previous message
                    if msg_id and msg_lines:
                        text = " ".join(msg_lines)
                        if len(text) > max_line:
                            text = text[:max_line] + "..."
                        print(f"[{msg_id}] {text}")
                    msg_id = m.group(1)
                    msg_lines = []
                else:
                    msg_lines.append(stripped)
            # Flush last message
            if msg_id and msg_lines:
                text = " ".join(msg_lines)
                if len(text) > max_line:
                    text = text[:max_line] + "..."
                print(f"[{msg_id}] {text}")

    elif cmd == "timeline":
        project_path = find_project_root()
        bash_history = project_path / ".agent" / "bash_history"
        if not bash_history.exists():
            print("No .agent/bash_history found.", file=sys.stderr)
            sys.exit(1)

        import re
        # Match: timestamp | AGENT/SCRIPT | tasks work/new/done ...
        pattern = re.compile(
            r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| \w+ \| '
            r'(?:.*/)?(tasks (?:work|new) .+)$'
        )
        seen = set()
        for line in bash_history.read_text().splitlines():
            m = pattern.match(line)
            if m:
                cmd = m.group(2)
                # Deduplicate AGENT+SCRIPT echoes (same command within 2 lines)
                if cmd not in seen:
                    seen.add(cmd)
                    print(f"{m.group(1)}  {cmd}")
                else:
                    seen.discard(cmd)

    elif cmd == "tagger":
        project_path = find_project_root()
        chat_log = project_path / ".agent" / "chat_log.md"
        bash_history = project_path / ".agent" / "bash_history"
        if not chat_log.exists():
            print("No .agent/chat_log.md found.", file=sys.stderr)
            sys.exit(1)
        if not bash_history.exists():
            print("No .agent/bash_history found.", file=sys.stderr)
            sys.exit(1)

        import re

        # 1. Parse messages from chat_log.md: (timestamp, msg_id, text)
        msg_header = re.compile(
            r'^\*\*\[(M\d+)\]\*\* \[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC\]'
        )
        gate_header = re.compile(r'^\*\*\[G\d+:\d+\]\*\*')
        entries = []  # (timestamp_str, sort_key, display_line)
        max_line = 200

        msg_id = None
        msg_ts = None
        msg_lines = []
        in_gate = False

        def flush_msg():
            if msg_id and msg_lines:
                text = " ".join(msg_lines)
                if len(text) > max_line:
                    text = text[:max_line] + "..."
                entries.append((msg_ts, 0, f"[{msg_id}] {text}"))

        for line in chat_log.read_text().splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped == "---":
                in_gate = False
                continue
            if gate_header.match(stripped):
                in_gate = True
                continue
            if in_gate:
                continue
            m = msg_header.match(stripped)
            if m:
                flush_msg()
                msg_id = m.group(1)
                msg_ts = m.group(2)
                msg_lines = []
            elif stripped.startswith("<!--"):
                continue  # skip attribution tags / comments
            else:
                msg_lines.append(stripped)

        flush_msg()

        # 2. Parse task transitions from bash_history
        task_pattern = re.compile(
            r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| \w+ \| '
            r'(?:.*/)?(tasks (?:work|new) .+)$'
        )
        seen = set()
        for line in bash_history.read_text().splitlines():
            m = task_pattern.match(line)
            if m:
                task_cmd = m.group(2)
                if task_cmd not in seen:
                    seen.add(task_cmd)
                    entries.append((m.group(1), 1, f"--- {task_cmd} ---"))
                else:
                    seen.discard(task_cmd)

        # 3. Sort by timestamp, then task transitions before messages (sort_key: 1 before 0)
        #    Actually: task transitions AFTER messages at same timestamp makes more sense
        #    But transitions should come BEFORE subsequent messages — sort_key 1 means
        #    transitions sort after messages at same second. That's fine: the transition
        #    happened between messages.
        entries.sort(key=lambda e: (e[0], e[1]))

        # 4. Output
        for _, _, display in entries:
            print(display)

    elif cmd == "tag":
        dry_run = "--dry-run" in cmd_args
        project_path = find_project_root()
        chat_log = project_path / ".agent" / "chat_log.md"
        bash_history = project_path / ".agent" / "bash_history"
        if not chat_log.exists():
            print("No .agent/chat_log.md found.", file=sys.stderr)
            sys.exit(1)
        if not bash_history.exists():
            print("No .agent/bash_history found.", file=sys.stderr)
            sys.exit(1)

        import re
        from bisect import bisect_right

        # 1. Build sorted task transition list from bash_history
        #    Each entry: (timestamp, active_task_or_None)
        task_pattern = re.compile(
            r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| \w+ \| '
            r'(?:.*/)?(tasks (?:work|new) .+)$'
        )
        work_re = re.compile(r'tasks work (\d+)')
        transitions = []  # [(timestamp, task_num_or_None)]
        seen = set()
        for line in bash_history.read_text().splitlines():
            m = task_pattern.match(line)
            if m:
                task_cmd = m.group(2)
                if task_cmd not in seen:
                    seen.add(task_cmd)
                else:
                    seen.discard(task_cmd)
                    continue
                ts = m.group(1)
                if "work done" in task_cmd:
                    transitions.append((ts, None))
                else:
                    wm = work_re.search(task_cmd)
                    if wm:
                        transitions.append((ts, wm.group(1).zfill(3)))
        transitions.sort(key=lambda t: t[0])
        trans_times = [t[0] for t in transitions]

        def active_task_at(ts):
            """Return task number active at timestamp ts, or None."""
            idx = bisect_right(trans_times, ts) - 1
            if idx < 0:
                return None
            return transitions[idx][1]

        # 2. Scan chat_log.md, find message headers with timestamps,
        #    insert tags at task transition points
        msg_header = re.compile(
            r'^(\*\*\[(M\d+)\]\*\* \[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC\])'
        )
        # Also detect existing tags to avoid double-tagging
        existing_tag = re.compile(r'^<!--\s*/?T\d+\s*-->$')

        lines = chat_log.read_text().splitlines(keepends=True)
        output = []
        current_tag = None  # currently open tag (task number)
        tags_inserted = 0

        for line in lines:
            stripped = line.strip()
            # Skip existing attribution tags (we'll rewrite them)
            if existing_tag.match(stripped):
                continue

            m = msg_header.match(stripped)
            if m:
                msg_id = m.group(2)
                msg_ts = m.group(3)
                task = active_task_at(msg_ts)

                if task != current_tag:
                    # Close previous tag if open
                    if current_tag is not None:
                        output.append(f"<!-- /T{current_tag} -->\n")
                        output.append("\n")
                        tags_inserted += 1
                    # Open new tag if task is active
                    if task is not None:
                        output.append(f"<!-- T{task} -->\n")
                        output.append("\n")
                        tags_inserted += 1
                    current_tag = task

            output.append(line)

        # Close final tag if still open
        if current_tag is not None:
            output.append(f"\n<!-- /T{current_tag} -->\n")
            tags_inserted += 1

        if dry_run:
            print(f"Would insert {tags_inserted} tags into chat_log.md")
            # Show first few transitions
            current_tag = None
            for line in output:
                stripped = line.strip()
                if existing_tag.match(stripped):
                    print(f"  {stripped}")
        else:
            chat_log.write_text("".join(output))
            print(f"Inserted {tags_inserted} tags into chat_log.md")

    elif cmd == "status":
        project_path = find_project_root()
        task_status(project_path)

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
