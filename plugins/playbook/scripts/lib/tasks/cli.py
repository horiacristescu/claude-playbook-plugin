"""CLI entry point for standalone tasks management."""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from tasks.core import create_task, list_tasks, task_status, PLAYBOOKS, _find_playbook_skill


def _state_file(project_path: Path) -> Path:
    """Return per-session state file under .agent/sessions/<id>/current_state."""
    session_id = os.environ.get("PLAYBOOK_SESSION_ID", "") or "default"
    state_dir = project_path / ".agent" / "sessions" / session_id
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "current_state"


def _capture_recent_chat(project_path: Path, max_messages: int = 10,
                         max_gap_seconds: int = 10800) -> list[str]:
    """Capture recent chat_log messages for task attribution.

    Scans backwards from end of chat_log.md. Stops at:
    - Previous 'tasks done' or 'tasks work done' in message text
    - A time gap > max_gap_seconds (default 3h) between consecutive messages
    - max_messages reached (default 10)

    Returns list of message blocks (most recent last), each as:
    "**[MNNN]** [timestamp]\\n<text truncated to 200 chars>"
    """
    import re
    from datetime import datetime

    chat_log = project_path / ".agent" / "chat_log.md"
    if not chat_log.exists():
        return []

    content = chat_log.read_text(encoding="utf-8")
    # Split into message blocks on --- separator
    msg_pattern = re.compile(
        r'\*\*\[(M\d+)\]\*\*\s+\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC\]\s+`\w+`\s*\n\s*\n(.*?)(?=\n---|\Z)',
        re.DOTALL
    )

    messages = []
    for m in msg_pattern.finditer(content):
        msg_id = m.group(1)
        timestamp_str = m.group(2)
        text = m.group(3).strip()
        try:
            ts = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        messages.append((msg_id, ts, timestamp_str, text))

    if not messages:
        return []

    # Scan backwards
    captured = []
    prev_ts = None
    for msg_id, ts, ts_str, text in reversed(messages):
        # Stop at time gap
        if prev_ts is not None:
            gap = (prev_ts - ts).total_seconds()
            if gap > max_gap_seconds:
                break
        prev_ts = ts

        # Stop at task-done marker
        text_lower = text.lower()
        if "tasks done" in text_lower or "tasks work done" in text_lower:
            break

        # Truncate long messages
        display_text = text[:200] + "..." if len(text) > 200 else text
        captured.append(f"**[{msg_id}]** [{ts_str}]\n{display_text}")

        if len(captured) >= max_messages:
            break

    # Reverse to chronological order
    captured.reverse()
    return captured


def _inject_chat_into_task(task_file: Path, messages: list[str]) -> None:
    """Inject captured chat messages into task.md References section."""
    if not messages:
        return

    import re

    def _utf8_safe(text: str) -> str:
        """Replace non-UTF-8-survivable code points like lone surrogates."""
        return text.encode("utf-8", errors="replace").decode("utf-8")

    content = task_file.read_text(encoding="utf-8")

    chat_block = "\n### Recent Chat (auto-captured at activation — review and remove unrelated)\n"
    for msg in messages:
        chat_block += f"\n{_utf8_safe(msg)}\n"

    # Insert after the first --- (end of References section, before Design Phase)
    first_sep = content.find("\n---\n")
    if first_sep >= 0:
        references = content[:first_sep]
        references = re.sub(
            r'\n### Recent Chat \(auto-captured at activation — review and remove unrelated\)\n.*\Z',
            "",
            references,
            flags=re.DOTALL,
        )
        content = references.rstrip() + "\n" + chat_block + content[first_sep:]
        task_file.write_text(_utf8_safe(content), encoding="utf-8")


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
    content = mind_map.read_text(encoding="utf-8")
    if len(content) <= max_chars:
        return content

    max_omitted_digits = len(str(content.count("\n")))
    marker_budget = len(f"\n\n[... {'9' * max_omitted_digits} lines omitted ...]\n")
    available = max(max_chars - marker_budget, 0)
    if available == 0:
        return content[:max_chars]

    # Keep 60% head, 40% tail — overview nodes are denser at the top.
    head_budget = int(available * 0.6)
    tail_budget = available - head_budget

    # Snap inward to line boundaries so the head/tail stay within budget.
    head_end = content.rfind("\n", 0, head_budget)
    if head_end < 0:
        head_end = head_budget
    tail_start = content.find("\n", len(content) - tail_budget)
    if tail_start < 0:
        tail_start = len(content) - tail_budget
    else:
        tail_start += 1
    head = content[:head_end]
    tail = content[tail_start:]
    omitted = content[head_end:tail_start].count("\n")
    marker = f"\n\n[... {omitted} lines omitted ...]\n"
    result = f"{head}{marker}{tail}"
    if len(result) > max_chars:
        overflow = len(result) - max_chars
        if overflow < len(tail):
            tail = tail[overflow:]
        else:
            head = head[:max(len(head) - (overflow - len(tail)), 0)]
            tail = ""
        result = f"{head}{marker}{tail}"
    return result[:max_chars]


