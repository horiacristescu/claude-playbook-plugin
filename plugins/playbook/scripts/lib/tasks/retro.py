"""Retrospective analysis for task history.

Extracts structured data from task.md files, chat_log.md, and MIND_MAP.md
for project-level retrospective analysis.
"""
from __future__ import annotations

import re
from pathlib import Path


def extract_tasks(tasks_dir: Path, since: int = 0) -> list[dict]:
    """Extract structured data from task.md files.

    Args:
        tasks_dir: Path to .agent/tasks/ directory
        since: Only include tasks with number >= since (0 = all)

    Returns list of dicts with keys:
        number, title, intent, why, status, gate_count, checked_count,
        bare_checkmark_count, gate_texts, parked_items, playbook_type
    """
    if not tasks_dir.exists():
        return []

    results = []
    for task_dir in sorted(tasks_dir.iterdir()):
        if not task_dir.is_dir():
            continue
        m = re.match(r'^(\d+)-(.+)$', task_dir.name)
        if not m:
            continue
        num = int(m.group(1))
        if num < since:
            continue

        task_file = task_dir / "task.md"
        if not task_file.exists():
            continue

        content = task_file.read_text(encoding="utf-8")
        results.append(_parse_task(num, m.group(2), content))

    return results


def _parse_task(num: int, slug: str, content: str) -> dict:
    """Parse a task.md file into structured data."""
    lines = content.splitlines()

    # Extract sections
    intent = _extract_section(lines, "Intent")
    why = _extract_section(lines, "Why")
    status = _extract_status(lines)
    parked = _extract_section(lines, "Parked")

    # Gate analysis
    gate_pattern = re.compile(r'^\s*- \[( |x|X)\]\s*(.*)')
    gates = []
    for line in lines:
        m = gate_pattern.match(line)
        if m:
            checked = m.group(1) in ('x', 'X')
            text = m.group(2).strip()
            gates.append({"checked": checked, "text": text})

    checked_count = sum(1 for g in gates if g["checked"])
    # Bare checkmark: checked gate where the agent didn't append any outcome.
    # Heuristic: text is very short (≤60 chars) and doesn't contain outcome markers
    # like " — ", ":", ".", or result words. Template gates are typically short labels.
    bare_count = 0
    for g in gates:
        if not g["checked"]:
            continue
        text = g["text"]
        # Long text = agent wrote something substantive
        if len(text) > 60:
            continue
        # Contains outcome markers = annotated
        if any(marker in text for marker in [" — ", " - ", "✓", "✗", "passing", "passed", "fixed"]):
            continue
        bare_count += 1

    # Parked items
    parked_items = []
    if parked and parked.strip() not in ("", "(Findings or ideas that emerged during work but are out of scope. Describe each with enough context for a future task to pick it up.)"):
        for line in parked.splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                parked_items.append(stripped[2:])

    # Detect playbook type from content
    playbook_type = _detect_type(content)

    return {
        "number": num,
        "title": slug.replace("-", " ").title(),
        "intent": intent,
        "why": why,
        "status": status,
        "gate_count": len(gates),
        "checked_count": checked_count,
        "bare_checkmark_count": bare_count,
        "gate_texts": [g["text"] for g in gates],
        "parked_items": parked_items,
        "playbook_type": playbook_type,
    }


def _extract_section(lines: list[str], heading: str) -> str:
    """Extract content between ## heading and next ## heading."""
    in_section = False
    section_lines = []
    for line in lines:
        if line.strip() == f"## {heading}":
            in_section = True
            continue
        if in_section:
            if line.startswith("## "):
                break
            section_lines.append(line)
    return "\n".join(section_lines).strip()


def _extract_status(lines: list[str]) -> str:
    """Extract status line (line after last ## Status)."""
    status_idx = None
    for i, line in enumerate(lines):
        if line.strip() == "## Status":
            status_idx = i
    if status_idx is not None and status_idx + 1 < len(lines):
        return lines[status_idx + 1].strip()
    return "unknown"


def _detect_type(content: str) -> str:
    """Detect task type from content heuristics."""
    if "<!-- stub:" in content:
        m = re.search(r'<!-- stub:(\w+) -->', content)
        return f"stub:{m.group(1)}" if m else "stub"
    if "## Design Phase" not in content and "## Work" in content:
        return "quick"
    if "### Round" in content:
        return "investigate"
    if "### Lenses" in content or "### Verdict" in content:
        return "evaluate"
    if "## Design Phase" in content:
        return "build"
    return "unknown"


