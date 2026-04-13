"""
Helpers for Codex lifecycle hook installation and runtime decisions.

Codex hook execution currently lives outside the provider policy stubs: Codex
invokes commands declared in hooks.json directly. This module keeps the logic
pure/testable while the small scripts in scripts/ act as thin entrypoints.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

from .policy import _is_code_file_path

HOOK_TIMEOUT_MS = 5000
MISSING_FILE_DIGEST = "__MISSING__"
SESSION_BASELINE_KEY = "__session__"
_CHAT_LOG_HEADER = "# Project Chat Log\n\nUser messages logged with timestamps.\n\n"
_OLD_CHAT_HEADER_RE = re.compile(r"^\*\*\[([0-9-]{10} [0-9:]{8} UTC)\]\*\*(.*)$")
_NEW_CHAT_HEADER_RE = re.compile(r"^\*\*\[M(\d{3,})\]\*\* ")


def resolve_session_id() -> str:
    """Best available session ID for Codex hook scripts.

    Priority:
    1. PLAYBOOK_SESSION_ID — set by bin/playbook-codex wrapper (may not survive sandbox)
    2. CODEX_THREAD_ID — native Codex env var, stable per session, always present
    3. pid-{ppid} — parent process PID (the Codex process that spawned this hook)
    """
    import os as _os
    return (
        _os.environ.get("PLAYBOOK_SESSION_ID")
        or _os.environ.get("CODEX_THREAD_ID")
        or f"pid-{_os.getppid()}"
    )


def codex_config_path(home_dir: Path | None = None) -> Path:
    """Return the global Codex config.toml path."""
    base = home_dir if home_dir is not None else Path.home()
    return base / ".codex" / "config.toml"


def enable_codex_hooks_feature(config_path: Path) -> bool:
    """Ensure [features] codex_hooks = true exists, preserving unrelated content.

    Returns True when the file content changed.
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        config_path.write_text("[features]\ncodex_hooks = true\n", encoding="utf-8")
        return True

    original = config_path.read_text(encoding="utf-8")
    lines = original.splitlines()

    features_start = None
    features_end = len(lines)
    for idx, line in enumerate(lines):
        if line.strip() == "[features]":
            features_start = idx
            for j in range(idx + 1, len(lines)):
                candidate = lines[j].strip()
                if (
                    candidate.startswith("[")
                    and candidate.endswith("]")
                    and not candidate.startswith("[[")
                    and "=" not in candidate
                ):
                    features_end = j
                    break
            break

    if features_start is None:
        new_text = original
        if new_text and not new_text.endswith("\n"):
            new_text += "\n"
        if new_text:
            new_text += "\n"
        new_text += "[features]\ncodex_hooks = true\n"
    else:
        updated = list(lines)
        found_key = False
        for idx in range(features_start + 1, features_end):
            stripped = updated[idx].strip()
            if stripped.startswith("codex_hooks"):
                updated[idx] = "codex_hooks = true"
                found_key = True
                break
        if not found_key:
            updated.insert(features_end, "codex_hooks = true")
        new_text = "\n".join(updated)
        if original.endswith("\n"):
            new_text += "\n"

    if new_text == original:
        return False
    config_path.write_text(new_text, encoding="utf-8")
    return True


def playbook_scripts_dir() -> Path:
    """Resolve the canonical scripts/ directory for this Playbook install."""
    here = Path(__file__).resolve()
    if here.parent.parent.name == "src":
        return here.parent.parent.parent / "scripts"
    if here.parent.parent.name == "lib" and here.parent.parent.parent.name == "scripts":
        return here.parent.parent.parent
    if here.parent.name == "provider" and (here.parent.parent / "scripts").exists():
        return here.parent.parent / "scripts"
    raise RuntimeError(f"Cannot resolve Playbook scripts directory from {here}")


def _command_for(script_name: str) -> str:
    script_path = playbook_scripts_dir() / script_name
    return f"python3 {shlex.quote(str(script_path))}"


def _playbook_hook_entry(script_name: str) -> dict:
    return {
        "hooks": [
            {
                "type": "command",
                "command": _command_for(script_name),
                "timeout": HOOK_TIMEOUT_MS,
            }
        ]
    }


def render_playbook_hooks() -> dict:
    """Return the Playbook-owned Codex hooks.json fragment."""
    return {
        "hooks": {
            "UserPromptSubmit": [
                _playbook_hook_entry("codex-user-prompt-hook"),
            ],
            "Stop": [
                _playbook_hook_entry("codex-stop-hook"),
            ],
        }
    }


def merge_hooks(existing: dict, additions: dict) -> dict:
    """Merge Playbook hook entries into an existing hooks.json document."""
    merged = json.loads(json.dumps(existing or {}))
    hooks = merged.setdefault("hooks", {})

    for event_name, new_entries in additions.get("hooks", {}).items():
        event_entries = hooks.setdefault(event_name, [])
        existing_commands = {
            hook.get("command")
            for entry in event_entries
            for hook in entry.get("hooks", [])
            if isinstance(hook, dict)
        }
        for entry in new_entries:
            commands = [
                hook.get("command")
                for hook in entry.get("hooks", [])
                if isinstance(hook, dict)
            ]
            if any(command in existing_commands for command in commands):
                continue
            event_entries.append(entry)
    return merged