def find_project_root() -> Path:
    """Find project root by looking for the nearest .agent/tasks/ directory."""
    cwd = Path.cwd()

    for p in [cwd, *cwd.parents]:
        if (p / ".agent" / "tasks").exists():
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
            session_id = os.environ.get("PLAYBOOK_SESSION_ID", "") or "default"
            session_state = agent_dir / "sessions" / session_id / "current_state"

            # Find the active task from session state file
            prev_task = session_state.read_text(encoding="utf-8").strip() if session_state.exists() else None

            if prev_task:
                # Set ## Status to done in task.md
                tasks_dir = project_path / ".agent" / "tasks"
                matches = list(tasks_dir.glob(f"{prev_task}-*/task.md"))
                if matches:
                    task_file = matches[0]
                    lines = task_file.read_text(encoding="utf-8").splitlines(keepends=True)
                    for i, line in enumerate(lines):
                        if line.strip() == "## Status" and i + 1 < len(lines):
                            lines[i + 1] = "done\n"
                            task_file.write_text("".join(lines), encoding="utf-8")
                            break
                # Remove session dirs that reference this task.
                # PLAYBOOK_SESSION_ID is not set when called from Bash tool, so scan all sessions.
                # Intentional partial delete: only sessions pointing at prev_task are removed;
                # sessions for other tasks are left intact.
                sessions_dir = agent_dir / "sessions"
                if sessions_dir.exists():
                    for sf in sessions_dir.glob("*/current_state"):
                        try:
                            if sf.read_text(encoding="utf-8").strip() == prev_task:
                                shutil.rmtree(sf.parent, ignore_errors=True)
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
                    lines = tf.read_text(encoding="utf-8").splitlines(keepends=True)
                    for i, line in enumerate(lines):
                        if line.strip() == "## Status" and i + 1 < len(lines):
                            lines[i + 1] = "in_progress\n"
                            tf.write_text("".join(lines), encoding="utf-8")
                            break
                    print(f"Note: task {task_num} was marked done — reopening.")
                    task_file = tf
                    # Fall through to activation below
                elif "<!-- stub:" in tf.read_text(encoding="utf-8"):
                    # Stub — allow activation, expansion happens below
                    task_file = tf
                else:
                    print(f"Task {task_num} has no open gates.", file=sys.stderr)
                    sys.exit(1)
            else:
                print(f"Task {task_num} not found", file=sys.stderr)
                sys.exit(1)

        # Auto-close previous task if all gates are checked
        agent_dir = project_path / ".agent"
        agent_dir.mkdir(parents=True, exist_ok=True)
        session_id = os.environ.get("PLAYBOOK_SESSION_ID", "") or "default"
        session_dir = agent_dir / "sessions" / session_id
        session_state = session_dir / "current_state"
        prev_task = None
        if session_state.exists():
            prev_task = session_state.read_text(encoding="utf-8").strip()
        if prev_task and prev_task != task_num:
            from tasks.core import _extract_head_position, _extract_status
            prev_matches = list((project_path / ".agent" / "tasks").glob(f"{prev_task}-*/task.md"))
            if prev_matches:
                prev_file = prev_matches[0]
                prev_status = _extract_status(prev_file)
                prev_head = _extract_head_position(prev_file)
                if prev_head == "(all gates checked)" and not prev_status.startswith("done"):
                    # Auto-close: set status to done
                    prev_lines = prev_file.read_text(encoding="utf-8").splitlines(keepends=True)
                    for i, line in enumerate(prev_lines):
                        if line.strip() == "## Status" and i + 1 < len(prev_lines):
                            prev_lines[i + 1] = "done\n"
                            prev_file.write_text("".join(prev_lines), encoding="utf-8")
                            break
                    print(f"Auto-closed task {prev_task} (all gates checked).")

        # Write task number to per-session current_state
        session_dir.mkdir(parents=True, exist_ok=True)
        session_state.write_text(f"{task_num}\n", encoding="utf-8")

        # Clean up stale session dirs older than 24h (remove entire dir, not just current_state)
        sessions_dir = agent_dir / "sessions"
        cutoff = time.time() - 86400
        if sessions_dir.exists():
            for sf in sessions_dir.glob("*/current_state"):
                try:
                    if sf.stat().st_mtime < cutoff:
                        shutil.rmtree(sf.parent, ignore_errors=True)
                except OSError:
                    pass

        # Expand stubs on activation
        task_content = task_file.read_text(encoding="utf-8")
        import re as _stub_re
        stub_match = _stub_re.search(r'<!-- stub:(\w+) -->', task_content)
        if stub_match:
            stub_type = stub_match.group(1)
            # Extract user's Intent and Why sections before expanding
            def _extract_section(content, heading):
                pattern = rf'^## {heading}\n(.*?)(?=\n## |\Z)'
                m = _stub_re.search(pattern, content, _stub_re.MULTILINE | _stub_re.DOTALL)
                return m.group(1).strip() if m else ""

            user_intent = _extract_section(task_content, "Intent")
            user_why = _extract_section(task_content, "Why")
            user_refs = _extract_section(task_content, "References")

            # Render full template
            from tasks.template import render_template
            task_num_int = int(task_num)
            title = task_file.parent.name.split("-", 1)[1].replace("-", " ").title()
            full_content = render_template(num=task_num_int, title=title, task_type=stub_type)

            # F3: Append playbook role template (same as create_task)
            from tasks.core import _load_playbook
            role_template = _load_playbook(stub_type, project_path)
            if role_template:
                full_content += "\n" + role_template + "\n"

            # Inject preserved user content
            if user_intent:
                # F2: Try both placeholder variants (build + quick)
                for placeholder in [
                    "(what we want to achieve \u2014 the outcome, not the activity)",
                    "(one line \u2014 what to do and how to verify)",
                ]:
                    if placeholder in full_content:
                        full_content = full_content.replace(placeholder, user_intent)
                        break
            if user_why:
                full_content = full_content.replace(
                    "(why this matters now \u2014 urgency, context, what breaks if delayed)",
                    user_why,
                )
            # F1: Inject preserved references
            if user_refs and "(optional)" not in user_refs.lower():
                # Replace the default References content
                full_content = _stub_re.sub(
                    r'(## References\n).*?(?=\n---)',
                    f'## References\n{user_refs}',
                    full_content,
                    count=1,
                    flags=_stub_re.DOTALL,
                )

            task_file.write_text(full_content, encoding="utf-8")
            # Re-read for chat injection and display
            task_content = full_content
            print(f"Expanded stub to full {stub_type} template.")

        # Workflow rules — deferred from bootstrap to task activation
        from tasks.template import workflow_briefing
        print("=== WORKFLOW ===")
        print(workflow_briefing())
        print()

        # Capture recent chat messages into task.md
        recent_chat = _capture_recent_chat(project_path)
        if recent_chat:
            _inject_chat_into_task(task_file, recent_chat)
            print(f"Captured {len(recent_chat)} recent chat message(s) into References.")

        # Print the full task file
        print(task_file.read_text(encoding="utf-8").rstrip())


    elif cmd == "new":
        # Parse --stub flag
        is_stub = False
        if cmd_args and cmd_args[0] == "--stub":
            is_stub = True
            cmd_args = cmd_args[1:]

        if len(cmd_args) < 2:
            print("Error: 'new' requires a type and a name", file=sys.stderr)
            print("Usage: tasks new [--stub] <type> <name> [intent...]", file=sys.stderr)
            from tasks.core import list_all_types
            all_types = list_all_types(find_project_root())
            print(f"Types: {', '.join(all_types)}", file=sys.stderr)
            sys.exit(1)

        task_type = cmd_args[0]
        from tasks.core import list_all_types, _find_custom_playbook
        project_path_for_check = find_project_root()
        is_custom = _find_custom_playbook(project_path_for_check, task_type) is not None
        if task_type not in PLAYBOOKS and task_type != "quick" and not is_custom:
            all_types = list_all_types(project_path_for_check)
            print(f"Error: unknown type '{task_type}'", file=sys.stderr)
            print(f"Types: {', '.join(all_types)}", file=sys.stderr)
            sys.exit(1)

        # args[1] = name, args[2:] = optional intent text
        task_name = cmd_args[1]
        intent_text = " ".join(cmd_args[2:]) if len(cmd_args) > 2 else None
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
        task_file = create_task(project_path, task_name, task_type=task_type,
                               intent_text=intent_text, stub=is_stub)
        pattern_name = PLAYBOOKS.get(task_type, f"custom ({task_type})")

        import re
        task_num_match = re.match(r'^(\d+)-', task_file.parent.name)
        task_num = task_num_match.group(1) if task_num_match else "?"

        print(f"Created: {task_file.relative_to(project_path)}")
        if is_stub:
            print(f"Stub ({pattern_name}) — expand with: tasks work {task_num}")
        elif task_type != "quick":
            print(f"Pattern: {pattern_name}")
            print(f"Next: fill in task.md gates, then ask user to run: tasks work {task_num}")
        else:
            print(f"Next: fill in task.md gates, then ask user to run: tasks work {task_num}")
        print()

        if task_type != "quick":
            # Print full playbook so agent has workflow guidance inline
            playbook_path = _find_playbook_skill(project_path)
            if playbook_path:
                playbook_file = Path(playbook_path)
                if playbook_file.exists():
                    print("=== PLAYBOOK (task.md design guide) ===")
                    print("Use this to improve your task.md: select patterns and gates as appropriate,")
                    print("or invent new ones. This is a starting point — expand as needed.")
                    print()
                    content = playbook_file.read_text(encoding="utf-8")
                    # Strip sections not relevant to task design
                    for marker in ["## Mind Map", "> Evidence base:"]:
                        idx = content.find(marker)
                        if idx > 0:
                            content = content[:idx]
                    print(content.rstrip())
                    print()
                    print(f"Now fill in {task_file.relative_to(project_path)} — design a good task.md.")

    elif cmd == "init":
        # Parse provider-specific init flags (additive on top of normal init)
        provider = None
        install_provider_hooks = False
        remaining_init_args = []
        i = 0
        while i < len(cmd_args):
            if cmd_args[i] == "--provider" and i + 1 < len(cmd_args):
                provider = cmd_args[i + 1]
                i += 2
            elif cmd_args[i] == "--hooks":
                install_provider_hooks = True
                i += 1
            else:
                remaining_init_args.append(cmd_args[i])
                i += 1
        cmd_args = remaining_init_args

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
""", encoding="utf-8")
            print("  MIND_MAP.md    created")
        else:
            print("  MIND_MAP.md    exists")

        # Create CLAUDE.md
        claude_md = target / "CLAUDE.md"
        if not claude_md.exists():
            from tasks.template import claude_md as claude_md_template
            claude_md.write_text(claude_md_template(title), encoding="utf-8")
            print("  CLAUDE.md      created")
        else:
            print("  CLAUDE.md      exists")

        # Check for duplicate hook registrations
        settings_file = target / ".claude" / "settings.json"
        if settings_file.exists():
            import json
            try:
                settings = json.loads(settings_file.read_text(encoding="utf-8"))
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

        # --provider: install provider-specific bootstrap file (additive)
        if provider:
            _PROVIDER_MAP = {"codex": "CodexAdapter", "gemini": "GeminiAdapter"}
            if provider not in _PROVIDER_MAP:
                print(f"Error: unknown provider '{provider}'. Choose: codex, gemini", file=sys.stderr)
                sys.exit(1)
            if install_provider_hooks and provider != "codex":
                print("Error: --hooks is currently supported only with --provider codex", file=sys.stderr)
                sys.exit(1)
            import importlib
            adapter_cls_name = _PROVIDER_MAP[provider]
            mod = importlib.import_module(f"provider.adapters.{provider}")
            adapter_cls = getattr(mod, adapter_cls_name)
            bootstrap_file = {"codex": "AGENTS.md", "gemini": "GEMINI.md"}[provider]
            bs_path = target / bootstrap_file
            already_existed = bs_path.exists()
            adapter = adapter_cls("init", target)
            adapter.install_bootstrap(target)
            print(f"  {bootstrap_file:<15}{'exists' if already_existed else 'created'}")
            if install_provider_hooks:
                adapter.install_hooks(target)
        elif install_provider_hooks:
            print("Error: --hooks requires --provider codex", file=sys.stderr)
            sys.exit(1)

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

        # CLI reference — shown last so mind map + tasks aren't buried
        from tasks.template import cli_reference
        print()
        print("=== CLI REFERENCE ===")
        print(cli_reference())

    elif cmd in ("list", "ls"):
        project_path = find_project_root()
        pending_only = "--pending" in cmd_args
        list_tasks(project_path, pending_only=pending_only)

    elif cmd == "panel-review":
        import subprocess
        from concurrent.futures import ProcessPoolExecutor, TimeoutError as FuturesTimeout

        # Parse flags
        review_mode = "plan"
        web_search = False
        timeout_secs = 300  # 5 min default
        extra_prompt = ""
        no_mind_map = False
        bare = False
        remaining_args = []
        i = 0
        while i < len(cmd_args):
            if cmd_args[i] == "--mode" and i + 1 < len(cmd_args):
                review_mode = cmd_args[i + 1]
                i += 2
            elif cmd_args[i] == "--web-search":
                web_search = True
                i += 1
            elif cmd_args[i] == "--timeout" and i + 1 < len(cmd_args):
                timeout_secs = int(cmd_args[i + 1])
                i += 2
            elif cmd_args[i] == "--prompt" and i + 1 < len(cmd_args):
                extra_prompt = cmd_args[i + 1]
                i += 2
            elif cmd_args[i] == "--no-mind-map":
                no_mind_map = True
                i += 1
            elif cmd_args[i] == "--bare":
                bare = True
                i += 1
            else:
                remaining_args.append(cmd_args[i])
                i += 1

        if review_mode not in ("plan", "impl"):
            print(f"Error: unknown mode '{review_mode}'", file=sys.stderr)
            sys.exit(1)

        task_num = remaining_args[0] if remaining_args else ""
        if task_num.isdigit():
            task_num = task_num.zfill(3)

        # Task number is optional; --prompt required when omitted
        if not task_num and not extra_prompt:
            print("Error: 'panel-review' requires a task number or --prompt", file=sys.stderr)
            print("Usage: tasks panel-review [<number>] [--mode plan|impl] [--prompt \"...\"] [--no-mind-map] [--bare] [--web-search] [--timeout SECONDS]", file=sys.stderr)
            sys.exit(1)

        project_path = find_project_root()

        # Resolve task file if task number given
        task_file = None
        task_path = None
        if task_num:
            tasks_dir = project_path / ".agent" / "tasks"
            matches = list(tasks_dir.glob(f"{task_num}-*/task.md"))
            if not matches:
                print(f"Task {task_num} not found", file=sys.stderr)
                sys.exit(1)
            task_file = matches[0]
            task_path = str(task_file.relative_to(project_path))

        from tasks.template import panel_plan_review_prompt, panel_impl_review_prompt

        # Build context
        MAX_CONTEXT_CHARS = 100_000
        context_parts = []
        if not bare:
            if not no_mind_map:
                mm_content = _load_mind_map(project_path)
                if mm_content:
                    context_parts.append(f"=== MIND_MAP.md ===\n{mm_content}")
            if task_file:
                task_content = task_file.read_text(encoding="utf-8")
                if len(task_content) > MAX_CONTEXT_CHARS // 2:
                    task_content = task_content[:MAX_CONTEXT_CHARS // 2] + "\n\n[... truncated ...]"
                context_parts.append(f"=== {task_path} ===\n{task_content}")
            else:
                # Taskless: include recent chat log as project context
                chat_log = project_path / ".agent" / "chat_log.md"
                if chat_log.exists():
                    chat_content = chat_log.read_text(encoding="utf-8")
                    max_chat = MAX_CONTEXT_CHARS // 2
                    if len(chat_content) > max_chat:
                        chat_content = "[... truncated ...]\n\n" + chat_content[-max_chat:]
                    context_parts.append(f"=== .agent/chat_log.md (recent) ===\n{chat_content}")
        system_context = "\n\n".join(context_parts)
        if len(system_context) > MAX_CONTEXT_CHARS:
            system_context = system_context[:MAX_CONTEXT_CHARS] + "\n\n[... truncated ...]"

        # Prompt strategy: bare/taskless → extra_prompt is full mission; with task → review prompt + optional steering
        if task_file:
            prompt_fn = panel_plan_review_prompt if review_mode == "plan" else panel_impl_review_prompt
            review_label = "plan review" if review_mode == "plan" else "impl review"
        else:
            prompt_fn = None
            review_label = "panel"

        # Output path: task dir when task given, .agent/ otherwise
        if task_file:
            judge_md = task_file.parent / "judge.md"
        else:
            (project_path / ".agent").mkdir(exist_ok=True)
            judge_md = project_path / ".agent" / "judge.md"

        # Discover available judges
        judges = []
        claude_bin = shutil.which("claude")
        codex_bin = shutil.which("codex")
        gemini_bin = shutil.which("gemini")

        if claude_bin:
            for variant in ["opus", "sonnet", "haiku"]:
                judges.append(("claude", variant, claude_bin))
        if codex_bin:
            for variant in ["gpt-5.4", "gpt-5.1-codex-max"]:
                judges.append(("codex", variant, codex_bin))
        if gemini_bin:
            for variant in ["3.1-pro", "2.5-pro"]:
                judges.append(("gemini", variant, gemini_bin))

        if not judges:
            print("Error: no judge backends found (need claude, codex, or gemini on PATH)", file=sys.stderr)
            sys.exit(1)

        display_target = task_path or "(promptless)"
        print(f"Running panel {review_label} on {display_target} ({len(judges)} judges, {timeout_secs}s timeout)...", flush=True)

        def run_judge(judge_spec):
            provider, variant, binary = judge_spec
            label = f"{provider}:{variant}" if variant else provider
            if prompt_fn:
                prompt = prompt_fn(task_path, inline_context=(provider != "claude"))
                if extra_prompt:
                    prompt += f"\n\nAdditional steering from the user:\n{extra_prompt}"
            else:
                prompt = extra_prompt

            try:
                if provider == "claude":
                    tools = "Read,Glob,Grep"
                    if web_search:
                        tools += ",WebSearch"
                    env = os.environ.copy()
                    env["CLAUDECODE"] = ""
                    env.pop("CLAUDE_CODE_SSE_PORT", None)
                    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
                    env.pop("CLAUDE_PROJECT_DIR", None)
                    # --allowedTools suppresses plugin hook registration — intentional:
                    # review agents are read-only evaluators, not task workers.
                    cmd_list = [
                        binary, "-p", prompt,
                        "--model", variant,
                        "--tools", tools,
                        "--allowedTools", tools,
                        "--append-system-prompt", system_context,
                    ]
                    result = subprocess.run(
                        cmd_list, cwd=str(project_path), env=env,
                        capture_output=True, text=True, timeout=timeout_secs,
                    )
                    return label, result.stdout or "(no output)"

                elif provider == "codex":
                    full_prompt = f"{system_context}\n\n---\n\n{prompt}"
                    cmd_list = [binary]
                    if web_search:
                        cmd_list.append("--search")
                    cmd_list += ["exec", "--ephemeral", "--skip-git-repo-check",
                        "--sandbox", "read-only"]
                    if variant:
                        cmd_list += ["-m", variant]
                    cmd_list.append("-")
                    result = subprocess.run(
                        cmd_list, cwd=str(project_path),
                        input=full_prompt, capture_output=True, text=True,
                        timeout=timeout_secs,
                    )
                    return label, result.stdout or "(no output)"

                elif provider == "gemini":
                    full_prompt = f"{system_context}\n\n---\n\n{prompt}"
                    # Map simplified variant names to full model IDs
                    model_id = f"gemini-{variant}"
                    if variant == "3.1-pro":
                        model_id = "gemini-3.1-pro-preview"
                    
                    cmd_list = [
                        binary, "-p", full_prompt,
                        "-m", model_id,
                        "--approval-mode", "plan",
                        "-o", "text",
                    ]
                    result = subprocess.run(
                        cmd_list, cwd=str(project_path),
                        capture_output=True, text=True, timeout=timeout_secs,
                    )
                    return label, result.stdout or "(no output)"

            except subprocess.TimeoutExpired:
                return label, f"(timed out after {timeout_secs}s)"
            except Exception as e:
                return label, f"(error: {e})"

        # Run all judges in parallel
        import concurrent.futures
        results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(judges)) as executor:
            futures = {executor.submit(run_judge, j): j for j in judges}
            for future in concurrent.futures.as_completed(futures):
                label, output = future.result()
                results[label] = output
                print(f"  [{label}] done", flush=True)

        # Write judge.md (path already set above based on task_file presence)
        display_label = task_path or extra_prompt[:60]
        lines = [f"# Panel {review_label.title()} — {display_label}\n"]
        lines.append(f"**Judges:** {len(results)} | **Web search:** {'yes' if web_search else 'no'} | **Timeout:** {timeout_secs}s\n\n")
        for label in sorted(results.keys()):
            lines.append("═" * 60)
            lines.append(f"  JUDGE: {label}")
            lines.append("═" * 60 + "\n")
            lines.append(results[label].strip())
            lines.append("\n\n")
        judge_md.write_text("\n".join(lines), encoding="utf-8")
        print(f"\nSaved: {judge_md.relative_to(project_path)} ({len(results)}/{len(judges)} judges)", flush=True)

    elif cmd in ("plan-review", "impl-review", "judge"):
        # "judge" is a legacy alias — auto-detects mode from task status
        review_cmd = cmd
        if not cmd_args:
            print(f"Error: '{review_cmd}' requires a task number", file=sys.stderr)
            print(f"Usage: tasks {review_cmd} <number> [--backend claude|codex|gemini]", file=sys.stderr)
            sys.exit(1)

        import subprocess

        # Parse flags
        backend = "claude"
        extra_prompt = ""
        remaining_args = []
        i = 0
        while i < len(cmd_args):
            if cmd_args[i] == "--backend" and i + 1 < len(cmd_args):
                backend = cmd_args[i + 1]
                i += 2
            elif cmd_args[i] == "--prompt" and i + 1 < len(cmd_args):
                extra_prompt = cmd_args[i + 1]
                i += 2
            else:
                remaining_args.append(cmd_args[i])
                i += 1

        if backend not in ("claude", "codex", "gemini"):
            print(f"Error: unknown backend '{backend}'", file=sys.stderr)
            print("Supported: claude (default), codex, gemini", file=sys.stderr)
            sys.exit(1)

        if not remaining_args:
            print(f"Error: '{review_cmd}' requires a task number", file=sys.stderr)
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

        from tasks.template import plan_review_prompt, impl_review_prompt

        # Build context: mind map + task content (bounded to avoid argv/context limits)
        MAX_CONTEXT_CHARS = 100_000
        context_parts = []
        mm_content = _load_mind_map(project_path)
        if mm_content:
            context_parts.append(f"=== MIND_MAP.md ===\n{mm_content}")
        task_content = task_file.read_text(encoding="utf-8")
        if len(task_content) > MAX_CONTEXT_CHARS // 2:
            task_content = task_content[:MAX_CONTEXT_CHARS // 2] + "\n\n[... truncated for context budget ...]"
        context_parts.append(f"=== {task_path} ===\n{task_content}")
        system_context = "\n\n".join(context_parts)
        if len(system_context) > MAX_CONTEXT_CHARS:
            system_context = system_context[:MAX_CONTEXT_CHARS] + "\n\n[... truncated for context budget ...]"

        # Determine mode: explicit from command, or auto-detect for legacy "judge"
        if review_cmd == "plan-review":
            review_mode = "plan"
        elif review_cmd == "impl-review":
            review_mode = "impl"
        else:  # legacy "judge" — auto-detect from status
            from tasks.core import _extract_status
            review_mode = "impl" if _extract_status(task_file).startswith("done") else "plan"

        prompt_fn = plan_review_prompt if review_mode == "plan" else impl_review_prompt
        review_label = "plan review" if review_mode == "plan" else "impl review"

        if backend == "claude":
            claude_bin = shutil.which("claude")
            if not claude_bin:
                print("Error: 'claude' not found on PATH", file=sys.stderr)
                sys.exit(1)

            prompt = prompt_fn(task_path)
            if extra_prompt:
                prompt += f"\n\nAdditional steering from the user:\n{extra_prompt}"
            env = os.environ.copy()
            env["CLAUDECODE"] = ""
            env.pop("CLAUDE_CODE_SSE_PORT", None)
            env.pop("CLAUDE_CODE_ENTRYPOINT", None)
            env["PLAYBOOK_SESSION_ID"] = "judge"

            # --dangerously-skip-permissions suppresses plugin hook registration —
            # intentional: judge is a read-only evaluator (seatbelt-sandboxed on
            # macOS), not a task worker. PLAYBOOK_SESSION_ID=judge above lets
            # hooks identify judge sessions if needed.
            claude_args = [
                claude_bin, "-p",
                "--dangerously-skip-permissions",
                "--max-budget-usd", "2",
                "--append-system-prompt", system_context,
                prompt,
            ]

            # Wrap in seatbelt sandbox on macOS (write containment)
            # Skip if already inside sandbox (nested sandbox-exec is not allowed)
            import platform
            already_sandboxed = (
                os.environ.get("PLAYBOOK_SANDBOXED") == "1"
                or str(project_path).startswith("/tmp/eval-")
                or str(project_path).startswith("/private/tmp/eval-")
            )
            if not already_sandboxed and platform.system() == "Darwin" and shutil.which("sandbox-exec"):
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

            print(f"Running {review_label} (claude) on {task_path}...", flush=True)
            result = subprocess.run(
                cmd_list,
                cwd=str(project_path),
                env=env,
                capture_output=True,
                text=True,
            )

        elif backend == "codex":
            codex_bin = shutil.which("codex")
            if not codex_bin:
                print("Error: 'codex' not found on PATH", file=sys.stderr)
                print("Install: https://github.com/openai/codex", file=sys.stderr)
                sys.exit(1)

            prompt = prompt_fn(task_path, inline_context=True)
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

            print(f"Running {review_label} (codex) on {task_path}...", flush=True)
            result = subprocess.run(
                cmd_list,
                cwd=str(project_path),
                input=full_prompt,
                capture_output=True,
                text=True,
            )

        else:  # gemini
            gemini_bin = shutil.which("gemini")
            if not gemini_bin:
                print("Error: 'gemini' not found on PATH", file=sys.stderr)
                sys.exit(1)

            prompt = prompt_fn(task_path, inline_context=True)
            full_prompt = f"{system_context}\n\n---\n\n{prompt}"
            if extra_prompt:
                full_prompt += f"\n\nAdditional steering from the user:\n{extra_prompt}"

            cmd_list = [
                gemini_bin, "-p", full_prompt,
                "-m", "gemini-3.1-pro-preview",
                "--approval-mode", "plan",
                "-o", "text",
            ]

            print(f"Running {review_label} (gemini) on {task_path}...", flush=True)
            result = subprocess.run(
                cmd_list,
                cwd=str(project_path),
                capture_output=True,
                text=True,
            )

        if result.stdout:
            print(result.stdout, end="", flush=True)
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr, flush=True)

        # Save output — backend-specific log files
        log_name = {
            "claude": "judge.log",
            "codex": "judge-codex.log",
            "gemini": "judge-gemini.log",
        }.get(backend, "judge.log")
        judge_log = task_file.parent / log_name
        output = (result.stdout or "").strip()
        if result.returncode != 0 and not output:
            if judge_log.exists():
                print(f"\nReview failed (exit {result.returncode}); kept previous {judge_log.relative_to(project_path)}", flush=True)
            else:
                print(f"\nReview failed (exit {result.returncode}); no output to save", flush=True)
        else:
            if backend == "claude":
                judge_log.write_text(result.stdout or "", encoding="utf-8")
            # codex: -o already writes the file; write stdout as fallback
            elif not judge_log.exists() or not judge_log.read_text(encoding="utf-8").strip():
                judge_log.write_text(result.stdout or "", encoding="utf-8")
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
        for line in chat_log.read_text(encoding="utf-8").splitlines():
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
        for line in bash_history.read_text(encoding="utf-8").splitlines():
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

        for line in chat_log.read_text(encoding="utf-8").splitlines():
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
        for line in bash_history.read_text(encoding="utf-8").splitlines():
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
        for line in bash_history.read_text(encoding="utf-8").splitlines():
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

        lines = chat_log.read_text(encoding="utf-8").splitlines(keepends=True)
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
            chat_log.write_text("".join(output), encoding="utf-8")
            print(f"Inserted {tags_inserted} tags into chat_log.md")

    elif cmd == "retro":
        project_path = find_project_root()
        # Parse --since N flag
        since = 0
        i = 0
        while i < len(cmd_args):
            if cmd_args[i] == "--since" and i + 1 < len(cmd_args):
                try:
                    since = int(cmd_args[i + 1])
                except ValueError:
                    print(f"Error: --since requires a number", file=sys.stderr)
                    sys.exit(1)
                i += 2
            else:
                i += 1

        from tasks.retro import (
            extract_tasks, extract_chatlog, extract_mindmap,
            build_task_windows,
        )

        tasks_dir = project_path / ".agent" / "tasks"
        chatlog_path = project_path / ".agent" / "chat_log.md"
        bash_history_path = project_path / ".agent" / "bash_history"
        mindmap_path = project_path / "MIND_MAP.md"

        # Extract data
        tasks = extract_tasks(tasks_dir, since=since)
        task_windows = build_task_windows(chatlog_path, bash_history_path)
        chatlog = extract_chatlog(chatlog_path, task_windows)
        mindmap = extract_mindmap(mindmap_path)

        if not tasks:
            print("No tasks found in window.", file=sys.stderr)
            sys.exit(1)

        # Run structural analysis passes
        from tasks.retro import (
            analyze_intent_health, analyze_garbage,
            generate_retro_task,
        )
        health = analyze_intent_health(tasks)
        gc = analyze_garbage(tasks)

        # Generate the retro task.md — a cognitive program
        retro_content = generate_retro_task(
            tasks=tasks, chatlog=chatlog, mindmap=mindmap,
            health=health, gc=gc,
        )

        # Create as a new task
        from tasks.core import _next_task_number, _slugify
        tasks_dir_path = project_path / ".agent" / "tasks"
        task_num = _next_task_number(tasks_dir_path)
        first = tasks[0]["number"]
        last = tasks[-1]["number"]
        slug = f"retro-{first:03d}-{last:03d}"
        folder_name = f"{task_num:03d}-{slug}"
        task_dir = tasks_dir_path / folder_name
        task_dir.mkdir(parents=True)
        task_file = task_dir / "task.md"
        task_file.write_text(retro_content, encoding="utf-8")

        print(f"Created: {task_file.relative_to(project_path)}")
        print(f"Retro task T{task_num:03d} — {len(tasks)} tasks in window, "
              f"{len(chatlog)} chat messages, {len(mindmap)} mind map nodes")
        print(f"Next: tasks work {task_num}")

    elif cmd == "status":
        project_path = find_project_root()
        task_status(project_path)

    elif cmd == "freehand":
        project_path = find_project_root()
        sub = cmd_args[0] if cmd_args else None

        if sub == "log":
            # Extract chat_log messages from freehand-start to now into task.md
            agent_dir = project_path / ".agent"
            state_file = _state_file(project_path)
            if not state_file.exists():
                print("Error: no active task", file=sys.stderr)
                sys.exit(1)
            task_num = state_file.read_text(encoding="utf-8").strip()
            tasks_dir = agent_dir / "tasks"
            matches = list(tasks_dir.glob(f"{task_num}-*/task.md"))
            if not matches:
                print(f"Error: task {task_num} not found", file=sys.stderr)
                sys.exit(1)
            task_file = matches[0]
            task_text = task_file.read_text(encoding="utf-8")

            # Find the freehand-start marker
            import re
            # Use findall + take last — supports multiple freehand blocks in one task
            all_markers = re.findall(r'<!-- freehand-start: (.+?) -->', task_text)
            marker_match = all_markers[-1] if all_markers else None
            if not marker_match:
                print("Error: no freehand-start marker found in task.md", file=sys.stderr)
                sys.exit(1)

            # Parse the start timestamp
            from datetime import datetime, timezone
            start_str = marker_match.strip()
            try:
                start_ts = datetime.fromisoformat(start_str)
                if start_ts.tzinfo is None:
                    start_ts = start_ts.replace(tzinfo=timezone.utc)
            except ValueError:
                print(f"Error: cannot parse freehand-start timestamp: {start_str}", file=sys.stderr)
                sys.exit(1)

            # Read chat_log.md and extract messages in the span
            chat_log = agent_dir / "chat_log.md"
            if not chat_log.exists():
                print("Error: .agent/chat_log.md not found", file=sys.stderr)
                sys.exit(1)

            log_text = chat_log.read_text(encoding="utf-8")
            # Parse message blocks: **[MNNN]** [YYYY-MM-DD HH:MM:SS UTC]
            msg_pattern = re.compile(
                r'^(\*\*\[M\d+\]\*\* \[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC\].*)',
                re.MULTILINE
            )
            # Split log into message blocks by the --- separator
            blocks = log_text.split("\n---\n")
            extracted = []
            for block in blocks:
                m = msg_pattern.search(block)
                if m:
                    ts_str = m.group(2)
                    try:
                        msg_ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    except ValueError:
                        continue
                    if msg_ts >= start_ts:
                        extracted.append(block.strip())

            if not extracted:
                print("No chat_log messages found in freehand span.")
                return

            # Insert extracted messages into task.md below the Freehand log gate
            log_gate_pattern = re.compile(r'^(- \[ \] Freehand log\b.*)', re.MULTILINE)
            log_gate_match = log_gate_pattern.search(task_text)
            if not log_gate_match:
                print("Error: no '- [ ] Freehand log' gate found in task.md", file=sys.stderr)
                sys.exit(1)

            insert_pos = log_gate_match.end()
            log_content = "\n\n" + "\n\n---\n\n".join(extracted) + "\n"
            new_text = task_text[:insert_pos] + log_content + task_text[insert_pos:]
            task_file.write_text(new_text, encoding="utf-8")
            print(f"Inserted {len(extracted)} chat_log messages into task.md")
            return

        # Main freehand command: insert Freehand block into active task
        state_file = _state_file(project_path)
        agent_dir = project_path / ".agent"

        if state_file.exists():
            task_num = state_file.read_text(encoding="utf-8").strip()
        else:
            task_num = None

        if not task_num:
            # Orchestrator mode: create + activate a new task
            print("No active task — creating freehand session...")
            task_file = create_task(project_path, "freehand", task_type="feature")
            # Extract task number from dir name
            task_num = task_file.parent.name.split("-")[0]
            # Activate it
            session_id = os.environ.get("PLAYBOOK_SESSION_ID", "") or "default"
            session_dir = agent_dir / "sessions" / session_id
            session_dir.mkdir(parents=True, exist_ok=True)
            (session_dir / "current_state").write_text(f"{task_num}\n", encoding="utf-8")
            print(f"Created and activated task {task_num}")
        else:
            # Work mode: insert into current task
            tasks_dir = agent_dir / "tasks"
            matches = list(tasks_dir.glob(f"{task_num}-*/task.md"))
            if not matches:
                print(f"Error: task {task_num} not found", file=sys.stderr)
                sys.exit(1)
            task_file = matches[0]

        # Insert Freehand block before first unchecked gate in Work Plan
        from datetime import datetime, timezone
        task_text = task_file.read_text(encoding="utf-8")
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        freehand_block = (
            f"\n### Freehand\n"
            f"<!-- freehand-start: {now_iso} -->\n"
            f"- [ ] Freehand\n"
            f"- [ ] Freehand log — run `.claude/bin/tasks freehand log` to capture chat_log messages, "
            f"then retro-add checked gates for work done\n"
            f"- [ ] Rewrite this freehand work into normal task gates inside this task so the final trace reads like ordinary tracked work\n"
            f"- [ ] Rename this task folder and header to match what was actually done, then check this gate last\n"
        )

        # Find Work Plan section and insert before first unchecked gate there
        import re
        work_plan_match = re.search(r'^## Work Plan\b', task_text, re.MULTILINE)
        if work_plan_match:
            # Search for first unchecked gate after Work Plan header
            after_wp = task_text[work_plan_match.start():]
            gate_match = re.search(r'^- \[ \]', after_wp, re.MULTILINE)
            if gate_match:
                insert_pos = work_plan_match.start() + gate_match.start()
            else:
                # No unchecked gates — find the next --- separator and insert before it
                sep_match = re.search(r'\n---\n', after_wp)
                if sep_match:
                    insert_pos = work_plan_match.start() + sep_match.start()
                else:
                    insert_pos = len(task_text)
        else:
            # No Work Plan section — append at end
            insert_pos = len(task_text)

        new_text = task_text[:insert_pos] + freehand_block + "\n" + task_text[insert_pos:]
        task_file.write_text(new_text, encoding="utf-8")
        print(f"Freehand block inserted in task {task_num}")
        print(f"Freehand mode active. Agent: wait for user instructions. Close only when user says done.")

    elif cmd == "doctor":
        project_path = find_project_root()
        passed = 0
        failed = 0

        def iter_hook_commands(node):
            if isinstance(node, dict):
                command = node.get("command")
                if isinstance(command, str):
                    yield command
                for value in node.values():
                    yield from iter_hook_commands(value)
            elif isinstance(node, list):
                for item in node:
                    yield from iter_hook_commands(item)

        def check(name: str, ok: bool, detail: str = ""):
            nonlocal passed, failed
            status = "PASS" if ok else "FAIL"
            msg = f"  [{status}] {name}"
            if detail:
                msg += f" — {detail}"
            print(msg)
            if ok:
                passed += 1
            else:
                failed += 1

        print("tasks doctor\n")

        # 1. Project structure
        agent_tasks = project_path / ".agent" / "tasks"
        check("project: .agent/tasks/ exists", agent_tasks.exists())
        claude_md = project_path / "CLAUDE.md"
        check("project: CLAUDE.md exists", claude_md.exists())
        mind_map = project_path / "MIND_MAP.md"
        check("project: MIND_MAP.md exists", mind_map.exists())

        # 2. Unicode
        stdout_enc = getattr(sys.stdout, "encoding", "unknown") or "unknown"
        check("unicode: stdout encoding", "utf" in stdout_enc.lower(), stdout_enc)

        # 3. Stale session dirs (current_state older than 24h — orphaned from crashed sessions)
        agent_dir = project_path / ".agent"
        stale = []
        sessions_dir = agent_dir / "sessions"
        if sessions_dir.exists():
            cutoff = time.time() - 86400
            for sf in sessions_dir.glob("*/current_state"):
                try:
                    if sf.stat().st_mtime < cutoff:
                        stale.append(sf.parent.name)
                except OSError:
                    pass
        check("session: no stale session dirs", len(stale) == 0,
              f"stale: {', '.join(stale)}" if stale else "clean")

        # 4. Hooks — check .claude/hooks/ (installed) or src/hooks/ (dev repo)
        hooks_dirs = [project_path / ".claude" / "hooks", project_path / "src" / "hooks"]
        for hook_name in ["state-echo-hook", "task-gate-hook"]:
            found = False
            for hooks_dir in hooks_dirs:
                hook_path = hooks_dir / hook_name
                if hook_path.exists():
                    executable = os.access(hook_path, os.X_OK)
                    check(f"hooks: {hook_name}", executable,
                          f"found at {hooks_dir.name}/" + ("" if executable else " but not executable"))
                    found = True
                    break
            if not found:
                check(f"hooks: {hook_name}", False, "missing")

        # 4b. Check ~/.claude/settings.json for stale hook entries pointing to nonexistent paths
        user_settings = Path.home() / ".claude" / "settings.json"
        stale_hooks = []
        if user_settings.exists():
            import json as _json
            try:
                settings = _json.loads(user_settings.read_text(encoding="utf-8"))
                for cmd in iter_hook_commands(settings.get("hooks", {})):
                    for token in cmd.split():
                        p = Path(token)
                        if p.suffix in (".sh", "") and len(p.parts) > 2 and not p.exists():
                            stale_hooks.append(str(p))
            except (ValueError, KeyError):
                pass
        check("hooks: no stale entries in ~/.claude/settings.json",
              len(stale_hooks) == 0,
              f"stale paths: {', '.join(stale_hooks[:3])}" if stale_hooks else "clean")

        # 5. Plugin version
        from tasks.core import VERSION as code_version
        installed_version = None
        plugin_json_paths = list(Path.home().glob(".claude/plugins/**/playbook/.claude-plugin/plugin.json"))
        if plugin_json_paths:
            import json as _json2
            try:
                pdata = _json2.loads(plugin_json_paths[0].read_text(encoding="utf-8"))
                installed_version = pdata.get("version", "unknown")
            except (ValueError, OSError):
                installed_version = "unreadable"
        if installed_version:
            version_ok = installed_version == code_version
            check("plugin: version matches code", version_ok,
                  f"installed={installed_version}, code={code_version}" + ("" if version_ok else " — run /upgrade"))
        else:
            check("plugin: installed", False, "no plugin found")

        # 6. Python version
        import platform
        py_ver = platform.python_version()
        major, minor = sys.version_info[:2]
        check("python: version >= 3.8", major >= 3 and minor >= 8, py_ver)

        # 7. write_text encoding (check installed plugin scripts)
        import re as _re
        import inspect
        cli_src = Path(inspect.getfile(sys.modules[__name__]))
        core_src = cli_src.parent / "core.py"
        unencoded = 0
        for src_file in [cli_src, core_src]:
            if src_file.exists():
                content = src_file.read_text(encoding="utf-8")
                # Find all write_text/read_text calls (may span multiple lines)
                for m in _re.finditer(r'\.(write_text|read_text)\(', content):
                    # Find the matching closing paren
                    start = m.end()
                    depth = 1
                    pos = start
                    while pos < len(content) and depth > 0:
                        if content[pos] == '(':
                            depth += 1
                        elif content[pos] == ')':
                            depth -= 1
                        pos += 1
                    call_body = content[start:pos]
                    if "encoding=" not in call_body:
                        unencoded += 1
        check("encoding: write_text/read_text have encoding=", unencoded == 0,
              f"{unencoded} unencoded calls" if unencoded else "all encoded")

        # 8. Gate echo truncation
        has_truncation = False
        for hd in hooks_dirs:
            echo_hook = hd / "state-echo-hook"
            if echo_hook.exists():
                hook_content = echo_hook.read_text(encoding="utf-8")
                has_truncation = "cut -c" in hook_content or "GATE_TEXT_STORE" in hook_content
                break
        check("hooks: gate text truncation", has_truncation,
              "prevents recursive duplication" if has_truncation else "gate text may grow unbounded")

        # Summary
        total = passed + failed
        print(f"\n{passed}/{total} checks passed", end="")
        if failed:
            print(f" ({failed} failed)")
        else:
            print()

    elif cmd == "mindmap-sync":
        import re as _re
        project_path = find_project_root()
        main_file = project_path / "MIND_MAP.md"
        overflow_file = project_path / "MIND_MAP_OVERFLOW.md"

        if not main_file.exists():
            print("Error: MIND_MAP.md not found", file=sys.stderr)
            sys.exit(1)
        if not overflow_file.exists():
            print("Error: MIND_MAP_OVERFLOW.md not found", file=sys.stderr)
            sys.exit(1)

        fix_mode = "--fix" in cmd_args

        def _extract_nodes(filepath: Path) -> dict[int, str]:
            """Extract {node_id: full_text} from a mind map file."""
            content = filepath.read_text(encoding="utf-8")
            nodes: dict[int, str] = {}
            parts = _re.split(r'(?m)^(?=\[\d+\])', content)
            for part in parts:
                m = _re.match(r'^\[(\d+)\]', part)
                if m:
                    nodes[int(m.group(1))] = part.strip()
            return nodes

        main_nodes = _extract_nodes(main_file)
        overflow_nodes = _extract_nodes(overflow_file)

        # Size stats
        main_size = main_file.stat().st_size
        overflow_size = overflow_file.stat().st_size
        full_count = sum(1 for nid in main_nodes if '↗' not in main_nodes[nid])
        summary_count = len(main_nodes) - full_count
        print(f"MIND_MAP.md: {main_size:,} chars (~{main_size // 4:,} tokens), "
              f"{len(main_nodes)} nodes ({full_count} full, {summary_count} summary/↗)")
        print(f"MIND_MAP_OVERFLOW.md: {overflow_size:,} chars, {len(overflow_nodes)} nodes")
        print()

        # Missing nodes
        main_only = sorted(set(main_nodes) - set(overflow_nodes))
        overflow_only = sorted(set(overflow_nodes) - set(main_nodes))
        if main_only:
            print(f"Missing from overflow: {main_only}")
        if overflow_only:
            print(f"Missing from main: {overflow_only}")

        # Content drift (full nodes only — summary nodes are intentionally shorter)
        drifted_main_ahead: list[tuple[int, int]] = []
        drifted_overflow_ahead: list[tuple[int, int]] = []
        for nid in sorted(set(main_nodes) & set(overflow_nodes)):
            main_text = main_nodes[nid]
            overflow_text = overflow_nodes[nid]
            if '↗' not in main_text and main_text != overflow_text:
                diff = len(main_text) - len(overflow_text)
                if diff > 0:
                    drifted_main_ahead.append((nid, diff))
                else:
                    drifted_overflow_ahead.append((nid, -diff))

        if drifted_main_ahead or drifted_overflow_ahead:
            print("Content drift (full nodes only):")
            for nid, diff in drifted_main_ahead:
                print(f"  [{nid}] main AHEAD by {diff} chars")
            for nid, diff in drifted_overflow_ahead:
                print(f"  [{nid}] overflow AHEAD by {diff} chars")
        else:
            print("No content drift.")

        # Cross-reference health
        all_main_text = main_file.read_text(encoding="utf-8")
        all_refs = set(int(m.group(1)) for m in _re.finditer(r'\[(\d+)\]', all_main_text))
        broken = sorted(all_refs - set(main_nodes))
        if broken:
            print(f"\nBroken cross-references: {broken}")

        # --fix: copy main→overflow for nodes where main is ahead
        if fix_mode and (drifted_main_ahead or main_only):
            overflow_content = overflow_file.read_text(encoding="utf-8")
            fixed = 0
            for nid, _ in drifted_main_ahead:
                old_text = overflow_nodes[nid]
                new_text = main_nodes[nid]
                overflow_content = overflow_content.replace(old_text, new_text)
                fixed += 1
            for nid in main_only:
                overflow_content = overflow_content.rstrip() + "\n\n" + main_nodes[nid] + "\n"
                fixed += 1
            overflow_file.write_text(overflow_content, encoding="utf-8")
            print(f"\nFixed: synced {fixed} node(s) main→overflow")
        elif drifted_main_ahead or main_only:
            fixable = len(drifted_main_ahead) + len(main_only)
            print(f"\n{fixable} node(s) can be auto-synced main→overflow. Run: tasks mindmap-sync --fix")

    elif cmd == "log":
        import re
        project_path = find_project_root()
        chat_log = project_path / ".agent" / "chat_log.md"
        if not chat_log.exists():
            print("Error: .agent/chat_log.md not found", file=sys.stderr)
            sys.exit(1)
        text = chat_log.read_text(encoding="utf-8")
        blocks = text.split("\n---\n")
        for block in blocks:
            m = re.match(
                r'\*\*(\[M\d+\])\*\* \[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}):\d{2} UTC\] `(\w+)`\s*\n+(.*)',
                block.strip(), re.DOTALL
            )
            if m:
                mid, ts, role, body = m.groups()
                body = body.strip().replace("\n", " ")
                if len(body) > 500:
                    body = body[:497] + "..."
                print(f"{mid} {ts} {role:<6} {body}")

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
