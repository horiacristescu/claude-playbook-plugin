"""Task management operations for .agent/tasks/ directories."""
from __future__ import annotations

import functools
import os
import re
import subprocess
from pathlib import Path

VERSION = "1.3.4"

AGENT_PROCESS_NAMES = frozenset({"claude", "codex", "agy", "pi"})


@functools.lru_cache(maxsize=1)
def find_agent_root_pid() -> int | None:
    """Walk parent process tree, return PID of the highest agent ancestor.

    Identifies claude/codex/agy/pi processes by `comm` (basename, no args).
    Returns None if no agent found within 20 hops or if `ps` is unavailable.
    Used as fallback when PLAYBOOK_SESSION_ID env var isn't propagated —
    Python and bash both walk the same tree and converge on the same PID.
    Result is cached: process tree is stable for the lifetime of this process.
    """
    pid = os.getppid()
    last_agent_pid: int | None = None
    for _ in range(20):
        if pid in (0, 1):
            break
        try:
            r = subprocess.run(
                ["ps", "-p", str(pid), "-o", "ppid=,comm="],
                capture_output=True, text=True, timeout=1,
            )
        except (OSError, subprocess.TimeoutExpired):
            break
        if r.returncode != 0 or not r.stdout.strip():
            break
        parts = r.stdout.strip().split(None, 1)
        if len(parts) < 2:
            break
        try:
            ppid = int(parts[0])
        except ValueError:
            break
        comm = os.path.basename(parts[1].strip())
        if comm in AGENT_PROCESS_NAMES:
            last_agent_pid = pid
        if ppid == pid:
            break
        pid = ppid
    return last_agent_pid


def resolve_session_id() -> str:
    """Resolve session_id used to namespace .agent/sessions/<id>/.

    Order: PLAYBOOK_SESSION_ID env → ancestor scan (root agent PID) →
    immediate-parent PID. The ancestor scan is the robust path: it survives
    env-propagation failures (VSCode CLAUDE_ENV_FILE quirks, missing wrappers,
    subprocess loss). Bash hooks mirror this resolver in gate-echo-lib.sh.
    """
    sid = os.environ.get("PLAYBOOK_SESSION_ID", "")
    if sid:
        return sid
    agent_pid = find_agent_root_pid()
    if agent_pid is not None:
        return f"pid-{agent_pid}"
    return f"pid-{os.getppid()}"

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


def _validate_username(name: str) -> None:
    """Raise SystemExit if name is not a safe directory component."""
    if not name or name in (".", "..") or not _USERNAME_RE.match(name) or "/" in name:
        print(
            f"Error: .agent/current_user contains invalid username {name!r}.\n"
            "Must be non-empty, start with a letter or digit, and contain only "
            "letters, digits, hyphens, underscores, and dots (no spaces or slashes).",
            file=__import__("sys").stderr,
        )
        raise SystemExit(1)


def resolve_agent_dir(project_path: Path) -> Path:
    """Return the agent state root for this project.

    Multi-user mode: .agent/current_user exists → .agent/<username>/
    Legacy mode:     .agent/current_user absent  → .agent/  (unchanged)
    Invalid content: print error and exit(1).
    """
    marker = project_path / ".agent" / "current_user"
    if not marker.exists():
        return project_path / ".agent"
    name = marker.read_text(encoding="utf-8").strip()
    _validate_username(name)
    return project_path / ".agent" / name


# Task type → pattern name in playbook skill
PLAYBOOKS = {
    "feature": "Build",
    "build": "Build",
    "bugfix": "Fix",
    "refactor": "Build",
    "cleanup": "Fix",
    "ops": "Build",
    "audit": "Evaluate",
    "eval": "Evaluate",
    "research": "Investigate",
}



def _slugify(name: str) -> str:
    """Convert name to lowercase hyphen-separated slug."""
    slug = re.sub(r'[\s_]+', '-', name)
    slug = re.sub(r'[^a-zA-Z0-9-]', '', slug)
    slug = re.sub(r'-+', '-', slug)
    return slug.strip('-').lower()


