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
                # Remove BOTH state files
                if legacy_state.exists():
                    legacy_state.unlink()
                if session_state and session_state.exists():
                    session_state.unlink()
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
                from tasks.core import _is_done, _extract_head_position
                tf = matches[0]
                done = _is_done(tf)
                head = _extract_head_position(tf)
                has_open = not head.startswith("(")
                if done and has_open:
                    print(f"Task {task_num} is done but has open gates. Set Status to 'pending' to reopen.", file=sys.stderr)
                elif done:
                    print(f"Task {task_num} is done (all gates complete).", file=sys.stderr)
                else:
                    print(f"Task {task_num} has no open gates.", file=sys.stderr)
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

    elif cmd == "status":
        project_path = find_project_root()
        task_status(project_path)

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
