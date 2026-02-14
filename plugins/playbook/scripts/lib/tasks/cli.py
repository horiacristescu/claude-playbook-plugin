"""CLI entry point for standalone tasks management."""
from __future__ import annotations

import sys
from pathlib import Path
from tasks.core import create_task, list_tasks, task_status, PLAYBOOKS, _find_playbook_skill


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
    print("Usage: tasks <command> [args]")
    print("")
    print("Commands:")
    print("  init                Create CLAUDE.md for this project")
    print("  bootstrap           Load mind map + skills + pending tasks")
    print("  work <number>       Set active task (e.g. tasks work 058)")
    print("  new <type> <name>   Create task with playbook template")
    print("  list [--pending]    List all tasks with status")
    print("  status              Show head position for active tasks")
    print("")
    print("Task types:", ", ".join(sorted(PLAYBOOKS.keys())))
    print("")
    print("Examples:")
    print("  tasks work 058")
    print("  tasks new feature add-auth")
    print("  tasks list --pending")


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
            state_file = project_path / ".agent" / "current_state"
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

        # Write task number to current_state
        state_file = project_path / ".agent" / "current_state"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(f"{task_num}\n")

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
        playbook_path = _find_playbook_skill(project_path)
        print(f"Playbook: {playbook_path or '(not found)'}")
        print()
        print("Next: fill in task.md gates, then ask user to run: tasks work " + task_num)
        print("Tip: Use `/playbook` skill for workflow patterns when writing gates.")

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
            claude_md.write_text(f"""# {title}

## Start Here

```bash
tasks bootstrap          # loads mind map, skills, pending tasks
```

Then **ask the user** what they want to work on. Don't autonomously pick a task.

## CLI

```bash
tasks work <number>              # activate task, hook starts tracking
tasks work done                  # deactivate when finished
tasks new <type> <name>          # create task — does NOT activate
tasks list [--pending]           # task overview
tasks status                     # current gate position
tasks bootstrap                  # orientation: mind map + skills + pending
```

## Don't

- Create task directories manually — always `tasks new`
- Edit `.agent/current_state` — use `tasks work <N>` / `tasks work done`
- Edit `## Status` in task.md directly — use `tasks work done`
- Skip task.md checkboxes — they're your observable progress
- Start coding without an active task — blocked by hook until `tasks work <N>`
""")
            print("  CLAUDE.md      created")
        else:
            print("  CLAUDE.md      exists")

    elif cmd == "bootstrap":
        project_path = find_project_root()
        home = Path.home()

        # Workflow briefing — the rules agents need to work correctly
        print("=== WORKFLOW ===")
        print("- Task numbers are zero-padded: 001, 012, 020 (not 1, 12, 20)")
        print("- Always `tasks work <N>` before editing task.md — hooks enforce this")
        print("- Never edit `## Status` directly — use `tasks work done`")
        print("- One gate at a time: read gate → do work → check box → next gate")
        print("- Pattern templates in task.md ARE the work plan — fill them in, don't skip")
        print()
        print("Tasks CLI:")
        print("  tasks work <N>           activate task (start here)")
        print("  tasks work done          mark done + deactivate")
        print("  tasks new <type> <name>  create task (doesn't activate)")
        print("  tasks list [--pending]   show tasks")
        print("  tasks status             current gate position")
        print()

        # Mind Map - institutional memory for agents
        mind_map = project_path / "MIND_MAP.md"
        if mind_map.exists():
            content = mind_map.read_text()
            max_chars = 25000
            print("=== MIND MAP (Your Institutional Memory) ===")
            print("Read to understand project context. Update when things change.")
            print()
            if len(content) <= max_chars:
                print(content.rstrip())
            else:
                print(content[:max_chars].rstrip())
                truncated = len(content) - max_chars
                print(f"\n... ({truncated} chars truncated)")
            print()

        # Pending tasks
        print("=== PENDING TASKS ===")
        list_tasks(project_path, pending_only=True)

        # Next steps
        print()
        print("IMPORTANT: Don't autonomously start tasks. Ask the user what to work on.")
        print("  User tells you: tasks work <N> | tasks new <type> <name> | or just chat")

    elif cmd in ("list", "ls"):
        project_path = find_project_root()
        pending_only = "--pending" in cmd_args
        list_tasks(project_path, pending_only=pending_only)

    elif cmd == "status":
        project_path = find_project_root()
        task_status(project_path)

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