def _display_title(name: str) -> str:
    """Render a task name for markdown headers."""
    return name.replace("-", " ").replace("_", " ").title()


def _next_task_number(tasks_dir: Path) -> int:
    """Find the next available task number."""
    if not tasks_dir.exists():
        return 1

    max_num = 0
    for item in tasks_dir.iterdir():
        if item.is_dir():
            match = re.match(r'^(\d+)-', item.name)
            if match:
                num = int(match.group(1))
                max_num = max(max_num, num)

    return max_num + 1



def _find_playbook_skill(project_path: Path | None = None) -> Path | None:
    """Find the playbook SKILL.md file.

    Resolution order:
    1. project_path/.claude/skills/playbook/SKILL.md  (project-local)
    2. ~/.claude/skills/playbook/SKILL.md              (home install)
    """
    if project_path:
        skill = project_path / ".claude" / "skills" / "playbook" / "SKILL.md"
        if skill.exists():
            return skill

    home_skill = Path.home() / ".claude" / "skills" / "playbook" / "SKILL.md"
    if home_skill.exists():
        return home_skill

    return None


def _load_playbook(task_type: str, project_path: Path | None = None) -> str | None:
    """Load a pattern template from the unified playbook skill.

    Extracts the ```markdown block under the matching ### Pattern heading.
    Returns the template text, or None if not found.
    """
    pattern_name = PLAYBOOKS.get(task_type)
    if not pattern_name:
        return None

    skill_path = _find_playbook_skill(project_path)
    if not skill_path:
        return None

    content = skill_path.read_text(encoding="utf-8")

    # Extract the ```markdown ... ``` block under ### <pattern_name>
    in_section = False
    in_code_block = False
    template_lines = []

    for line in content.splitlines():
        if line.strip() == f"### {pattern_name}":
            in_section = True
            continue
        if in_section:
            # Stop at next ### heading
            if line.startswith("### ") and not in_code_block:
                break
            if line.strip() == "```markdown":
                in_code_block = True
                continue
            if in_code_block:
                if line.strip() == "```":
                    break
                template_lines.append(line)

    return "\n".join(template_lines) if template_lines else None


def _find_custom_playbook(project_path: Path, task_type: str) -> Path | None:
    """Check if a custom playbook template exists in .agent/playbooks/."""
    playbook = resolve_agent_dir(project_path) / "playbooks" / f"{task_type}.md"
    return playbook if playbook.exists() else None


def list_all_types(project_path: Path) -> list[str]:
    """Return sorted list of all available task types (built-in + custom)."""
    types = set(PLAYBOOKS.keys()) | {"quick"}
    playbooks_dir = resolve_agent_dir(project_path) / "playbooks"
    if playbooks_dir.exists():
        for f in playbooks_dir.glob("*.md"):
            if f.name != "README.md":
                types.add(f.stem)
    return sorted(types)


def create_task(project_path: Path, name: str, task_type: str | None = None,
                intent_text: str | None = None, stub: bool = False) -> Path:
    """Create a new task with the given name.

    Args:
        project_path: Path to the project root
        name: Human-readable name for the task
        task_type: Task type (feature, bugfix, etc.) for playbook template.
            If a matching .agent/playbooks/<type>.md exists, uses that
            instead of the base Python template.
        intent_text: Optional intent paragraph to pre-fill ## Intent section.
        stub: If True, generate minimal stub (no gates) instead of full template.

    Returns:
        Path to the created task.md file
    """
    tasks_dir = resolve_agent_dir(project_path) / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    task_num = _next_task_number(tasks_dir)
    slug = _slugify(name)
    folder_name = f"{task_num:03d}-{slug}"

    task_dir = tasks_dir / folder_name
    task_dir.mkdir()

    # Check for custom playbook template first
    custom = _find_custom_playbook(project_path, task_type) if task_type else None

    if stub:
        # Stub mode: minimal template with no gates
        from tasks.template import render_stub_template
        content = render_stub_template(
            num=task_num, title=_display_title(name),
            intent_text=intent_text or "",
            task_type=task_type,
        )
    elif custom:
        content = custom.read_text(encoding="utf-8")
        content = content.replace("{{NNN}}", f"{task_num:03d}")
        content = content.replace("{{TITLE}}", _display_title(name))
    else:
        # Fall back to base Python template
        from tasks.template import render_template
        content = render_template(num=task_num, title=_display_title(name), task_type=task_type)

        # Append playbook template if task_type specified
        if task_type:
            role_template = _load_playbook(task_type, project_path)
            if role_template:
                content += "\n" + role_template + "\n"

    # Pre-fill Intent section if intent_text provided
    if intent_text and not stub:
        # Replace placeholder in all template variants
        for placeholder in [
            "(what we want to achieve \u2014 the outcome, not the activity)",
            "(one line \u2014 what to do and how to verify)",
        ]:
            if placeholder in content:
                content = content.replace(placeholder, intent_text)
                break

    task_file = task_dir / "task.md"
    task_file.write_text(content, encoding="utf-8")

    return task_file


