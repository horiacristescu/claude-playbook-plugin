"""CLI entry point for standalone tasks management."""
from __future__ import annotations

import os
import re
import shutil
import sys
import time
from pathlib import Path
from tasks.core import create_task, list_tasks, task_status, PLAYBOOKS, _find_playbook_skill, resolve_session_id, resolve_agent_dir, run_merge_doctor


def _state_file(project_path: Path) -> Path:
    """Return per-session state file under .agent/sessions/<id>/current_state."""
    session_id = resolve_session_id()
    state_dir = resolve_agent_dir(project_path) / "sessions" / session_id
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

    chat_log = resolve_agent_dir(project_path) / "chat_log.md"
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
        agent = p / ".agent"
        if (agent / "tasks").exists():
            return p
        # Multi-user layout: .agent/<user>/tasks/
        if agent.is_dir():
            for sub in agent.iterdir():
                if sub.is_dir() and (sub / "tasks").exists():
                    return p

    # Fall back to cwd (create_task will make .agent/tasks/)
    return cwd


def _gc_dead_sessions(project_path: Path) -> None:
    """Remove stale session dirs and legacy flat files.

    Called at every tasks invocation. Cheap: O(N sessions × 1 stat).

    Session dirs older than 24h (by current_state mtime) are removed.
    Legacy flat files (.hook_counters.*, current_state*) in .agent/ root
    are always removed — they're pre-migration artifacts.
    """
    agent_dir = resolve_agent_dir(project_path)
    sessions_dir = agent_dir / "sessions"

    # Clean legacy flat files from pre-migration layout
    for pattern in (".hook_counters.*", "current_state", "current_state.*"):
        for f in agent_dir.glob(pattern):
            if f.is_file():
                try:
                    f.unlink()
                except OSError:
                    pass

    # Clean stale session dirs
    if not sessions_dir.exists():
        return
    cutoff = time.time() - 86400
    own_session = os.environ.get("PLAYBOOK_SESSION_ID", "")
    for session_dir in sessions_dir.iterdir():
        if not session_dir.is_dir():
            continue
        # Never remove our own session
        if own_session and session_dir.name == own_session:
            continue
        name = session_dir.name
        # PID-based sessions: instant GC via kill -0 liveness check
        if name.startswith("pid-"):
            try:
                pid = int(name[4:])
                os.kill(pid, 0)  # raises OSError if process is dead
                continue  # still alive — keep
            except (ValueError, OSError):
                pass  # dead or invalid — remove
        else:
            # Non-PID sessions (legacy UUIDs, "default"): 24h mtime fallback
            state_file = session_dir / "current_state"
            try:
                if state_file.exists() and state_file.stat().st_mtime >= cutoff:
                    continue  # fresh — keep
            except OSError:
                pass
        shutil.rmtree(session_dir, ignore_errors=True)


def _panel_triage_frame() -> list[str]:
    """Return the lines to append to a panel-review judge.md so the reading
    agent meets the triage discipline alongside the findings.

    Same wording for plan and impl modes (the panel-review assembly is shared);
    mirrors the per-task pushback gate from `template.judge_section()` /
    `template.judge_impl_section()` but lives in the file the agent actually
    reads after the panel runs.
    """
    bar = "═" * 60
    return [
        bar,
        "## Triage",  # No indent — must match `^## ` line-start parsers (impl-review F4).
        bar + "\n",
        (
            "These findings are opinion, not gospel. Before applying any of "
            "them, decide per-finding: real correctness issue, speculative "
            "concern, or wrong call. Document accept (with rationale) / park "
            "(with rationale) / reject (with rationale). Verify file:line "
            "claims before applying — panel judges sometimes cite wrong "
            "locations. The panel doesn't live with the outcomes — you do. "
            "Push back where you have concrete evidence the panel doesn't."
        ),
        "",
    ]


def _cmd_prepare_merge(project_path: Path, target: str, dry_run: bool) -> None:
    """Prepare current branch's Playbook state to merge cleanly into target."""
    import subprocess
    import re as _re

    agent_dir = resolve_agent_dir(project_path)

    # --- Shared: merge base ---
    try:
        merge_base = subprocess.check_output(
            ["git", "-C", str(project_path), "merge-base", "HEAD", target],
            text=True,
        ).strip()
    except subprocess.CalledProcessError:
        print(f"Error: could not compute merge base with '{target}'. Is '{target}' a valid branch?",
              file=sys.stderr)
        sys.exit(1)

    # --- Step 1: Task renumbering (placeholder) ---
    _prepare_merge_tasks(project_path, agent_dir, target, merge_base, dry_run)

    # --- Step 2: Chat log re-sequencing (placeholder) ---
    _prepare_merge_chatlog(project_path, agent_dir, target, merge_base, dry_run)

    # --- Step 3: MIND_MAP collision report (placeholder) ---
    _prepare_merge_mindmap(project_path, target, merge_base)

    if dry_run:
        print("(dry-run — no files written)")


