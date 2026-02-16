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
    """Load MIND_MAP.md content, truncated to max_chars. Returns None if missing."""
    mind_map = project_path / "MIND_MAP.md"
    if not mind_map.exists():
        return None
    content = mind_map.read_text()
    if len(content) > max_chars:
        content = content[:max_chars] + "\n... (truncated)"
    return content


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
        project_path = find_project_root()

        # Handle 'tasks work done' - deactivate current task and set Status in task.md
        if task_num == "done":
            state_file = _state_file(project_path)
            if state_file.exists():
                prev_task = state_file.read_text().strip()
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
                state_file.unlink()
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

        # Write task number to current_state (per-session if PLAYBOOK_SESSION_ID set)
        state_file = _state_file(project_path)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(f"{task_num}\n")

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
            print("Usage: tasks judge <number>", file=sys.stderr)
            sys.exit(1)

        import shutil
        import subprocess

        task_num = cmd_args[0]
        project_path = find_project_root()
        tasks_dir = project_path / ".agent" / "tasks"
        matches = list(tasks_dir.glob(f"{task_num}-*/task.md"))
        if not matches:
            print(f"Task {task_num} not found", file=sys.stderr)
            sys.exit(1)

        task_file = matches[0]
        task_path = str(task_file.relative_to(project_path))

        claude_bin = shutil.which("claude")
        if not claude_bin:
            print("Error: 'claude' not found on PATH", file=sys.stderr)
            sys.exit(1)

        from tasks.template import judge_prompt
        prompt = judge_prompt(task_path)

        # Build system context: mind map + task content
        context_parts = []
        mm_content = _load_mind_map(project_path)
        if mm_content:
            context_parts.append(f"=== MIND_MAP.md ===\n{mm_content}")
        context_parts.append(f"=== {task_path} ===\n{task_file.read_text()}")
        system_context = "\n\n".join(context_parts)

        env = os.environ.copy()
        env["CLAUDECODE"] = ""
        env["PLAYBOOK_SESSION_ID"] = "judge"

        cmd_list = [
            claude_bin, "-p",
            "--dangerously-skip-permissions",
            "--max-budget-usd", "2",
            "--append-system-prompt", system_context,
            prompt,
        ]

        print(f"Running blind judge on {task_path}...", flush=True)
        result = subprocess.run(
            cmd_list,
            cwd=str(project_path),
            env=env,
            capture_output=True,
            text=True,
        )
        if result.stdout:
            print(result.stdout, end="", flush=True)
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr, flush=True)

        # Save judge output
        judge_log = task_file.parent / "judge.log"
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