def _extract_status(task_file: Path) -> str:
    """Extract status from task file (line after last ## Status)."""
    try:
        lines = task_file.read_text(encoding="utf-8").splitlines()
        status_idx = None
        for i, line in enumerate(lines):
            if line.strip() == "## Status":
                status_idx = i
        if status_idx is not None and status_idx + 1 < len(lines):
            return lines[status_idx + 1].strip()
        return "unknown"
    except Exception:
        return "error"


def _extract_problem(task_file: Path) -> str:
    """Extract first line of Problem/Intent section from task file."""
    try:
        lines = task_file.read_text(encoding="utf-8").splitlines()
        in_section = False
        for line in lines:
            if line.strip() in ("## Problem", "## Intent"):
                in_section = True
                continue
            if in_section:
                if not line.strip():
                    continue
                if line.startswith("##"):
                    break
                text = line.strip()
                if text.startswith("(") and text.endswith(")"):
                    text = text[1:-1]
                return text
        return ""
    except Exception:
        return ""


def _extract_head_position(task_file: Path) -> str:
    """Find the first unchecked checkbox or empty required field."""
    try:
        lines = task_file.read_text(encoding="utf-8").splitlines()
        for line in lines:
            stripped = line.strip()
            # Unchecked checkbox
            if stripped.startswith("- [ ]"):
                return stripped[6:].strip()  # text after "- [ ] "
            # Empty required field (line ending with : and nothing after)
            if stripped.endswith(":") and stripped.startswith("- **"):
                return stripped
        return "(all gates checked)"
    except Exception:
        return "(error reading)"


def _is_done(task_file: Path) -> bool:
    """Check if a task's status starts with 'done'."""
    return _extract_status(task_file).startswith("done")


def _find_active_task(project_path: Path, name_filter: str = "") -> Path | None:
    """Find the active task: earliest non-done task with unchecked gates.

    If name_filter is given, only match tasks whose folder name contains it.
    """
    tasks_dir = resolve_agent_dir(project_path) / "tasks"
    if not tasks_dir.exists():
        return None
    for task_file in sorted(tasks_dir.glob("*/task.md")):
        if name_filter and name_filter not in task_file.parent.name:
            continue
        if _is_done(task_file):
            continue
        head = _extract_head_position(task_file)
        if not head.startswith("("):
            return task_file
    return None