def install_project_hooks(project_root: Path) -> Path:
    """Write or merge repo-local .codex/hooks.json for Playbook."""
    hooks_dir = project_root / ".codex"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hooks_path = hooks_dir / "hooks.json"

    existing: dict
    if hooks_path.exists():
        existing = json.loads(hooks_path.read_text(encoding="utf-8"))
    else:
        existing = {}

    merged = merge_hooks(existing, render_playbook_hooks())
    hooks_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return hooks_path


def current_state_file(project_root: Path, session_id: str) -> Path:
    return project_root / ".agent" / "sessions" / session_id / "current_state"


def has_active_task(project_root: Path, session_id: str) -> bool:
    state_file = current_state_file(project_root, session_id)
    if not state_file.exists():
        return False
    try:
        return bool(state_file.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def _baseline_key(turn_id: str | None) -> str:
    if not turn_id:
        return SESSION_BASELINE_KEY
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in turn_id)


def _turn_baseline_file(project_root: Path, session_id: str, turn_id: str | None) -> Path:
    safe_turn_id = _baseline_key(turn_id)
    session_dir = project_root / ".agent" / "sessions" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir / f"codex-dirty-baseline-{safe_turn_id}.json"


def _chat_log_path(project_root: Path) -> Path:
    return project_root / ".agent" / "chat_log.md"


def _chat_counter_path(project_root: Path) -> Path:
    return project_root / ".agent" / "chat_log_counter"


def _session_counter_path(project_root: Path, session_id: str) -> Path:
    return project_root / ".agent" / "sessions" / session_id / "counters"


def _agent_dir_writable(project_root: Path) -> bool:
    agent_dir = project_root / ".agent"
    return agent_dir.is_dir() and agent_dir.exists() and os.access(agent_dir, os.W_OK)


def _normalize_prompt(prompt: str) -> str:
    text = prompt.replace("\n", " ")
    text = re.sub(r" +", " ", text)
    text = re.sub(r"<ide_opened_file>[^<]*</ide_opened_file>", "", text)
    text = re.sub(r"<ide_selection>[^<]*</ide_selection>", "", text)
    text = text.strip()

    max_len = 500
    if len(text) > max_len:
        removed = len(text) - max_len
        text = f"{text[:max_len]}...[{removed} chars removed]"
    return text


def _migrate_chat_log_if_needed(log_path: Path, counter_path: Path) -> None:
    if not log_path.exists():
        return
    original = log_path.read_text(encoding="utf-8")
    if not original.strip():
        return
    if any(_NEW_CHAT_HEADER_RE.match(line) for line in original.splitlines()):
        return
    if not any(_OLD_CHAT_HEADER_RE.match(line) for line in original.splitlines()):
        return

    msg_num = 0
    new_lines: list[str] = []
    for line in original.splitlines():
        match = _OLD_CHAT_HEADER_RE.match(line)
        if match:
            msg_num += 1
            suffix = match.group(2)
            new_lines.append(f"**[M{msg_num:03d}]** [{match.group(1)}]{suffix}")
        else:
            new_lines.append(line)
    new_text = "\n".join(new_lines)
    if original.endswith("\n"):
        new_text += "\n"
    log_path.write_text(new_text, encoding="utf-8")
    counter_path.write_text(f"{msg_num}\n", encoding="utf-8")