def _git_ls_tasks(project_path: Path, ref: str, agent_dir: Path) -> dict[int, str]:
    """Return {task_number: dir_name} for tasks present at git ref. Empty dict if path absent."""
    import subprocess
    import re
    agent_dir_rel = str(agent_dir.relative_to(project_path))
    tasks_path = agent_dir_rel + "/tasks/"
    try:
        out = subprocess.check_output(
            ["git", "-C", str(project_path), "ls-tree", "--name-only", ref, tasks_path],
            text=True, stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return {}
    result: dict[int, str] = {}
    for entry in out.splitlines():
        name = entry.rstrip("/").split("/")[-1]
        m = re.match(r"^(\d+)-", name)
        if m:
            result[int(m.group(1))] = name
    return result


def _prepare_merge_tasks(project_path: Path, agent_dir: Path, target: str,
                         merge_base: str, dry_run: bool) -> None:
    import re

    base_tasks = _git_ls_tasks(project_path, merge_base, agent_dir)
    target_tasks = _git_ls_tasks(project_path, target, agent_dir)

    # Current tasks: scan working tree directly
    tasks_dir = agent_dir / "tasks"
    current_tasks: dict[int, str] = {}
    if tasks_dir.exists():
        for d in tasks_dir.iterdir():
            if d.is_dir():
                m = re.match(r"^(\d+)-", d.name)
                if m:
                    current_tasks[int(m.group(1))] = d.name

    new_on_current = {n: name for n, name in current_tasks.items() if n not in base_tasks}
    new_on_target = {n: name for n, name in target_tasks.items() if n not in base_tasks}
    collisions = set(new_on_current) & set(new_on_target)

    if not collisions:
        print("Tasks: no collisions — already clean.")
        return

    # Assign new numbers starting after the highest number on target (across all tasks, not just new)
    max_target = max(target_tasks) if target_tasks else 0
    rename_map: dict[int, int] = {}
    next_num = max_target + 1
    for old_num in sorted(collisions):
        rename_map[old_num] = next_num
        next_num += 1

    print("Tasks: " + str(len(collisions)) + " collision(s) to renumber: "
          + ", ".join(f"T{n}→T{rename_map[n]}" for n in sorted(collisions)))

    if dry_run:
        for old_num in sorted(rename_map):
            old_name = current_tasks[old_num]
            new_name = old_name.replace(str(old_num) + "-", str(rename_map[old_num]) + "-", 1)
            print(f"  [dry-run] rename {old_name} → {new_name}")
        return

    # Abort before any mutation if a live session holds a task being renumbered
    sessions_dir = agent_dir / "sessions"
    if sessions_dir.exists():
        for session_dir in sessions_dir.iterdir():
            if not session_dir.is_dir():
                continue
            state_file = session_dir / "current_state"
            if not state_file.exists():
                continue
            try:
                task_num = int(state_file.read_text(encoding="utf-8").strip())
            except ValueError:
                continue
            if task_num in rename_map and _session_is_live(session_dir):
                print(
                    f"Error: session {session_dir.name} is live and holds task T{task_num}. "
                    "Stop that session before running prepare-merge.",
                    file=sys.stderr,
                )
                sys.exit(1)

    _rename_colliding_tasks(project_path, agent_dir, current_tasks, rename_map)
    _rewrite_task_refs(project_path, agent_dir, rename_map)


def _rename_colliding_tasks(project_path: Path, agent_dir: Path,
                             current_tasks: dict[int, str], rename_map: dict[int, int]) -> None:
    import subprocess
    import re
    tasks_dir = agent_dir / "tasks"

    for old_num in sorted(rename_map):
        new_num = rename_map[old_num]
        old_name = current_tasks[old_num]
        # Preserve slug: "133-prepare-merge" → "135-prepare-merge"
        new_name = str(new_num) + old_name[len(str(old_num)):]
        old_rel = str((tasks_dir / old_name).relative_to(project_path))
        new_rel = str((tasks_dir / new_name).relative_to(project_path))

        result = subprocess.run(
            ["git", "-C", str(project_path), "mv", old_rel, new_rel],
            capture_output=True,
        )
        if result.returncode != 0:
            # Fallback for untracked dirs
            (tasks_dir / old_name).rename(tasks_dir / new_name)

        # Rewrite H1 title: "# 133 - ..." → "# 135 - ..."
        task_md = tasks_dir / new_name / "task.md"
        if task_md.exists():
            text = task_md.read_text(encoding="utf-8")
            text = re.sub(
                rf"^# {old_num}(?=[\s\-]|$)",
                f"# {new_num}",
                text,
                count=1,
                flags=re.MULTILINE,
            )
            task_md.write_text(text, encoding="utf-8")

    # Clear chat_log_offset for all sessions — stale after renames + upcoming ref rewrite
    sessions_dir = agent_dir / "sessions"
    if sessions_dir.exists():
        for session_dir in sessions_dir.iterdir():
            if session_dir.is_dir():
                offset_file = session_dir / "chat_log_offset"
                if offset_file.exists():
                    offset_file.unlink()


def _session_is_live(session_dir: Path) -> bool:
    """Return True if the session's process is still running."""
    name = session_dir.name
    if name.startswith("pid-"):
        try:
            pid = int(name[4:])
            os.kill(pid, 0)
            return True
        except (ValueError, OSError):
            return False
    return False


def _rewrite_task_refs(project_path: Path, agent_dir: Path, rename_map: dict[int, int]) -> None:
    import re

    def _apply(text: str) -> str:
        # Process in descending order of old number to avoid cascading (T13 inside T133)
        for old_num in sorted(rename_map, reverse=True):
            new_num = rename_map[old_num]
            text = re.sub(rf"\bT{old_num}\b", f"T{new_num}", text)
            text = re.sub(rf"\btask {old_num}\b", f"task {new_num}", text, flags=re.IGNORECASE)
            text = re.sub(rf"\b{old_num}(?=-[a-z])", str(new_num), text)
            text = re.sub(rf"\bG{old_num}:(\d+)\b", rf"G{new_num}:\1", text)
        return text

    # Rewrite all task.md files (including non-colliding tasks that reference old numbers)
    tasks_dir = agent_dir / "tasks"
    if tasks_dir.exists():
        for task_dir in tasks_dir.iterdir():
            if task_dir.is_dir():
                task_md = task_dir / "task.md"
                if task_md.exists():
                    original = task_md.read_text(encoding="utf-8")
                    updated = _apply(original)
                    if updated != original:
                        task_md.write_text(updated, encoding="utf-8")

    # Rewrite chat_log.md
    chat_log = agent_dir / "chat_log.md"
    if chat_log.exists():
        original = chat_log.read_text(encoding="utf-8")
        updated = _apply(original)
        if updated != original:
            chat_log.write_text(updated, encoding="utf-8")

    # Rewrite current_state in dead sessions; live sessions were already rejected upstream
    sessions_dir = agent_dir / "sessions"
    if sessions_dir.exists():
        for session_dir in sessions_dir.iterdir():
            if not session_dir.is_dir():
                continue
            state_file = session_dir / "current_state"
            if not state_file.exists():
                continue
            try:
                task_num = int(state_file.read_text(encoding="utf-8").strip())
            except ValueError:
                continue
            if task_num in rename_map:
                state_file.write_text(str(rename_map[task_num]) + "\n", encoding="utf-8")


def _prepare_merge_chatlog(project_path: Path, agent_dir: Path, target: str,
                           merge_base: str, dry_run: bool) -> None:
    import subprocess
    import re

    agent_dir_rel = str(agent_dir.relative_to(project_path))

    def _git_show_text(ref: str, rel_path: str) -> str:
        try:
            return subprocess.check_output(
                ["git", "-C", str(project_path), "show", f"{ref}:{rel_path}"],
                text=True, stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            return ""

    def _last_mid(text: str) -> int:
        mids = re.findall(r"\*\*\[M(\d+)\]\*\*", text)
        return max(int(m) for m in mids) if mids else 0

    chat_log_rel = agent_dir_rel + "/chat_log.md"
    base_last = _last_mid(_git_show_text(merge_base, chat_log_rel))
    target_last = _last_mid(_git_show_text(target, chat_log_rel))

    chat_log = agent_dir / "chat_log.md"
    if not chat_log.exists():
        print("Chat log: not found — skipping.")
        return

    current_text = chat_log.read_text(encoding="utf-8")
    new_mids = [int(m) for m in re.findall(r"\*\*\[M(\d+)\]\*\*", current_text) if int(m) > base_last]

    if not new_mids:
        print("Chat log: no new entries beyond merge base — already clean.")
        return

    # Idempotency: if new entries already start beyond target's last MID, we're done
    if min(new_mids) > target_last:
        print("Chat log: new entries already positioned beyond target's last MID — already clean.")
        return

    offset = target_last - base_last
    if offset <= 0:
        print("Chat log: target has not advanced beyond merge base — no re-sequencing needed.")
        return

    def _reseq(m: "re.Match[str]") -> str:
        mid = int(m.group(1))
        if mid > base_last:
            width = max(len(m.group(1)), len(str(mid + offset)))
            return f"**[M{mid + offset:0{width}d}]**"
        return m.group(0)

    updated = re.sub(r"\*\*\[M(\d+)\]\*\*", _reseq, current_text)
    new_highest = max(new_mids) + offset

    print(f"Chat log: re-sequencing {len(new_mids)} new entr{'y' if len(new_mids)==1 else 'ies'} "
          f"(offset +{offset}, new highest M{new_highest}).")

    if dry_run:
        return

    chat_log.write_text(updated, encoding="utf-8")
    (agent_dir / "chat_log_counter").write_text(str(new_highest) + "\n", encoding="utf-8")


_FENCE_RE = re.compile(r"^\s*```")
_NODE_HEAD_RE = re.compile(r"^\[(\d+)\]")


def _node_starts(lines: list[str]) -> tuple[list[tuple[int, int]], bool]:
    """Fence-aware scan for node-definition lines — the ONE shared node-boundary
    detector behind `_partition_overflow`, `_scan_overflow_ids`, and the
    `mindmap-sync` `_extract_nodes`. All three agree on node STARTS because they
    share this scan (body extent is each caller's own concern).

    `lines` is `content.splitlines(keepends=True)`. Returns
    `(starts, in_fence_at_eof)` where `starts = [(line_index, node_id)]` for every
    `^[N]` line that is NOT inside a ``` code fence — so a fenced `[9]` example is
    never a ghost node. An unmatched fence surfaces as `in_fence_at_eof=True` so
    callers can fail closed.
    """
    starts: list[tuple[int, int]] = []
    in_fence = False
    for i, ln in enumerate(lines):
        if _FENCE_RE.match(ln):
            in_fence = not in_fence
            continue
        if not in_fence:
            m = _NODE_HEAD_RE.match(ln)
            if m:
                starts.append((i, int(m.group(1))))
    return starts, in_fence


def _partition_overflow(content: str):
    """Fence-aware partition of an OVERFLOW file into (preamble, spans, tail).

    `spans` is `[(node_id, raw_span_text)]` in file order, and
    `preamble + ''.join(raw) + tail == content` byte-for-byte. Returns **None**
    (caller fails closed) when the structure is ambiguous or unsafe to reorder:
    an unmatched code fence, no nodes, a blank-line-preceded markdown heading
    inside a NON-last node (can't tell node content from a section), or a coverage
    miss.

    `tail` = a trailing non-node section (e.g. `## Legacy`) detached off the LAST
    span — recognized ONLY at a heading **preceded by a blank line**, so a `## …`
    line glued directly to node prose stays part of that node (not amputated).
    """
    fence_re = _FENCE_RE
    heading_re = re.compile(r"^#{1,6}\s")

    lines = content.splitlines(keepends=True)
    starts, in_fence = _node_starts(lines)   # shared fence-aware scan
    if in_fence or not starts:
        return None

    preamble = "".join(lines[: starts[0][0]])
    spans: list[tuple[int, str]] = []
    for k, (idx, nid) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
        spans.append((nid, "".join(lines[idx:end])))

    def _section_heading_idx(span_text: str):
        """Line index of the first blank-line-preceded heading (fence-aware), or None."""
        sl = span_text.splitlines(keepends=True)
        infence = False
        for j in range(1, len(sl)):
            if fence_re.match(sl[j]):
                infence = not infence
                continue
            if not infence and heading_re.match(sl[j]) and sl[j - 1].strip() == "":
                return j
        return None

    # A section heading inside a non-last node is ambiguous — refuse to reorder.
    for _, span in spans[:-1]:
        if _section_heading_idx(span) is not None:
            return None

    # Detach a trailing section off the LAST span (blank-preceded heading only).
    tail = ""
    last_nid, last_span = spans[-1]
    j = _section_heading_idx(last_span)
    if j is not None:
        sl = last_span.splitlines(keepends=True)
        spans[-1] = (last_nid, "".join(sl[:j]))
        tail = "".join(sl[j:])

    if preamble + "".join(s for _, s in spans) + tail != content:
        return None
    return (preamble, spans, tail)


def sort_overflow_by_id(content: str) -> tuple[str, bool, str]:
    """Sort MIND_MAP_OVERFLOW.md `[N]` nodes into ascending numeric order.

    Pure + fail-safe. Returns (new_content, changed, reason).

    Contract:
    - **Already sorted / unsortable / ambiguous → (content, False, reason)**: input
      returned byte-for-byte; the caller must NOT rewrite (a sorted CRLF file is
      never normalized).
    - **Reordered → (new_content, True, "reordered N node(s)")**: node *bodies* and
      the preamble + trailing section are preserved byte-for-byte; only the blank-line
      separators BETWEEN nodes are canonicalized to the file's dominant separator.
      Idempotent — re-running is a no-op.

    Safety: fence-aware (a `[N]` line inside a ``` fence is not a node start); the
    reordered output is re-parsed and must yield the same preamble, the same node-body
    multiset, the same tail, and ascending ids — else it fails closed.
    """
    parsed = _partition_overflow(content)
    if parsed is None:
        return (content, False, "ambiguous/unparseable structure — left unchanged")
    preamble, spans, tail = parsed
    if len(spans) < 2:
        return (content, False, "fewer than 2 nodes — nothing to sort")

    ids = [nid for nid, _ in spans]
    if ids == sorted(ids):
        return (content, False, "already sorted")

    sep = "\r\n\r\n" if "\r\n" in content else "\n\n"
    ordered = sorted(spans, key=lambda t: t[0])        # stable: dup ids keep order
    bodies = [s.rstrip("\r\n") for _, s in ordered]    # node text, sans trailing sep
    new_content = sep.join(bodies)
    if preamble:                                        # preserve preamble byte-exact
        glue = "" if preamble.endswith(("\n", "\r")) else sep
        new_content = preamble + glue + new_content
    if tail:                                            # preserve tail byte-exact
        new_content = new_content + sep + tail
    if content.endswith("\r\n") and not new_content.endswith("\r\n"):
        new_content += "\r\n"
    elif content.endswith("\n") and not new_content.endswith("\n"):
        new_content += "\n"

    # REAL post-check: re-parse the output and compare structurally. Fail closed on
    # any mismatch (catches separator collisions, lost bytes, mis-detached sections).
    reparsed = _partition_overflow(new_content)
    if reparsed is None:
        return (content, False, "reordered output failed re-parse — left unchanged")
    new_pre, new_spans, new_tail = reparsed
    new_ids = [nid for nid, _ in new_spans]
    if (new_pre == preamble and new_tail == tail and new_ids == sorted(ids)
            and sorted(s.rstrip("\r\n") for _, s in new_spans) == sorted(bodies)):
        return (new_content, True, f"reordered {len(ids)} node(s)")
    return (content, False, "post-sort verification failed — left unchanged")


def _scan_overflow_ids(content: str) -> tuple[list[int], bool, bool]:
    """Fence-aware node-id scan. Returns (ids_in_file_order, in_fence_at_eof, ok)."""
    starts, in_fence = _node_starts(content.splitlines(keepends=True))
    return ([nid for _, nid in starts], in_fence, not in_fence)


def _parse_nodes(text: str) -> dict[int, str]:
    """node_id -> full raw body, for the git-merge collision detector
    (`_prepare_merge_mindmap`). Distinct from `_extract_nodes`/`_node_bodies`: it
    accumulates EVERY line of a node verbatim (no heading-trim) and requires a
    trailing space after `[N]` — its bodies feed an md5 comparison, so byte-exact
    accumulation is the point.

    Fence-aware (task 007): a `[N] ` line INSIDE a ``` code fence is NOT a node
    start — otherwise a fenced example would ghost-split the enclosing node and
    mis-attribute its body. Node-START detection uses the same fence toggle as
    `_node_starts`; the fence line itself is kept in the current node's body.
    Hoisted to module level (was nested) so it is unit-testable.
    """
    nodes: dict[int, str] = {}
    current_id: int | None = None
    current_lines: list[str] = []
    in_fence = False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            if current_id is not None:
                current_lines.append(line)
            continue
        m = None if in_fence else re.match(r"^\[(\d+)\] ", line)
        if m:
            if current_id is not None:
                nodes[current_id] = "".join(current_lines)
            current_id = int(m.group(1))
            current_lines = [line]
        elif current_id is not None:
            current_lines.append(line)
    if current_id is not None and current_lines:
        nodes[current_id] = "".join(current_lines)
    return nodes


def _prepare_merge_mindmap(project_path: Path, target: str, merge_base: str) -> None:
    import subprocess
    import hashlib

    def _git_show_text(ref: str, rel_path: str) -> str:
        try:
            return subprocess.check_output(
                ["git", "-C", str(project_path), "show", f"{ref}:{rel_path}"],
                text=True, stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            return ""

    def _h(text: str) -> str:
        return hashlib.md5(text.encode()).hexdigest()

    collision_found = False
    for filename in ("MIND_MAP.md", "MIND_MAP_OVERFLOW.md"):
        base_nodes = _parse_nodes(_git_show_text(merge_base, filename))
        target_nodes = _parse_nodes(_git_show_text(target, filename))
        cur_file = project_path / filename
        current_nodes = _parse_nodes(cur_file.read_text(encoding="utf-8") if cur_file.exists() else "")

        all_ids = set(base_nodes) | set(target_nodes) | set(current_nodes)
        changed_current = {n for n in all_ids if _h(current_nodes.get(n, "")) != _h(base_nodes.get(n, ""))}
        changed_target = {n for n in all_ids if _h(target_nodes.get(n, "")) != _h(base_nodes.get(n, ""))}
        collisions = sorted(changed_current & changed_target)
        if not collisions:
            continue

        collision_found = True
        print(f"\n{filename}: {len(collisions)} node collision(s) requiring manual synthesis:")
        for node_id in collisions:
            tgt = target_nodes.get(node_id, "(absent on target)")
            cur = current_nodes.get(node_id, "(absent on current)")
            print(f"\n  ── [{node_id}] {target} (target) ──")
            for line in tgt.splitlines():
                print(f"  {line}")
            print(f"\n  ── [{node_id}] HEAD (current) ──")
            for line in cur.splitlines():
                print(f"  {line}")

    if not collision_found:
        print("MIND_MAP: no node collisions.")


def print_usage():
    from tasks.template import usage_text
    print(usage_text())


def _gate_bounce(task_id: str, task_file, action: str) -> bool:
    """If `task_file` has open (unchecked) gates, print a steering message and
    return True (the caller should abort). Returns False when all gates are
    checked. The `--force` decision is the caller's — this only reports.
    """
    from tasks.core import _extract_head_position
    head = _extract_head_position(task_file)
    if head == "(all gates checked)":
        return False
    try:
        open_count = sum(
            1 for ln in task_file.read_text(encoding="utf-8").splitlines()
            if ln.strip().startswith("- [ ]")
        )
    except OSError:
        open_count = 0
    print(
        f"Blocked: task {task_id} has {open_count} open gate(s) — {action} needs them finalized.",
        file=sys.stderr,
    )
    print(f"  Next open gate: {head}", file=sys.stderr)
    print(
        "  Finish them (check the boxes in task.md), then retry — or override with --force.",
        file=sys.stderr,
    )
    return True


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

    _gc_dead_sessions(find_project_root())

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
        force = any(a in ("--force", "-f") for a in cmd_args[1:])
        project_path = find_project_root()

        # Handle 'tasks work done' - deactivate current task and set Status in task.md
        if task_num == "done":
            agent_dir = resolve_agent_dir(project_path)
            session_id = resolve_session_id()
            session_state = agent_dir / "sessions" / session_id / "current_state"

            # Find the active task from session state file
            prev_task = session_state.read_text(encoding="utf-8").strip() if session_state.exists() else None

            if prev_task:
                # Set ## Status to done in task.md
                tasks_dir = agent_dir / "tasks"
                matches = list(tasks_dir.glob(f"{prev_task}-*/task.md"))
                if matches:
                    task_file = matches[0]
                    if not force and _gate_bounce(prev_task, task_file, "closing this task"):
                        sys.exit(1)
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
                sessions_dir = agent_dir / "sessions"  # agent_dir already resolved above
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
            tasks_dir = resolve_agent_dir(project_path) / "tasks"
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
        agent_dir = resolve_agent_dir(project_path)
        agent_dir.mkdir(parents=True, exist_ok=True)
        session_id = resolve_session_id()
        session_dir = agent_dir / "sessions" / session_id
        session_state = session_dir / "current_state"
        prev_task = None
        if session_state.exists():
            prev_task = session_state.read_text(encoding="utf-8").strip()
        if prev_task and prev_task != task_num:
            from tasks.core import _extract_head_position, _extract_status
            prev_matches = list((agent_dir / "tasks").glob(f"{prev_task}-*/task.md"))
            if prev_matches:
                prev_file = prev_matches[0]
                prev_status = _extract_status(prev_file)
                prev_head = _extract_head_position(prev_file)
                if prev_head == "(all gates checked)":
                    if not prev_status.startswith("done"):
                        # Auto-close: set status to done
                        prev_lines = prev_file.read_text(encoding="utf-8").splitlines(keepends=True)
                        for i, line in enumerate(prev_lines):
                            if line.strip() == "## Status" and i + 1 < len(prev_lines):
                                prev_lines[i + 1] = "done\n"
                                prev_file.write_text("".join(prev_lines), encoding="utf-8")
                                break
                        print(f"Auto-closed task {prev_task} (all gates checked).")
                elif not prev_status.startswith("done") and not force:
                    # prev task still has open gates — don't silently abandon it.
                    _gate_bounce(prev_task, prev_file, f"switching to task {task_num}")
                    sys.exit(1)
                elif not prev_status.startswith("done"):
                    print(f"--force: switching away from task {prev_task} with open gates (left in_progress).")

        # Write task number to per-session current_state
        session_dir.mkdir(parents=True, exist_ok=True)
        session_state.write_text(f"{task_num}\n", encoding="utf-8")

        # Stale session GC handled by _gc_dead_sessions() at CLI entry point

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
            tasks_dir = resolve_agent_dir(project_path) / "tasks"
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

        # Create .agent/tasks/ (or .agent/<user>/tasks/ in multi-user mode)
        tasks_dir = resolve_agent_dir(target) / "tasks"
        existed = tasks_dir.exists()
        tasks_dir.mkdir(parents=True, exist_ok=True)
        print(f"  {tasks_dir.relative_to(target)}  {'exists' if existed else 'created'}")

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
            _PROVIDER_MAP = {"codex": "CodexAdapter", "antigravity": "AntigravityAdapter", "pi": "PiAdapter"}
            if provider not in _PROVIDER_MAP:
                print(f"Error: unknown provider '{provider}'. Choose: codex, antigravity, pi", file=sys.stderr)
                sys.exit(1)
            import importlib
            adapter_cls_name = _PROVIDER_MAP[provider]
            mod = importlib.import_module(f"provider.adapters.{provider}")
            adapter_cls = getattr(mod, adapter_cls_name)
            bootstrap_file = {"codex": "AGENTS.md", "antigravity": "GEMINI.md", "pi": "AGENTS.md"}[provider]
            bs_path = target / bootstrap_file
            already_existed = bs_path.exists()
            adapter = adapter_cls("init", target)
            adapter.install_bootstrap(target)
            print(f"  {bootstrap_file:<15}{'exists' if already_existed else 'created'}")
            if install_provider_hooks:
                adapter.install_hooks(target)
        elif install_provider_hooks:
            print("Error: --hooks requires --provider codex, antigravity, or pi", file=sys.stderr)
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
        models_flag = None  # --models CSV → explicit judge set for this run
        remaining_args = []
        i = 0
        while i < len(cmd_args):
            if cmd_args[i] == "--mode" and i + 1 < len(cmd_args):
                review_mode = cmd_args[i + 1]
                i += 2
            elif cmd_args[i] == "--models" and i + 1 < len(cmd_args):
                models_flag = [s.strip() for s in cmd_args[i + 1].split(",") if s.strip()]
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
            print("Usage: tasks panel-review [<number>] [--mode plan|impl] [--models codex:gpt-5.5,agy,...] [--prompt \"...\"] [--no-mind-map] [--bare] [--web-search] [--timeout SECONDS]", file=sys.stderr)
            sys.exit(1)

        project_path = find_project_root()

        # Resolve task file if task number given
        task_file = None
        task_path = None
        if task_num:
            tasks_dir = resolve_agent_dir(project_path) / "tasks"
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
                chat_log = resolve_agent_dir(project_path) / "chat_log.md"
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

        # Output path: task dir when task given, agent_dir/ otherwise
        if task_file:
            judge_md = task_file.parent / "judge.md"
        else:
            agent_dir = resolve_agent_dir(project_path)
            agent_dir.mkdir(exist_ok=True)
            judge_md = agent_dir / "judge.md"

        # Discover available judges via adapter classes — each adapter declares
        # its own binary_name() and panel_variants(). Adding a new provider is
        # a one-line append to PANEL_ADAPTERS; no dispatch changes needed.
        from provider.adapters.claude import ClaudeAdapter
        from provider.adapters.codex import CodexAdapter
        from provider.adapters.antigravity import AntigravityAdapter
        from provider.adapters.pi import PiAdapter
        from provider.sandbox import load_judge_config, resolve_judge_spec
        PANEL_ADAPTERS = (ClaudeAdapter, CodexAdapter, AntigravityAdapter, PiAdapter)
        _JUDGE_ADAPTERS = {
            "claude": ClaudeAdapter, "codex": CodexAdapter,
            "agy": AntigravityAdapter, "pi": PiAdapter,
        }

        # Judge-set precedence: --models flag → models.json `panel` (shipped ⊕
        # project .agent/models.json) → legacy full fan-out (only if no config).
        if models_flag is not None:
            spec_names = models_flag
        else:
            spec_names = load_judge_config().get("panel") or None

        judges = []  # list of (adapter_cls, variant)
        if spec_names:
            skipped = []
            for nm in spec_names:
                try:
                    provider, variant = resolve_judge_spec(nm)
                except ValueError as e:
                    print(f"Error: {e}", file=sys.stderr)
                    sys.exit(1)
                cls = _JUDGE_ADAPTERS.get(provider)
                if cls is None:
                    print(f"Error: no adapter for provider '{provider}' (spec '{nm}')", file=sys.stderr)
                    sys.exit(1)
                if cls.is_available():
                    judges.append((cls, variant))
                else:
                    skipped.append(f"{nm} ({cls.binary_name()} not on PATH)")
            if skipped:
                print(f"  Skipped unavailable: {', '.join(skipped)}", flush=True)
        else:
            # No configured panel — legacy discovery (all providers × variants).
            for cls in PANEL_ADAPTERS:
                if cls.is_available():
                    for variant in cls.panel_variants():
                        judges.append((cls, variant))

        if not judges:
            print("Error: no available judges. Install a provider CLI, or name "
                  "reachable ones with --models (e.g. --models codex:gpt-5.5,agy).",
                  file=sys.stderr)
            sys.exit(1)

        display_target = task_path or "(promptless)"
        print(f"Running panel {review_label} on {display_target} ({len(judges)} judges, {timeout_secs}s timeout)...", flush=True)

        def run_judge(judge_spec):
            adapter_cls, variant = judge_spec
            provider_name = adapter_cls.binary_name()
            label = f"{provider_name}:{variant}" if variant else provider_name
            if prompt_fn:
                prompt = prompt_fn(task_path, inline_context=(provider_name != "claude"))
                if extra_prompt:
                    prompt += f"\n\nAdditional steering from the user:\n{extra_prompt}"
            else:
                prompt = extra_prompt

            try:
                adapter = adapter_cls(session_id="judge", project_root=project_path)
                output = adapter.run_headless_judge(
                    prompt=prompt,
                    model=variant,
                    system_context=system_context,
                    web_search=web_search,
                    timeout_secs=timeout_secs,
                )
                return label, output
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

        # Classify each judge as succeeded vs failed — a failed judge must NOT
        # read as a clean empty review (T139). Failure markers come from the
        # adapters' format_judge_output ("(FAILED — exit N)", "(no output)") and
        # run_judge's own guards ("(timed out…)", "(error:…)").
        def _judge_failed(text: str) -> bool:
            t = text.lstrip()
            return (t.startswith("(FAILED") or t.startswith("(timed out")
                    or t.startswith("(error") or t == "(no output)")

        failed = {lbl for lbl, out in results.items() if _judge_failed(out)}
        succeeded = len(results) - len(failed)

        # Write judge.md (path already set above based on task_file presence)
        display_label = task_path or extra_prompt[:60]
        lines = [f"# Panel {review_label.title()} — {display_label}\n"]
        lines.append(f"**Judges:** {succeeded}/{len(results)} succeeded | **Web search:** {'yes' if web_search else 'no'} | **Timeout:** {timeout_secs}s\n")
        if failed:
            lines.append(f"**⚠ Failed judges:** {', '.join(sorted(failed))} — see their blocks below for the exit code / stderr. NOT a clean empty review.\n")
        lines.append("\n")
        # Triage frame (T124): prepend the pushback discipline AT THE TOP so
        # the reading agent meets the instruction BEFORE the per-judge
        # findings — primes the triage lens before the data is read.
        # The judges themselves never see this; it's bundled with their
        # outputs purely for the reading agent. Mirrors the in-task pushback
        # gate from template.judge_section / judge_impl_section, but for
        # panel reviews (where findings live in judge.md, not task.md) the
        # discipline rides with the data. Helper is unit-tested in tests/test_cli.py.
        lines.extend(_panel_triage_frame())
        for label in sorted(results.keys()):
            tag = "  [FAILED]" if label in failed else ""
            lines.append("═" * 60)
            lines.append(f"  JUDGE: {label}{tag}")
            lines.append("═" * 60 + "\n")
            lines.append(results[label].strip())
            lines.append("\n\n")
        judge_md.write_text("\n".join(lines), encoding="utf-8")
        summary = f"\nSaved: {judge_md.relative_to(project_path)} ({succeeded}/{len(judges)} judges succeeded)"
        if failed:
            summary += f"; FAILED: {', '.join(sorted(failed))}"
        print(summary, flush=True)

    elif cmd in ("plan-review", "impl-review", "judge"):
        # "judge" is a legacy alias — auto-detects mode from task status
        review_cmd = cmd
        if not cmd_args:
            print(f"Error: '{review_cmd}' requires a task number", file=sys.stderr)
            print(f"Usage: tasks {review_cmd} <number> [--backend codex|claude|agy|pi] [--model <variant>] [--prompt \"...\"]  (default backend: models.json default_judge, ships codex)", file=sys.stderr)
            sys.exit(1)

        import subprocess

        # Parse flags
        backend = None   # explicit --backend; else from models.json default_judge
        model = None     # explicit --model (variant within the backend)
        extra_prompt = ""
        remaining_args = []
        i = 0
        while i < len(cmd_args):
            if cmd_args[i] == "--backend" and i + 1 < len(cmd_args):
                backend = cmd_args[i + 1]
                i += 2
            elif cmd_args[i] == "--model" and i + 1 < len(cmd_args):
                model = cmd_args[i + 1]
                i += 2
            elif cmd_args[i] == "--prompt" and i + 1 < len(cmd_args):
                extra_prompt = cmd_args[i + 1]
                i += 2
            else:
                remaining_args.append(cmd_args[i])
                i += 1

        # No --backend → models.json default_judge (provider or provider:variant,
        # project-overridable; ships as "codex" so headless review avoids the
        # metered claude -p path by default). --model overrides the variant.
        if backend is None:
            from provider.sandbox import load_judge_config, resolve_judge_spec
            dj = load_judge_config().get("default_judge") or "claude"
            try:
                backend, dj_variant = resolve_judge_spec(dj)
            except ValueError:
                backend, dj_variant = dj, None
            if model is None:
                model = dj_variant

        # Accept friendlier aliases: "agy"/"gemini" → "antigravity", "qwen" → "pi"
        if backend in ("agy", "gemini"):
            backend = "antigravity"
        elif backend == "qwen":
            backend = "pi"
        if backend not in ("claude", "codex", "antigravity", "pi"):
            print(f"Error: unknown backend '{backend}'", file=sys.stderr)
            print("Supported: codex (default), claude, antigravity (alias: agy), pi (alias: qwen)", file=sys.stderr)
            sys.exit(1)

        if not remaining_args:
            print(f"Error: '{review_cmd}' requires a task number", file=sys.stderr)
            sys.exit(1)

        task_num = remaining_args[0]
        if task_num.isdigit():
            task_num = task_num.zfill(3)
        project_path = find_project_root()
        tasks_dir = resolve_agent_dir(project_path) / "tasks"
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

            # Bypass flag injected by provider.sandbox.run() — don't pass here.
            # The judge is a read-only evaluator sandboxed via provider.sandbox
            # (write containment via seatbelt/bwrap). PLAYBOOK_SESSION_ID=judge
            # above lets hooks identify judge sessions if needed.
            claude_args = ["-p", "--max-budget-usd", "2"]
            if model:
                from provider.adapters.claude import ClaudeAdapter
                claude_args += ["--model", ClaudeAdapter._MODEL_MAP.get(model, model)]
            claude_args += ["--append-system-prompt", system_context, prompt]

            from provider import sandbox as _sandbox
            print(f"Running {review_label} (claude) on {task_path}...", flush=True)
            result = _sandbox.run(
                "claude",
                claude_args,
                project_root=project_path,
                env=env,
                capture_output=True,
                text=True,
            )

        elif backend == "codex":
            if not shutil.which("codex"):
                print("Error: 'codex' not found on PATH", file=sys.stderr)
                print("Install: https://github.com/openai/codex", file=sys.stderr)
                sys.exit(1)

            prompt = prompt_fn(task_path, inline_context=True)
            # Codex has no system prompt — inline context into the user prompt
            full_prompt = f"{system_context}\n\n---\n\n{prompt}"

            codex_log = task_file.parent / "judge-codex.log"
            # Bypass flag (--dangerously-bypass-approvals-and-sandbox) inserted
            # after `exec` by provider.sandbox._compose_agent_argv.
            codex_args = ["exec"]
            if model:
                codex_args += ["-m", model]
            codex_args += [
                "-s", "workspace-write",
                "--ephemeral",
                "-C", str(project_path),
                "-o", str(codex_log),
                "-",  # read prompt from stdin
            ]

            codex_env = os.environ.copy()
            codex_env["PLAYBOOK_SESSION_ID"] = "judge"

            from provider import sandbox as _sandbox
            print(f"Running {review_label} (codex) on {task_path}...", flush=True)
            result = _sandbox.run(
                "codex", codex_args,
                project_root=project_path,
                env=codex_env,
                input=full_prompt,
                capture_output=True,
                text=True,
            )

        elif backend == "antigravity":  # agy
            if not shutil.which("agy"):
                print("Error: 'agy' not found on PATH", file=sys.stderr)
                sys.exit(1)

            prompt = prompt_fn(task_path, inline_context=True)
            full_prompt = f"{system_context}\n\n---\n\n{prompt}"
            if extra_prompt:
                full_prompt += f"\n\nAdditional steering from the user:\n{extra_prompt}"

            if model:
                print(f"  (note: agy has no model flag — ignoring --model {model}; uses agy's UI-selected model)", flush=True)
            # agy v1.0.2 quirks: --print mode ignores cwd, needs --add-dir;
            # no -m/--model flag yet (uses whatever the agy UI has set).
            # Bypass (--dangerously-skip-permissions) prepended by sandbox.
            agy_args = [
                "--add-dir", str(project_path),
                "--print", full_prompt,
                "--print-timeout", "300s",
            ]

            agy_env = os.environ.copy()
            agy_env["PLAYBOOK_SESSION_ID"] = "judge"

            from provider import sandbox as _sandbox
            print(f"Running {review_label} (agy) on {task_path}...", flush=True)
            result = _sandbox.run(
                "agy", agy_args,
                project_root=project_path,
                env=agy_env,
                capture_output=True,
                text=True,
            )

        else:  # pi (local Qwen via oMLX)
            if not (shutil.which("pi") or shutil.which("omlx")):
                print("Error: neither 'pi' nor 'omlx' found on PATH", file=sys.stderr)
                print("Install: oMLX app (https://omlx.app/) or pi CLI", file=sys.stderr)
                sys.exit(1)

            prompt = prompt_fn(task_path, inline_context=True)

            # Pi has no system prompt convention — append-system-prompt threads
            # the system context. --no-context-files skips AGENTS.md/CLAUDE.md
            # auto-load so the judge isn't biased by project conventions.
            # --provider oss points at the local oMLX endpoint (127.0.0.1:8000).
            pi_args = [
                "-p", prompt,
                "--provider", "oss",
                "--no-context-files",
                "--append-system-prompt", system_context,
            ]
            if model:
                pi_args += ["--model", model]

            pi_env = os.environ.copy()
            pi_env["PLAYBOOK_SESSION_ID"] = "judge"

            from provider import sandbox as _sandbox
            print(f"Running {review_label} (pi) on {task_path}...", flush=True)
            result = _sandbox.run(
                "pi", pi_args,
                project_root=project_path,
                env=pi_env,
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
            "antigravity": "judge-agy.log",
            "pi": "judge-pi.log",
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

        chat_log = resolve_agent_dir(project_path) / "chat_log.md"
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

    elif cmd == "intent":
        # Vertical retro: 4 blind intent extractions over one task's layers.
        if not cmd_args:
            print("Error: 'intent' requires a task number", file=sys.stderr)
            print("Usage: tasks intent <number> [--chat-file P] [--base REF --head REF] "
                  "[--collect-only] [--timeout S]", file=sys.stderr)
            sys.exit(1)

        task_num = cmd_args[0]
        if task_num.isdigit():
            task_num = task_num.zfill(3)
        chat_file = base = head = None
        collect_only = False
        timeout_secs = 300
        i = 1
        while i < len(cmd_args):
            a = cmd_args[i]
            if a == "--chat-file" and i + 1 < len(cmd_args):
                chat_file = Path(cmd_args[i + 1]); i += 2
            elif a == "--base" and i + 1 < len(cmd_args):
                base = cmd_args[i + 1]; i += 2
            elif a == "--head" and i + 1 < len(cmd_args):
                head = cmd_args[i + 1]; i += 2
            elif a == "--collect-only":
                collect_only = True; i += 1
            elif a == "--timeout" and i + 1 < len(cmd_args):
                timeout_secs = int(cmd_args[i + 1]); i += 2
            else:
                print(f"Error: unknown option for intent: {a}", file=sys.stderr)
                sys.exit(1)

        if bool(base) != bool(head):
            print("Error: --base and --head must be given together (an explicit range)",
                  file=sys.stderr)
            sys.exit(1)

        from tasks.intent import (
            collect_all, run_extractions, make_default_runner,
            write_run, find_task_dir, new_run_id, last_intent_entry, LAYERS,
        )
        project_path = find_project_root()
        agent_dir = resolve_agent_dir(project_path)
        task_dir = find_task_dir(agent_dir / "tasks", task_num)
        if task_dir is None:
            print(f"Error: no task {task_num} under {agent_dir / 'tasks'}", file=sys.stderr)
            sys.exit(1)

        slices = collect_all(project_path, agent_dir, task_dir, task_num,
                             chat_file=chat_file, base=base, head=head)
        print(f"Intent review — task {task_num} ({task_dir.name})")
        for layer in LAYERS:
            s = slices[layer]
            print(f"  {layer:7} {'✓' if s.available else '✗'}  {s.provenance}")
        avail = [l for l in LAYERS if slices[l].available]
        if not avail:
            print("Error: no available evidence on any layer — nothing to infer. "
                  "Pass --chat-file and/or --base/--head.", file=sys.stderr)
            sys.exit(1)

        run_id = new_run_id()
        if collect_only:
            from tasks.intent import build_prompt
            reports = {l: (build_prompt(slices[l]) if slices[l].available
                           else f"# Intent inferred from {l}\n\n_(no evidence — "
                                f"{slices[l].provenance})_\n") for l in LAYERS}
            print("\n(--collect-only: wrote prompts, skipped model calls)")
        else:
            print(f"\nRunning {len(avail)} blind extraction(s) "
                  f"(default judge, {timeout_secs}s each)...", flush=True)
            reports = run_extractions(slices, make_default_runner(
                project_path, timeout_secs=timeout_secs))

        run_dir = write_run(task_dir, slices, reports, run_id=run_id)
        rel = run_dir.relative_to(project_path)
        print(f"\nReports written: {rel}/")
        print(f"Grading sheet:   {rel}/review.md")
        prior = last_intent_entry(project_path / "INTENT.md", task_num)
        if prior:
            print("Prior validated intent exists — reconcile as a DELTA against INTENT.md.")
        print("\nNext: read review.md with the user, grade the seams, then append "
              "vetted intent to INTENT.md (the /intent command drives this).")

    elif cmd == "timeline":
        project_path = find_project_root()
        bash_history = resolve_agent_dir(project_path) / "bash_history"
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
        chat_log = resolve_agent_dir(project_path) / "chat_log.md"
        bash_history = resolve_agent_dir(project_path) / "bash_history"
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
        chat_log = resolve_agent_dir(project_path) / "chat_log.md"
        bash_history = resolve_agent_dir(project_path) / "bash_history"
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

        tasks_dir = resolve_agent_dir(project_path) / "tasks"
        chatlog_path = resolve_agent_dir(project_path) / "chat_log.md"
        bash_history_path = resolve_agent_dir(project_path) / "bash_history"
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
        tasks_dir_path = resolve_agent_dir(project_path) / "tasks"
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

    elif cmd == "global-retro-collect":
        since = None
        machine = None
        out_dir = Path.cwd()
        archive_format = "zip"
        roots = []
        i = 0
        while i < len(cmd_args):
            arg = cmd_args[i]
            if arg == "--since" and i + 1 < len(cmd_args):
                since = cmd_args[i + 1]
                i += 2
            elif arg == "--machine" and i + 1 < len(cmd_args):
                machine = cmd_args[i + 1]
                i += 2
            elif arg == "--out" and i + 1 < len(cmd_args):
                out_dir = Path(cmd_args[i + 1])
                i += 2
            elif arg == "--format" and i + 1 < len(cmd_args):
                archive_format = cmd_args[i + 1]
                i += 2
            elif arg.startswith("--"):
                print(f"Error: unknown option for global-retro-collect: {arg}", file=sys.stderr)
                print("Usage: tasks global-retro-collect --since DATE [--machine NAME] [--out DIR] [--format zip|tgz] ROOT [ROOT...]", file=sys.stderr)
                sys.exit(1)
            else:
                roots.append(Path(arg))
                i += 1

        if since is None:
            print("Error: global-retro-collect requires --since DATE", file=sys.stderr)
            print("Usage: tasks global-retro-collect --since DATE [--machine NAME] [--out DIR] [--format zip|tgz] ROOT [ROOT...]", file=sys.stderr)
            sys.exit(1)
        if not roots:
            print("Error: global-retro-collect requires at least one root directory", file=sys.stderr)
            print("Usage: tasks global-retro-collect --since DATE [--machine NAME] [--out DIR] [--format zip|tgz] ROOT [ROOT...]", file=sys.stderr)
            sys.exit(1)

        try:
            from tasks.global_retro_collect import collect_global_retro
            archive_path, manifest = collect_global_retro(
                roots=roots,
                since=since,
                out_dir=out_dir,
                machine=machine,
                archive_format=archive_format,
            )
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

        kept = sum(1 for project in manifest["projects"] if project["kept"])
        task_count = sum(len(project["included_tasks"]) for project in manifest["projects"])
        file_count = sum(len(project["included_files"]) for project in manifest["projects"])
        print(f"Created: {archive_path}")
        print(
            f"Global retro collection: {kept} project(s), "
            f"{task_count} task(s), {file_count} file(s)"
        )
        print("Includes manifest.json and manifest.tsv")

    elif cmd == "status":
        project_path = find_project_root()
        task_status(project_path)

    elif cmd == "freehand":
        project_path = find_project_root()
        sub = cmd_args[0] if cmd_args else None

        if sub == "log":
            # Extract chat_log messages from freehand-start to now into task.md
            agent_dir = resolve_agent_dir(project_path)
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
        agent_dir = resolve_agent_dir(project_path)

        if state_file.exists():
            task_num = state_file.read_text(encoding="utf-8").strip()
        else:
            task_num = None

        if not task_num:
            # Orchestrator mode: create a minimal freehand task (no Design Phase)
            print("No active task — creating freehand session...")
            from tasks.core import _next_task_number, _slugify
            tasks_dir = agent_dir / "tasks"
            task_num_int = _next_task_number(tasks_dir)
            task_num = f"{task_num_int:03d}"
            slug = _slugify("freehand")
            task_dir = tasks_dir / f"{task_num}-{slug}"
            task_dir.mkdir(parents=True, exist_ok=True)
            task_file = task_dir / "task.md"
            # Write minimal template — Freehand gate is first unchecked gate
            from datetime import datetime, timezone
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            task_file.write_text(
                f"# {task_num} - Freehand\n\n"
                f"## Status\nin_progress\n\n"
                f"## Intent\n(freehand session — intent determined during work)\n\n"
                f"## Work Plan\n\n"
                f"### Freehand\n"
                f"<!-- freehand-start: {now_iso} -->\n"
                f"- [ ] Freehand\n"
                f"- [ ] Freehand log — run `.claude/bin/tasks freehand log` to capture chat_log messages, "
                f"then retro-add checked gates for work done\n"
                f"- [ ] Rewrite this freehand work into normal task gates inside this task so the final trace reads like ordinary tracked work\n"
                f"- [ ] Rename this task folder and header to match what was actually done, then check this gate last\n",
                encoding="utf-8",
            )
            # Activate it
            session_id = resolve_session_id()
            session_dir = agent_dir / "sessions" / session_id
            session_dir.mkdir(parents=True, exist_ok=True)
            (session_dir / "current_state").write_text(f"{task_num}\n", encoding="utf-8")
            print(f"Created and activated task {task_num}")
        else:
            # Work mode: insert freehand block into current task
            tasks_dir = agent_dir / "tasks"
            matches = list(tasks_dir.glob(f"{task_num}-*/task.md"))
            if not matches:
                print(f"Error: task {task_num} not found", file=sys.stderr)
                sys.exit(1)
            task_file = matches[0]

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
                after_wp = task_text[work_plan_match.start():]
                gate_match = re.search(r'^- \[ \]', after_wp, re.MULTILINE)
                if gate_match:
                    insert_pos = work_plan_match.start() + gate_match.start()
                else:
                    sep_match = re.search(r'\n---\n', after_wp)
                    if sep_match:
                        insert_pos = work_plan_match.start() + sep_match.start()
                    else:
                        insert_pos = len(task_text)
            else:
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
        agent_tasks = resolve_agent_dir(project_path) / "tasks"
        check("project: tasks/ exists", agent_tasks.exists())
        claude_md = project_path / "CLAUDE.md"
        check("project: CLAUDE.md exists", claude_md.exists())
        mind_map = project_path / "MIND_MAP.md"
        check("project: MIND_MAP.md exists", mind_map.exists())

        # 2. Unicode
        stdout_enc = getattr(sys.stdout, "encoding", "unknown") or "unknown"
        check("unicode: stdout encoding", "utf" in stdout_enc.lower(), stdout_enc)

        # 3. Stale session dirs (current_state older than 24h — orphaned from crashed sessions)
        agent_dir = resolve_agent_dir(project_path)
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
        hooks_dirs = [project_path / "scripts", project_path / ".claude" / "hooks", project_path / "src" / "hooks"]
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

        # 9. Session-id resolver consistency (split-brain regression guard).
        # Python and bash must produce identical session_ids without PLAYBOOK_SESSION_ID,
        # otherwise hooks and CLI look in different .agent/sessions/ directories.
        gate_lib = None
        for hd in hooks_dirs + [project_path / "scripts"]:
            cand = hd / "gate-echo-lib.sh"
            if cand.exists():
                gate_lib = cand
                break
        if gate_lib:
            import subprocess as _sub
            from tasks.core import find_agent_root_pid
            saved = os.environ.pop("PLAYBOOK_SESSION_ID", None)
            try:
                find_agent_root_pid.cache_clear()
                py_sid = resolve_session_id()
                env = {k: v for k, v in os.environ.items() if k != "PLAYBOOK_SESSION_ID"}
                r = _sub.run(["bash", "-c", f"source {gate_lib} && resolve_session_id"],
                             capture_output=True, text=True, env=env, timeout=5)
                bash_sid = r.stdout.strip()
            finally:
                if saved is not None:
                    os.environ["PLAYBOOK_SESSION_ID"] = saved
            agree = py_sid == bash_sid and py_sid.startswith("pid-")
            detail = f"both → {py_sid}" if agree else f"MISMATCH py={py_sid!r} bash={bash_sid!r}"
            check("session-id: Python ≡ bash resolver", agree, detail)
        else:
            check("session-id: Python ≡ bash resolver", False, "gate-echo-lib.sh not found")

        # Summary
        total = passed + failed
        print(f"\n{passed}/{total} checks passed", end="")
        if failed:
            print(f" ({failed} failed)")
        else:
            print()

    elif cmd == "merge-doctor":
        if cmd_args and cmd_args[0] in ("--help", "-h"):
            print("Usage: tasks merge-doctor <source-branch> [target-branch]")
            print()
            print("  Audits a cross-namespace merge for per-user contamination,")
            print("  stranded conflict markers, and legacy .agent/ paths.")
            print("  Inspects the in-progress merge (MERGE_HEAD present) or the most")
            print("  recent merge commit reachable from HEAD. <source>/<target> are")
            print("  used for cross-comparison only — no branch is checked out.")
            print()
            print("  Exit: 1 if any actionable findings, else 0.")
            sys.exit(0)
        if not cmd_args:
            print("Usage: tasks merge-doctor <source-branch> [target-branch]", file=sys.stderr)
            print("  Audits a cross-namespace merge for per-user contamination,", file=sys.stderr)
            print("  stranded conflict markers, and legacy .agent/ paths.", file=sys.stderr)
            print("  Run 'tasks merge-doctor --help' for details.", file=sys.stderr)
            sys.exit(2)
        source = cmd_args[0]
        target = cmd_args[1] if len(cmd_args) > 1 else "main"
        project_path = find_project_root()
        if not (project_path / ".git").exists():
            print(f"Error: not a git repository: {project_path}", file=sys.stderr)
            sys.exit(2)
        # Validate both refs up front. A bad ref otherwise collapses to an empty
        # user set in _md_user_dirs and the doctor can exit 0 ("SAFE") while
        # silently skipping the intended cross-branch comparison.
        import subprocess as _sp
        for _ref in (source, target):
            if _sp.run(["git", "rev-parse", "--verify", "--quiet", f"{_ref}^{{commit}}"],
                       cwd=str(project_path), capture_output=True).returncode != 0:
                print(f"Error: not a valid git ref: {_ref}", file=sys.stderr)
                print("Usage: tasks merge-doctor <source-branch> [target-branch]", file=sys.stderr)
                sys.exit(2)
        findings = run_merge_doctor(project_path, source, target)
        sys.exit(1 if findings else 0)

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
            """Extract {node_id: full_text} from a mind map file.

            Node STARTS come from the shared fence-aware `_node_starts` scan, so a
            `[N]` line inside a ``` fence is NOT a node boundary (it stays part of
            the enclosing node) — the three mind-map parsers agree on starts.

            The LAST node would otherwise absorb everything to EOF, so a trailing
            `## Legacy`/notes section is cut at the first markdown heading (`#…`)
            after the node's first line — but NOT a `#` line inside a fenced code
            block (a `# comment` in a code example), so multi-line overflow node
            bodies with code aren't truncated.
            """
            content = filepath.read_text(encoding="utf-8")
            lines = content.splitlines(keepends=True)
            starts, _ = _node_starts(lines)   # fence-aware: a fenced [N] is not a node
            nodes: dict[int, str] = {}
            for k, (idx, nid) in enumerate(starts):
                end_idx = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
                part = ''.join(lines[idx:end_idx])
                plines = part.split('\n')
                end = len(plines)
                in_fence = False
                for i in range(1, len(plines)):
                    if plines[i].lstrip().startswith('```'):
                        in_fence = not in_fence
                        continue
                    if not in_fence and _re.match(r'^#{1,6}\s', plines[i]):
                        end = i
                        break
                nodes[nid] = '\n'.join(plines[:end]).strip()
            return nodes

        main_nodes = _extract_nodes(main_file)
        overflow_nodes = _extract_nodes(overflow_file)

        # Guard: an unmatched code fence in MIND_MAP.md makes `_extract_nodes`'
        # node boundaries (and thus every drift/missing diagnostic below AND the
        # sync source of truth) unreliable. Detect it up front — before any
        # diagnostic is printed or any --fix path (incl. sort-only) writes — so the
        # operator isn't handed a misleading "Missing from overflow" list computed
        # from a corrupt parse. Hard-stop under --fix; warn in read-only mode.
        _, _main_in_fence = _node_starts(
            main_file.read_text(encoding="utf-8").splitlines(keepends=True))
        if _main_in_fence:
            msg = ("MIND_MAP.md has an unmatched code fence — its node boundaries "
                   "can't be trusted")
            if fix_mode:
                print(f"Error: {msg}, so --fix won't run. File(s) NOT modified; "
                      "close the fence and re-run.", file=sys.stderr)
                sys.exit(1)
            print(f"Warning: {msg}; the counts below may be wrong.", file=sys.stderr)

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

        # Content drift (full nodes only — summary nodes are intentionally shorter).
        # `drifted` = EVERY full node whose main/overflow text differs, regardless
        # of length sign. A same-length ref remap (e.g. [29]→[36]) has diff==0 and
        # used to be mis-bucketed as "overflow ahead" and skipped by --fix; it now
        # lands in `drifted` and is auto-syncable. main is the canonical source, so
        # all drift syncs main→overflow.
        # NOTE: the `'↗' not in main_text` gate means SUMMARY (↗) nodes never enter
        # this comparison — so mindmap-sync structurally CANNOT catch a stale ref
        # buried in the OVERFLOW body of a ↗-summary node (the §4.3 case). That is
        # ref-integrity.py's job (whole-file ref scan) + the skill's manual grep.
        drifted: list[tuple[int, int]] = []   # (nid, signed diff = len(main)-len(overflow))
        for nid in sorted(set(main_nodes) & set(overflow_nodes)):
            main_text = main_nodes[nid]
            overflow_text = overflow_nodes[nid]
            if '↗' not in main_text and main_text != overflow_text:
                drifted.append((nid, len(main_text) - len(overflow_text)))

        if drifted:
            print("Content drift (full nodes only):")
            for nid, diff in drifted:
                if diff > 0:
                    print(f"  [{nid}] main AHEAD by {diff} chars")
                elif diff < 0:
                    print(f"  [{nid}] overflow AHEAD by {-diff} chars")
                else:
                    print(f"  [{nid}] differs (same length — e.g. ref remap)")
        else:
            print("No content drift.")

        # Cross-reference health
        all_main_text = main_file.read_text(encoding="utf-8")
        all_refs = set(int(m.group(1)) for m in _re.finditer(r'\[(\d+)\]', all_main_text))
        broken = sorted(all_refs - set(main_nodes))
        if broken:
            print(f"\nBroken cross-references: {broken}")

        # Numeric-sort status, computed independently of drift/main_only so a
        # complete-but-out-of-order overflow (the run-2 manual-reorder case) is
        # reachable by --fix. Read newline-preserving (Path.open, not read_text's
        # newline= kwarg which is Python ≥3.13 only) so a sorted CRLF file isn't
        # flagged/rewritten.  NB: the sort path below is CRLF-safe; the drift/append
        # branch still normalizes CRLF→LF via read_text (pre-existing — Parked P1).
        with overflow_file.open(encoding="utf-8", newline="") as _f:
            overflow_raw = _f.read()
        _, sort_needed, sort_reason = sort_overflow_by_id(overflow_raw)
        if sort_needed:
            print(f"Overflow node order: out of numeric order → --fix will sort.")
        elif sort_reason not in ("already sorted", "fewer than 2 nodes — nothing to sort"):
            print(f"Overflow sort: skipped ({sort_reason}).")

        # --fix: copy main→overflow for EVERY drifted full node (any length sign,
        # incl. same-length ref remaps) plus nodes missing from overflow, THEN
        # numerically sort the result (idempotent — appended nodes land in place).
        #
        # The drift edit is a span-SCOPED replace keyed by node id (via the
        # fence-aware `_partition_overflow`): for each drifted node, ONLY that
        # node's body substring is swapped INSIDE its own raw span (count=1), so
        # (a) one node's text being a substring of another can't cause a
        # wrong-occurrence hit, (b) untouched spans stay byte-identical (separators
        # and all), and (c) any post-body remainder of a drifted node (e.g. a glued
        # `## heading` + content that `_extract_nodes` truncates away) is preserved
        # rather than dropped. It is CRLF-safe end to end: it reuses the
        # newline-preserving `overflow_raw` (no `read_text` LF-normalization), and
        # converts both the old and new node text (LF, from `read_text`) to the
        # overflow's native newline before matching/splicing. `main_only` nodes are
        # appended at the boundary, each emitting its OWN `sep` (NOT relying on the
        # trailing sort, which returns bytes unchanged when ids are already
        # ascending); interior separators of existing nodes are left untouched.
        if fix_mode and (drifted or main_only or sort_needed):
            if drifted or main_only:
                # (MIND_MAP.md's fence integrity was already verified up front,
                # before any diagnostic or write — see the guard after extraction.)
                parsed = _partition_overflow(overflow_raw)
                if parsed is None:
                    # Fail closed: an ambiguous structure (unmatched code fence, a
                    # section heading inside a non-last node, or no nodes) can't be
                    # edited span-by-span safely. Write NOTHING and exit non-zero —
                    # never fall through to the old corrupting replace()/rstrip().
                    print("Error: MIND_MAP_OVERFLOW.md has an ambiguous structure "
                          "(unmatched code fence, or a section heading inside a "
                          "non-last node) — --fix cannot safely edit raw spans. "
                          "File NOT modified; resolve the structure by hand and "
                          "re-run.", file=sys.stderr)
                    sys.exit(1)
                preamble, spans, tail = parsed
                nl = "\r\n" if "\r\n" in overflow_raw else "\n"
                sep = nl + nl
                drift_ids = {nid for nid, _ in drifted}
                # Drift: swap ONLY the drifted node's body inside its own span,
                # preserving every other byte (untouched spans + drifted remainder).
                # The old/new text is converted to the SPAN's own newline style
                # (not the file's dominant one) so a stray CRLF elsewhere in an
                # otherwise-LF file can't make `old` un-matchable and trip a
                # spurious fail-closed on a node that's actually fine.
                out_spans = []
                for nid, span in spans:
                    if nid in drift_ids:
                        span_nl = "\r\n" if "\r\n" in span else "\n"
                        old = overflow_nodes[nid].replace("\n", span_nl)
                        new = main_nodes[nid].replace("\n", span_nl)
                        if old not in span:
                            print(f"Error: could not locate node [{nid}]'s current "
                                  "body within its span for an exact sync — --fix "
                                  "aborted, file NOT modified.", file=sys.stderr)
                            sys.exit(1)
                        out_spans.append(span.replace(old, new, 1))
                    else:
                        out_spans.append(span)               # byte-identical
                overflow_content = preamble + "".join(out_spans)
                # Append main_only nodes at the boundary only (interior separators
                # of existing nodes untouched): trim the last span's trailing
                # newlines, re-add a canonical sep before each new node, and a sep
                # before the trailing section if one exists.
                if main_only:
                    overflow_content = overflow_content.rstrip("\r\n")
                    for nid in main_only:
                        overflow_content += sep + main_nodes[nid].replace("\n", nl)
                    if tail:
                        overflow_content += sep
                overflow_content = overflow_content + tail
                if overflow_raw.endswith("\r\n") and not overflow_content.endswith("\r\n"):
                    overflow_content += "\r\n"
                elif overflow_raw.endswith("\n") and not overflow_content.endswith("\n"):
                    overflow_content += "\n"
                fixed = len(drifted) + len(main_only)
            else:
                overflow_content = overflow_raw   # sort-only: preserve newlines
                fixed = 0
            overflow_content, sort_changed, sort_msg = sort_overflow_by_id(overflow_content)
            with overflow_file.open("w", encoding="utf-8", newline="") as _f:
                _f.write(overflow_content)   # newline-preserving write (3.8-safe)
            done = []
            if fixed:
                done.append(f"synced {fixed} node(s) main→overflow")
            if sort_changed:
                done.append(sort_msg)   # "reordered N node(s)"
            print(f"\nFixed: {', '.join(done) if done else 'no change needed'}")
        elif drifted or main_only:
            fixable = len(drifted) + len(main_only)
            print(f"\n{fixable} node(s) can be auto-synced main→overflow. Run: tasks mindmap-sync --fix")

    elif cmd == "log":
        # tasks log [N] [--width W]
        # Compact one-line-per-message view of chat_log.md (no gate cruft).
        # N: show only the last N messages (default: all).
        # --width: crop each message body to W chars (default 500).
        import re
        cmd_args = sys.argv[2:]
        last_n = None
        width = 500
        i = 0
        while i < len(cmd_args):
            a = cmd_args[i]
            if a == "--width" and i + 1 < len(cmd_args):
                width = max(10, int(cmd_args[i + 1])); i += 2
            elif a.isdigit():
                last_n = int(a); i += 1
            else:
                i += 1
        project_path = find_project_root()
        chat_log = resolve_agent_dir(project_path) / "chat_log.md"
        if not chat_log.exists():
            print("Error: .agent/chat_log.md not found", file=sys.stderr)
            sys.exit(1)
        text = chat_log.read_text(encoding="utf-8")
        blocks = text.split("\n---\n")
        lines = []
        for block in blocks:
            m = re.match(
                r'\*\*(\[M\d+\])\*\* \[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}):\d{2} UTC\] `(\w+)`\s*\n+(.*)',
                block.strip(), re.DOTALL
            )
            if m:
                mid, ts, role, body = m.groups()
                body = " ".join(body.split())
                if len(body) > width:
                    body = body[:width - 1] + "…"
                lines.append(f"{mid} {ts} {role:<6} {body}")
        if last_n is not None:
            lines = lines[-last_n:]
        for line in lines:
            print(line)

    elif cmd == "prepare-merge":
        project_path = find_project_root()
        target = "main"
        dry_run = False
        remaining = list(cmd_args)
        while remaining:
            a = remaining.pop(0)
            if a == "--target" and remaining:
                target = remaining.pop(0)
            elif a == "--dry-run":
                dry_run = True
            else:
                print(f"Unknown argument: {a}", file=sys.stderr)
                sys.exit(1)
        _cmd_prepare_merge(project_path, target, dry_run)

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