def task_done(project_path: Path, name_filter: str = "") -> dict:
    """Check off the current gate and return checked + next gate info.

    Returns dict with keys: task_name, checked, next, task_file.
    On error, returns dict with 'error' key.
    """
    task_file = None

    agent_dir = resolve_agent_dir(project_path)
    session_id = resolve_session_id()
    state_files = [agent_dir / "sessions" / session_id / "current_state"]

    for state_file in state_files:
        if not state_file.exists():
            continue
        task_num = state_file.read_text(encoding="utf-8").strip()
        if not task_num:
            continue
        matches = sorted((agent_dir / "tasks").glob(f"{task_num}-*/task.md"))
        if not matches:
            continue
        candidate = matches[0]
        if name_filter and name_filter not in candidate.parent.name:
            continue
        if _is_done(candidate):
            continue
        head = _extract_head_position(candidate)
        if not head.startswith("("):
            task_file = candidate
            break

    if task_file is None:
        task_file = _find_active_task(project_path, name_filter)
    if not task_file:
        return {"error": "No active task with open gates"}

    task_name = task_file.parent.name
    lines = task_file.read_text(encoding="utf-8").splitlines()

    # Find and check off the first unchecked gate
    checked_text = None
    checked_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("- [ ]"):
            checked_text = stripped[6:].strip()
            # Preserve original indentation, just flip the checkbox
            lines[i] = line.replace("- [ ]", "- [x]", 1)
            checked_idx = i
            break

    if checked_text is None:
        return {"error": f"No unchecked gate in {task_name}"}

    # Write back
    task_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Collect next gates (up to 3) after the one we just checked
    upcoming = []
    for line in lines[checked_idx + 1:]:
        stripped = line.strip()
        if stripped.startswith("- [ ]"):
            upcoming.append(stripped[6:].strip())
        elif stripped.endswith(":") and stripped.startswith("- **"):
            upcoming.append(stripped)
        else:
            continue
        if len(upcoming) >= 3:
            break

    return {
        "task_name": task_name,
        "checked": checked_text,
        "upcoming": upcoming,
        "task_file": task_file,
    }


def _extract_progress(task_file: Path) -> str:
    """Count checked/total checkboxes in a task file."""
    try:
        content = task_file.read_text(encoding="utf-8")
        checked = content.count("- [x]") + content.count("- [X]")
        total = checked + content.count("- [ ]")
        return f"{checked}/{total}" if total > 0 else "-"
    except Exception:
        return "-"


def list_tasks(project_path: Path, pending_only: bool = False) -> None:
    """List all tasks with their status and intent."""
    tasks_dir = resolve_agent_dir(project_path) / "tasks"

    if not tasks_dir.exists():
        print("No .agent/tasks/ directory found")
        return

    task_files = sorted(tasks_dir.glob("*/task.md"))

    if not task_files:
        print("No tasks found")
        return

    status_w = 7
    progress_w = 8
    intent_w = 500

    # Collect rows first to compute dynamic name column width
    rows = []
    counts = {"done": 0, "pending": 0, "other": 0}

    for task_file in task_files:
        name = task_file.parent.name
        status = _extract_status(task_file)
        status_key = status.split()[0] if status else "unknown"

        if status_key in ("done", "pending"):
            counts[status_key] += 1
        else:
            counts["other"] += 1

        if pending_only and status_key == "done":
            continue

        intent = _extract_problem(task_file)
        progress = _extract_progress(task_file)

        if len(intent) > intent_w:
            intent = intent[:intent_w-1] + "…"
        if len(status) > status_w:
            status = status[:status_w]

        rows.append((name, status, progress, intent))

    name_w = max((len(r[0]) for r in rows), default=4)
    name_w = max(name_w, 4)  # at least wide enough for "Name"

    print(f"{'Name':<{name_w}} | {'Status':<{status_w}} | {'Progress':<{progress_w}} | Intent")
    print(f"{'-'*name_w}-+-{'-'*status_w}-+-{'-'*progress_w}-+-{'-'*intent_w}")

    for name, status, progress, intent in rows:
        print(f"{name:<{name_w}} | {status:<{status_w}} | {progress:<{progress_w}} | {intent}")

    print("")
    parts = []
    if counts["done"]:
        parts.append(f"{counts['done']} done")
    if counts["pending"]:
        parts.append(f"{counts['pending']} pending")
    if counts["other"]:
        parts.append(f"{counts['other']} other")
    summary = f"Summary: {', '.join(parts)}"
    if pending_only:
        summary += f" (showing {len(rows)} open)"
    print(summary)
    print("Task files: .agent/tasks/<name>/task.md — activate with: tasks work <number>")