def extract_chatlog(path: Path, task_windows: dict[int, tuple[str, str]] | None = None) -> list[dict]:
    """Extract messages from chat_log.md.

    Args:
        path: Path to chat_log.md
        task_windows: Optional dict mapping task number → (start_timestamp, end_timestamp).
            If provided, each message gets a 'task' field with the task number it falls within.

    Returns list of dicts with keys: id, timestamp, speaker, text, task (optional)
    """
    if not path.exists():
        return []

    content = path.read_text(encoding="utf-8")
    messages = []

    # Pattern: **[M001]** [2026-02-14 10:14:45 UTC] `HOST`
    msg_pattern = re.compile(
        r'\*\*\[M(\d+)\]\*\*\s+\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\s+UTC)?)\]\s+`(\w+)`'
    )

    # Split on message headers
    parts = msg_pattern.split(content)
    # parts: [preamble, id1, ts1, speaker1, text1, id2, ts2, speaker2, text2, ...]
    i = 1
    while i + 3 < len(parts):
        msg_id = int(parts[i])
        timestamp = parts[i + 1].strip()
        speaker = parts[i + 2]
        text = parts[i + 3].strip()
        # Trim text to first --- or next message
        if "---" in text:
            text = text[:text.index("---")].strip()

        msg = {
            "id": msg_id,
            "timestamp": timestamp,
            "speaker": speaker,
            "text": text,
        }

        # Attribute to task window if available
        if task_windows:
            msg["task"] = _attribute_to_task(timestamp, task_windows)

        messages.append(msg)
        i += 4

    return messages


def _normalize_ts(ts: str) -> str:
    """Strip ' UTC' suffix for consistent comparison."""
    return ts.replace(" UTC", "").strip()


def _attribute_to_task(timestamp: str, task_windows: dict[int, tuple[str, str]]) -> int | None:
    """Find which task window a timestamp falls into."""
    ts = _normalize_ts(timestamp)
    for task_num, (start, end) in task_windows.items():
        if _normalize_ts(start) <= ts < _normalize_ts(end):  # F2: exclusive end
            return task_num
    return None


def build_task_windows(chatlog_path: Path, bash_history_path: Path | None = None) -> dict[int, tuple[str, str]]:
    """Build task number → (start_timestamp, end_timestamp) mapping.

    Scans chat_log.md gate entries and bash_history for 'tasks work <N>' activations.
    Each task's window extends from its activation to the next task's activation.
    """
    windows: dict[int, str] = {}  # task_num → activation timestamp

    # Scan chat_log for gate entries: **[G083:42]** [timestamp]
    if chatlog_path.exists():
        content = chatlog_path.read_text(encoding="utf-8")
        gate_pattern = re.compile(
            r'\*\*\[G(\d+):\d+\]\*\*\s+\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\s+UTC)?)\]'
        )
        for m in gate_pattern.finditer(content):
            task_num = int(m.group(1))
            ts = m.group(2).strip()
            if task_num not in windows or ts < windows[task_num]:
                windows[task_num] = ts

    # Scan bash_history for 'tasks work <N>' entries
    if bash_history_path and bash_history_path.exists():
        content = bash_history_path.read_text(encoding="utf-8")
        work_pattern = re.compile(
            r'\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\].*tasks\s+work\s+(\d+)'
        )
        for m in work_pattern.finditer(content):
            ts = m.group(1).strip()
            task_num = int(m.group(2))
            if task_num not in windows or ts < windows[task_num]:
                windows[task_num] = ts

    if not windows:
        return {}

    # Convert to (start, end) ranges: each task ends when the next one starts
    sorted_tasks = sorted(windows.items(), key=lambda x: x[1])
    result = {}
    for i, (task_num, start_ts) in enumerate(sorted_tasks):
        if i + 1 < len(sorted_tasks):
            end_ts = sorted_tasks[i + 1][1]
        else:
            end_ts = "9999-12-31 23:59:59 UTC"  # still active
        result[task_num] = (start_ts, end_ts)

    return result


def extract_mindmap(path: Path) -> list[dict]:
    """Extract nodes from MIND_MAP.md.

    Returns list of dicts with keys: id, text, size (bytes)
    """
    if not path.exists():
        return []

    content = path.read_text(encoding="utf-8")
    nodes = []

    # Pattern: [N] **Title** — description
    node_pattern = re.compile(r'^\[(\d+)\]\s+(.*)', re.MULTILINE)

    for m in node_pattern.finditer(content):
        node_id = int(m.group(1))
        text = m.group(2).strip()
        nodes.append({
            "id": node_id,
            "text": text,
            "size": len(text.encode("utf-8")),
        })

    return nodes


# --- Analysis passes ---

# Default gate counts per template type (approximate)
_TEMPLATE_DEFAULTS = {
    "build": 20,
    "quick": 3,
    "investigate": 15,
    "evaluate": 15,
    "unknown": 15,
}