def _current_chat_counter(log_path: Path, counter_path: Path) -> int:
    if counter_path.exists():
        try:
            return int(counter_path.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            pass

    highest = 0
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            match = _NEW_CHAT_HEADER_RE.match(line)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest


def reset_session_counters(project_root: Path, session_id: str) -> Path:
    counter_path = _session_counter_path(project_root, session_id)
    counter_path.parent.mkdir(parents=True, exist_ok=True)

    preserved: list[str] = []
    if counter_path.exists():
        for line in counter_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("gate_"):
                preserved.append(line)

    lines = ["tools=0", "writes=0", *preserved]
    counter_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return counter_path


def append_prompt_to_chat_log(
    project_root: Path,
    session_id: str,
    prompt: str | None,
    *,
    timestamp: dt.datetime | None = None,
) -> bool:
    """Append a Codex UserPromptSubmit prompt to .agent/chat_log.md.

    Returns True when a non-empty prompt was logged, False when logging was
    intentionally skipped (e.g. empty prompt or non-writable .agent/).
    """
    if not _agent_dir_writable(project_root):
        return False

    user_message = _normalize_prompt(prompt or "")
    if not user_message:
        return False

    log_path = _chat_log_path(project_root)
    counter_path = _chat_counter_path(project_root)

    if not log_path.exists():
        log_path.write_text(_CHAT_LOG_HEADER, encoding="utf-8")

    _migrate_chat_log_if_needed(log_path, counter_path)

    ts = (timestamp or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%d %H:%M:%S UTC")
    current = _current_chat_counter(log_path, counter_path)
    next_id = current + 1
    counter_path.write_text(f"{next_id}\n", encoding="utf-8")

    with log_path.open("a", encoding="utf-8") as f:
        f.write("---\n\n")
        f.write(f"**[M{next_id:03d}]** [{ts}] `HOST` (codex)\n\n")
        f.write(f"{user_message}\n\n")

    reset_session_counters(project_root, session_id)
    return True


def _digest_for_file(path: Path) -> str:
    if not path.exists():
        return MISSING_FILE_DIGEST
    if path.is_dir():
        return MISSING_FILE_DIGEST
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _all_code_files_state(project_root: Path) -> dict[str, str]:
    """Fallback snapshot for non-Git projects: hash all code files under the repo."""
    state: dict[str, str] = {}
    skip_dirs = {".git", ".agent", ".claude", ".codex", ".pytest_cache", ".hypothesis", "__pycache__"}
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        rel_path = path.relative_to(project_root).as_posix()
        parts = set(rel_path.split("/"))
        if parts & skip_dirs:
            continue
        if not _is_code_file_path(rel_path):
            continue
        state[rel_path] = _digest_for_file(path)
    return state


def code_state(project_root: Path) -> dict[str, str]:
    """Return a snapshot of relevant code changes for the current project.

    In Git repos, only dirty code files are tracked. Outside Git, fall back to
    a full code-file snapshot so no-task steering still works.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(project_root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "-z",
            ],
            capture_output=True,
            check=False,
        )
    except OSError:
        return _all_code_files_state(project_root)

    if result.returncode != 0:
        return _all_code_files_state(project_root)

    state: dict[str, str] = {}
    entries = result.stdout.decode("utf-8", errors="replace").split("\0")
    idx = 0
    while idx < len(entries):
        entry = entries[idx]
        idx += 1
        if not entry:
            continue
        if len(entry) < 4:
            continue
        status = entry[:2]
        rel_path = entry[3:]
        if "R" in status or "C" in status:
            if idx >= len(entries):
                break
            rel_path = entries[idx]
            idx += 1
        rel_path = rel_path.strip()
        if not rel_path or not _is_code_file_path(rel_path):
            continue
        state[rel_path] = _digest_for_file(project_root / rel_path)
    return state


def save_turn_baseline(project_root: Path, session_id: str, turn_id: str | None) -> Path:
    """Persist the starting dirty code state for a Codex turn.

    If turn_id is unavailable, fall back to a session-scoped baseline key.
    """
    baseline_file = _turn_baseline_file(project_root, session_id, turn_id)
    baseline_file.write_text(
        json.dumps(code_state(project_root), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return baseline_file


def load_turn_baseline(project_root: Path, session_id: str, turn_id: str | None) -> dict[str, str] | None:
    baseline_file = _turn_baseline_file(project_root, session_id, turn_id)
    if not baseline_file.exists():
        return None
    try:
        return json.loads(baseline_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def has_new_code_changes(baseline: dict[str, str], current: dict[str, str]) -> bool:
    """Return True when the current dirty-code snapshot differs from the baseline."""
    for path, digest in current.items():
        if path not in baseline:
            return True
        if baseline[path] != digest:
            return True
    return False


def _active_task_stop_decision(project_root: Path, session_id: str) -> dict:
    """Reuse the existing authoritative stop guard for active-task sessions."""
    stop_hook = playbook_scripts_dir() / "stop-hook"
    try:
        result = subprocess.run(
            ["bash", str(stop_hook)],
            cwd=project_root,
            input=json.dumps({"session_id": session_id}),
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return {
            "decision": "block",
            "reason": f"Playbook stop guard failed to run: {exc}",
        }

    if result.returncode == 0:
        return {}
    reason = (result.stderr or result.stdout or "Complete all gates before finishing.").strip()
    return {
        "decision": "block",
        "reason": reason,
    }


def stop_decision_for_no_task_code_changes(
    project_root: Path,
    session_id: str,
    turn_id: str | None,
) -> dict:
    """Return the JSON response for Codex Stop hooks.

    Missing turn identifiers degrade to a session-scoped baseline rather than
    disabling enforcement silently.
    """
    if has_active_task(project_root, session_id):
        return _active_task_stop_decision(project_root, session_id)

    baseline = load_turn_baseline(project_root, session_id, turn_id)
    if baseline is None:
        return {}

    current = code_state(project_root)
    if not has_new_code_changes(baseline, current):
        return {}

    changed = sorted(path for path, digest in current.items() if baseline.get(path) != digest)
    changed_preview = ", ".join(changed[:3])
    if len(changed) > 3:
        changed_preview += ", ..."
    reason = (
        "You changed code without an active Playbook task"
        + (f" ({changed_preview})" if changed_preview else "")
        + ". Run `.claude/bin/tasks work <N>` if this belongs to an existing task, "
          "or create one with `.claude/bin/tasks new quick <name> <intent>` before continuing."
    )
    return {
        "decision": "block",
        "reason": reason,
    }