def task_status(project_path: Path) -> None:
    """Show head position (first unchecked gate) for each active task."""
    tasks_dir = resolve_agent_dir(project_path) / "tasks"

    if not tasks_dir.exists():
        print("No .agent/tasks/ directory found")
        return

    task_files = sorted(tasks_dir.glob("*/task.md"))

    if not task_files:
        print("No tasks found")
        return

    for task_file in task_files:
        name = task_file.parent.name
        status = _extract_status(task_file)

        if status == "done":
            continue

        head = _extract_head_position(task_file)
        progress = _extract_progress(task_file)

        print(f"{name:<40} | {progress:<8} | {head}")


# merge-doctor — mechanical contamination check for cross-namespace merges
# --------------------------------------------------------------------------

# Lines under this length are too noisy (empty, "ok", single punctuation) to
# treat as evidence of contamination by themselves.
_MERGE_DOCTOR_LINE_FLOOR = 4
# Flag a per-user file when the *cumulative* non-whitespace bytes of foreign
# lines clear this threshold — catches one long foreign line OR many short
# ones (chat-log timestamps, M-tags, "tasks done" markers).
_MERGE_DOCTOR_FOREIGN_BYTES_MIN = 20
# (conflict-marker detection lives in _md_has_conflict_markers / _CONFLICT_MARKER_RE —
# line-start angle markers only; a substring tuple would re-introduce the
# `=======`/prose false positives.)