def analyze_intent_health(tasks: list[dict]) -> list[dict]:
    """Pass 1: Score each task's intent health using structural signals.

    Returns list of dicts with keys:
        number, title, intent_present, bare_ratio, gate_adaptation, parked_count,
        hollowness (0.0-1.0, higher = worse)
    """
    results = []
    for t in tasks:
        checked = t["checked_count"]
        bare = t["bare_checkmark_count"]
        bare_ratio = bare / max(checked, 1)

        # Intent present = non-empty and not a placeholder
        intent_present = bool(t["intent"]) and not t["intent"].startswith("(")

        # Gate adaptation: how much did the task deviate from template default?
        default = _TEMPLATE_DEFAULTS.get(t["playbook_type"], 15)
        gate_adaptation = t["gate_count"] - default  # positive = added, negative = removed

        # Hollowness score: weighted combination
        # - Missing intent: +0.4
        # - High bare ratio: +0.4 * bare_ratio
        # - No adaptation (exactly template): +0.1
        # - No parked items on a large task: +0.1
        hollowness = 0.0
        if not intent_present:
            hollowness += 0.4
        hollowness += 0.4 * bare_ratio
        if gate_adaptation == 0 and t["gate_count"] > 5:
            hollowness += 0.1
        if len(t["parked_items"]) == 0 and t["gate_count"] > 15:
            hollowness += 0.1

        results.append({
            "number": t["number"],
            "title": t["title"],
            "intent_present": intent_present,
            "bare_ratio": bare_ratio,
            "gate_adaptation": gate_adaptation,
            "parked_count": len(t["parked_items"]),
            "hollowness": round(hollowness, 2),
        })

    # Sort by hollowness descending
    results.sort(key=lambda x: x["hollowness"], reverse=True)
    return results


def analyze_steering(chatlog: list[dict]) -> list[dict]:
    """Pass 2: Extract user corrections from chat log.

    Filters for short messages (≤100 words) containing correction markers.
    Returns list of dicts with keys: id, timestamp, task, text
    """
    correction_markers = [
        "no,", "no ", "not that", "instead ", "don't ", "stop ",
        "wrong", "I meant", "not what I", "that's not",
    ]
    # "let's" and "actually" and "wait" are too broad — normal instructions

    # Patterns to exclude (not corrections)
    exclude_patterns = [
        "You are a senior engineer",  # judge prompts
        "<task-notification>",
        "<ide_selection>",
        "tool-use-id",
    ]

    corrections = []
    for msg in chatlog:
        text = msg["text"]
        words = text.split()
        # Filter: short messages only
        if len(words) > 100:
            continue
        # Filter: exclude known non-correction patterns
        if any(pat in text for pat in exclude_patterns):
            continue
        # Filter: must contain a correction marker
        lower = text.lower()
        if not any(marker in lower for marker in correction_markers):
            continue
        corrections.append({
            "id": msg["id"],
            "timestamp": msg["timestamp"],
            "task": msg.get("task"),
            "text": text[:200],  # truncate for report
        })

    return corrections


def analyze_garbage(tasks: list[dict]) -> dict:
    """Pass 3: Garbage collection — parked items, pending tasks, loose ends.

    Returns dict with keys: parked (list), pending (list), loose_ends (list)
    """
    all_intents = " ".join(t["intent"] for t in tasks if t["intent"])

    # Parked items: check if pursued by later tasks
    parked = []
    for t in tasks:
        for item in t["parked_items"]:
            # Extract keywords from parked item (first 5 significant words)
            words = [w.lower().strip("*:,.-()") for w in item.split()[:10]
                     if len(w) > 4]  # F4: longer words to reduce false positives
            matches = sum(1 for w in words[:5] if w in all_intents.lower())
            pursued = matches >= 2  # F4: require 2+ keyword matches
            parked.append({
                "task": t["number"],
                "text": item[:150],
                "status": "pursued" if pursued else "still-waiting",
            })

    # Pending tasks
    pending = []
    for t in tasks:
        if t["status"] == "pending":
            pending.append({
                "number": t["number"],
                "title": t["title"],
                "gates": f"{t['checked_count']}/{t['gate_count']}",
                "has_work": t["checked_count"] > 0,
            })

    # Loose ends: done tasks with unchecked gates, stubs
    loose_ends = []
    for t in tasks:
        if t["status"] == "done" and t["checked_count"] < t["gate_count"]:
            loose_ends.append({
                "number": t["number"],
                "title": t["title"],
                "unchecked": t["gate_count"] - t["checked_count"],
            })
        if t["playbook_type"].startswith("stub"):
            loose_ends.append({
                "number": t["number"],
                "title": t["title"],
                "issue": "stub not expanded",
            })

    return {"parked": parked, "pending": pending, "loose_ends": loose_ends}


def analyze_mindmap(nodes: list[dict]) -> list[dict]:
    """Pass 4: Mind map reconciliation.

    Flag nodes that are derivable from repo, too large, or likely stale.
    Returns list of dicts with keys: id, text_preview, size, proposal, reason
    """
    # Patterns that indicate derivable/compressible content
    derivable_patterns = [
        (re.compile(r'`[/\w]+\.\w+`'), "contains file paths (derivable from repo)"),
        (re.compile(r'\d+ tests?'), "contains test counts (derivable from test suite)"),
    ]

    proposals = []
    for n in nodes:
        text = n["text"]
        size = n["size"]

        # Large nodes
        if size > 500:
            proposals.append({
                "id": n["id"],
                "text_preview": text[:80],
                "size": size,
                "proposal": "compress",
                "reason": f"Large node ({size} bytes) — review for compressible content",
            })
            continue  # F3: don't double-flag

        # Check for derivable patterns (only for small nodes)
        for pattern, reason in derivable_patterns:
            if pattern.search(text):
                proposals.append({
                    "id": n["id"],
                    "text_preview": text[:80],
                    "size": size,
                    "proposal": "review",
                    "reason": reason,
                })
                break  # one flag per node

    return proposals