def _md_git(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
    # errors="replace": git blobs may be non-UTF-8 (e.g. Windows cp1252 task.md);
    # strict decoding would raise UnicodeDecodeError and abort the whole audit.
    proc = subprocess.run(
        ["git", *cmd],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _md_git_show(ref: str, path: str, cwd: Path) -> str | None:
    rc, out, _ = _md_git(["show", f"{ref}:{path}"], cwd)
    return out if rc == 0 else None


def _md_user_dirs(ref: str, cwd: Path) -> set[str]:
    """Names of .agent/<user>/ tree entries on <ref>."""
    rc, out, _ = _md_git(
        ["ls-tree", "-d", "--name-only", ref, ".agent/"], cwd
    )
    if rc != 0:
        return set()
    users: set[str] = set()
    for line in out.splitlines():
        line = line.strip().rstrip("/")
        if not line:
            continue
        # entries look like ".agent/userA"
        parts = line.split("/")
        if len(parts) == 2 and parts[0] == ".agent" and parts[1]:
            users.add(parts[1])
    return users


def _md_unmerged_paths(cwd: Path) -> set[str]:
    """Paths git considers currently unmerged (active merge conflicts).

    Parses `git ls-files --unmerged` output where each row is
    `<mode> <hash> <stage>\\t<path>`. Splits on `\\t` to handle paths
    containing spaces. Returns empty set if no merge is in progress.
    """
    rc, out, _ = _md_git(["ls-files", "--unmerged"], cwd)
    if rc != 0:
        return set()
    paths: set[str] = set()
    for line in out.splitlines():
        if "\t" not in line:
            continue
        path = line.split("\t", 1)[1].strip()
        if path:
            paths.add(path)
    return paths


def _md_tracked(path: str, cwd: Path) -> bool:
    """True iff <path> is currently tracked in the git index."""
    rc, out, _ = _md_git(["ls-files", "--", path], cwd)
    return rc == 0 and bool(out.strip())


def _md_ignored(path: str, cwd: Path) -> bool:
    """True iff <path> is covered by .gitignore.

    `git check-ignore <path>` exits 0 when the path matches an ignore rule,
    1 when it does not, and 128 on errors (bad path, not a git repo). Collapse
    only exit 0 to True so transient errors don't mask findings.
    """
    rc, _, _ = _md_git(["check-ignore", "-q", path], cwd)
    return rc == 0


def _md_nontrivial(text: str) -> set[str]:
    """Return the set of stripped lines >= LINE_FLOOR chars long.

    The line floor screens out pure-noise lines (empty, single chars). The
    real contamination threshold is checked per-comparison against
    FOREIGN_BYTES_MIN on the *sum* of foreign-line lengths, so a single short
    "tasks done" appearing on the wrong branch can still be detected if other
    short foreign lines accompany it.
    """
    lines: set[str] = set()
    for raw in text.splitlines():
        stripped = raw.strip()
        if len(stripped) >= _MERGE_DOCTOR_LINE_FLOOR:
            lines.add(stripped)
    return lines


_CONFLICT_MARKER_RE = re.compile(r'(?m)^(<{7}|>{7})')


def _md_has_conflict_markers(text: str) -> bool:
    """True iff text has a git conflict marker at LINE-START (``<<<<<<<`` or
    ``>>>>>>>``, 7 chars). A bare ``=======`` line is NOT treated as a marker —
    it is valid markdown (setext H1 underline / horizontal rule). Matching at
    line-start (not substring) avoids flagging prose like ``grep '<<<<<<'``."""
    return bool(_CONFLICT_MARKER_RE.search(text))


def _md_marker_lines(text: str) -> set[str]:
    """Conflict-marker lines (``<<<<<<<…`` / ``>>>>>>>…``) at line-start."""
    return {ln for ln in text.splitlines() if _CONFLICT_MARKER_RE.match(ln)}


def _md_new_marker_lines(rel: str, cur_text: str, parents: list[str],
                         cwd: Path) -> bool:
    """True iff `rel` has a conflict-marker line NOT present in any parent's
    version of the file. Git's synthesized conflict markers are in neither
    parent, so a NEW marker line is a real stranded marker; a marker line that
    already exists in a parent is pre-existing documentation (e.g. a task.md
    showing an example conflict, which a merge may merely append to) — not a
    false positive to gate on."""
    cur = _md_marker_lines(cur_text)
    if not cur:
        return False
    parent_markers: set[str] = set()
    for p in parents:
        pt = _md_git_show(p, rel, cwd)
        if pt is not None:
            parent_markers |= _md_marker_lines(pt)
    return bool(cur - parent_markers)


def run_merge_doctor(project_path: Path, source: str, target: str) -> int:
    """Audit a merge for per-user cross-contamination and stranded markers.

    Inspection contract:
      - Working tree if a merge is in progress (.git/MERGE_HEAD present).
      - Else the most recent merge commit reachable from HEAD.
      - Neither → print "no merge state detected" and return 0.

    Findings are classified into three buckets:
      - actionable: real problems (contamination, tracked legacy paths,
        stranded conflict markers). Counted toward exit code.
      - expected: mid-merge surface that Step 5 of the skill will resolve
        (conflict markers in files git lists as --unmerged).
      - informational: pre-existing untracked files outside any user
        namespace that aren't gitignored. Printed but not counted.

    A fourth tier — untracked + gitignored — is suppressed entirely so the
    user doesn't see the disk noise (.DS_Store, bash_history) they
    explicitly named as annoying.

    Returns len(actionable). Callers map >0 → exit code 1.
    """
    merge_head = project_path / ".git" / "MERGE_HEAD"
    merge_commit = None
    if merge_head.exists():
        mid_merge = True
        state = "mid-merge (working tree)"
    else:
        rc, out, _ = _md_git(
            ["log", "--merges", "-n", "1", "--pretty=%H"], project_path
        )
        if rc == 0 and out.strip():
            mid_merge = False
            merge_commit = out.strip()
            state = f"post-merge (commit {merge_commit[:8]})"
        else:
            print("no merge state detected")
            return 0

    print(f"merge-doctor: inspecting {state}")
    print(f"  source ref: {source}")
    print(f"  target ref: {target}")
    print()

    actionable: list[str] = []
    expected: list[str] = []
    informational: list[str] = []

    # Paths git considers actively unmerged — only populated mid-merge.
    # In post-merge mode this is empty by construction, which collapses
    # the [EXPECTED] bucket so any surviving marker reports as actionable.
    unmerged = _md_unmerged_paths(project_path) if mid_merge else set()

    # The two merge parents. A conflict marker git writes is in NEITHER parent,
    # so a marker line that already exists in a parent is pre-existing
    # documentation (e.g. a task.md showing an example conflict), not a stranded
    # merge marker — see `_md_markers_from_this_merge`. This classifies markers
    # by content novelty, not just whether the path was touched, so a
    # merge-modified doc with pre-existing example markers isn't a false positive.
    if mid_merge:
        merge_parents = ["HEAD", "MERGE_HEAD"]
    else:
        merge_parents = [f"{merge_commit}^1", f"{merge_commit}^2"]

    # 1. User detection (union of both sides)
    src_users = _md_user_dirs(source, project_path)
    tgt_users = _md_user_dirs(target, project_path)
    all_users = src_users | tgt_users
    print(f"detected user namespaces: {sorted(all_users) or '(none)'}")
    if src_users != tgt_users:
        if src_users - tgt_users:
            print(f"  source-only: {sorted(src_users - tgt_users)}")
        if tgt_users - src_users:
            print(f"  target-only: {sorted(tgt_users - src_users)}")

    # current_user marker cross-check — configuration error, always actionable
    for ref, label in [(source, "source"), (target, "target")]:
        marker = _md_git_show(ref, ".agent/current_user", project_path)
        if marker is not None:
            name = marker.strip()
            if name and name not in all_users:
                actionable.append(
                    f"current_user marker on {label} '{ref}' is '{name}' "
                    f"but no .agent/{name}/ directory exists on either side"
                )
    print()

    # 2. Per-user cross-contamination + per-user marker scan
    user_to_refs: dict[str, list[str]] = {}
    for u in src_users:
        user_to_refs.setdefault(u, []).append(source)
    for u in tgt_users:
        user_to_refs.setdefault(u, []).append(target)

    reported_markers: set[str] = set()  # files whose conflict-marker finding
                                        # is already classified; the global
                                        # stranded scan skips these.
    for user in sorted(all_users):
        user_dir = project_path / ".agent" / user
        if not user_dir.exists():
            continue
        for f in sorted(user_dir.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(project_path).as_posix()
            # Skip untracked files: git can't write a merge result to them and
            # they can't enter the commit, so they can't carry contamination or
            # stranded markers INTO the merge (the field-test FP: an untracked
            # chat_log.md flagged as contamination).
            if not _md_tracked(rel, project_path):
                continue
            try:
                # errors="replace" so a non-UTF-8 (e.g. cp1252) working-tree file
                # is still scanned for contamination/markers, not silently skipped.
                wt_text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            # First: per-user contamination check (always actionable when it
            # fires — runs BEFORE marker classification so contamination on
            # an unmerged file still wins over the "expected marker" bucket).
            wt_lines = _md_nontrivial(wt_text)
            contam_found = False
            if wt_lines:
                self_lines: set[str] = set()
                for ref in user_to_refs.get(user, []):
                    content = _md_git_show(ref, rel, project_path)
                    if content is not None:
                        self_lines |= _md_nontrivial(content)

                parts = rel.split("/", 2)
                rest = parts[2] if len(parts) >= 3 else None
                if rest:
                    for other in all_users - {user}:
                        if contam_found:
                            break
                        for other_ref in user_to_refs.get(other, []):
                            other_rel = f".agent/{other}/{rest}"
                            other_content = _md_git_show(other_ref, other_rel, project_path)
                            if other_content is None:
                                continue
                            other_lines = _md_nontrivial(other_content)
                            foreign = (other_lines & wt_lines) - self_lines
                            foreign_bytes = sum(len(line) for line in foreign)
                            if foreign and foreign_bytes >= _MERGE_DOCTOR_FOREIGN_BYTES_MIN:
                                sample = next(iter(foreign))
                                snippet = sample if len(sample) <= 80 else sample[:77] + "..."
                                actionable.append(
                                    f"contamination: {rel} contains {len(foreign)} line(s) "
                                    f"({foreign_bytes} bytes) from {other_ref}:{other_rel} "
                                    f"— sample: {snippet}"
                                )
                                contam_found = True
                                reported_markers.add(rel)
                                break

            # Then: marker classification. Skip if contamination already
            # claimed the file (it's been added to reported_markers).
            if not contam_found and _md_has_conflict_markers(wt_text):
                if mid_merge and rel in unmerged:
                    expected.append(f"conflict markers in {rel} (active merge surface)")
                elif _md_new_marker_lines(rel, wt_text, merge_parents, project_path):
                    actionable.append(f"stranded conflict markers in {rel}")
                else:
                    informational.append(
                        f"conflict-marker line(s) in {rel} (pre-existing in a parent — likely documentation)")
                reported_markers.add(rel)

    # 3. Global stranded marker scan, deduped against per-user findings.
    # Match git conflict markers at LINE-START only (angle markers, 7 chars) —
    # NOT a bare ======= (markdown) nor a substring in prose. A marker line that
    # already exists in a parent is pre-existing documentation → informational.
    rc, out, _ = _md_git(
        ["grep", "-l", "-E", r"^(<<<<<<<|>>>>>>>)"], project_path
    )
    if rc == 0:
        for line in out.splitlines():
            line = line.strip()
            if not line or line in reported_markers:
                continue
            try:
                cur_text = (project_path / line).read_text(encoding="utf-8", errors="replace")
            except OSError:
                cur_text = ""
            if mid_merge and line in unmerged:
                expected.append(f"conflict markers in {line} (active merge surface)")
            elif _md_new_marker_lines(line, cur_text, merge_parents, project_path):
                actionable.append(f"stranded conflict markers in {line}")
            else:
                informational.append(
                    f"conflict-marker line(s) in {line} (pre-existing in a parent — likely documentation)")

    # 4. Legacy paths under .agent/ — three-way classify (suppress quiet noise)
    # Recursive scan: real installs accumulate detritus deeper than the top
    # level (.agent/cache/x, .agent/legacy/y). The `all_users` guard skips
    # anything inside a per-user namespace — those files were already
    # classified by the contamination scan above.
    # `.agent/current_user` flows through standard classification: tracked →
    # actionable (the install-day bug Step 6 of the skill fixes), ignored →
    # suppressed, else → informational.
    agent_dir = project_path / ".agent"
    if agent_dir.exists():
        for f in sorted(agent_dir.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(project_path).as_posix()
            # Skip per-user-namespace files (handled by contamination scan).
            # rel looks like ".agent/<first>/..." for nested paths.
            parts = rel.split("/", 2)
            if len(parts) >= 2 and parts[1] in all_users:
                continue
            if _md_tracked(rel, project_path):
                actionable.append(
                    f"legacy shared path tracked in git: {rel} "
                    f"(needs `git rm --cached {rel}`)"
                )
            elif _md_ignored(rel, project_path):
                # untracked AND gitignored: guaranteed never to enter a
                # commit. Suppress entirely — this is the .DS_Store /
                # bash_history noise the user explicitly named.
                continue
            else:
                informational.append(
                    f"untracked path outside any user namespace: {rel} "
                    f"(could end up in a commit if `git add` is blind)"
                )

    # Print buckets in priority order
    if actionable:
        print("[ACTIONABLE] — fix before continuing:")
        for item in actionable:
            print(f"  {item}")
        print()
    if expected:
        print("[EXPECTED] — mid-merge surface, Step 5 will resolve:")
        for item in expected:
            print(f"  {item}")
        print()
    if informational:
        print("[INFORMATIONAL] — note, do not block:")
        for item in informational:
            print(f"  {item}")
        print()
    if not (actionable or expected or informational):
        print("(no findings)")
        print()

    # Summary
    verdict = "NEEDS ATTENTION" if actionable else "SAFE TO CONTINUE"
    print(
        f"merge-doctor: {len(actionable)} actionable, "
        f"{len(expected)} expected, "
        f"{len(informational)} informational — {verdict}"
    )
    return len(actionable)
