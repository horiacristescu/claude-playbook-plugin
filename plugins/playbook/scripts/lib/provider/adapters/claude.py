"""
ClaudeAdapter — reference implementation of ProviderAdapter for Claude Code.

This is the adapter every other provider is measured against. Claude Code
provides the richest hook surface: all five capability flags are True.

Session identity: session_id is injected by Claude Code into every hook's
stdin payload under the key "session_id". No PID-walk or SQLite needed.

Chat log capture: reads session JSONL at
    ~/.claude/projects/<project-slug>/<session_id>.jsonl
incrementally by byte offset. Does NOT parse from hook stdin — file-based
capture is provider-portable and survives stdin truncation.

Integration: spec-only in T111. The existing bash hooks (task-gate-hook,
chat-log-hook, stop-hook, state-echo-hook) are the authoritative
implementation. This adapter documents the intended Python interface for T112.
"""

from __future__ import annotations
import json
import os
import subprocess
from pathlib import Path
from typing import Optional

from ..adapter import ProviderAdapter
from ..capabilities import ProviderCapabilities, SessionFacts
from ..events import MessageEvent, ToolEvent, StopEvent
from ..policy import Decision


class ClaudeAdapter(ProviderAdapter):
    """Reference provider adapter for Claude Code (claude CLI)."""

    # Noise patterns to skip when reading session JSONL
    _NOISE_PREFIXES = (
        "<command-name>",
        "<task-notification>",
        "<system-reminder>",
        "[Request interrupted by user]",
    )

    def __init__(self, session_id: str, project_root: Path) -> None:
        self._session_id = session_id
        self._project_root = project_root

    # ── Identity ─────────────────────────────────────────────────────────────

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def project_root(self) -> Path:
        return self._project_root

    # ── Bootstrap ────────────────────────────────────────────────────────────

    def bootstrap_file_name(self) -> str:
        return "CLAUDE.md"

    def install_bootstrap(self, project_root: Path) -> None:
        """No-op: CLAUDE.md is managed by `tasks init` separately."""

    # ── Hooks ─────────────────────────────────────────────────────────────────

    def install_hooks(self, project_root: Path) -> None:
        """Hook entries are written to .claude/settings.json by tasks init.
        Implementation deferred to T112 (no hook changes in T111)."""

    def uninstall_hooks(self, project_root: Path) -> None:
        """Remove Playbook entries from .claude/settings.json hooks.
        Implementation deferred to T112."""

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def launch_interactive(self, project_root: Path, **kwargs) -> int:
        """Launch `claude` with PLAYBOOK_SESSION_ID pre-set."""
        import uuid
        env = os.environ.copy()
        env["PLAYBOOK_SESSION_ID"] = self._session_id or str(uuid.uuid4())
        env["PLAYBOOK_PROJECT_ROOT"] = str(project_root)
        result = subprocess.run(["claude"], cwd=project_root, env=env, **kwargs)
        return result.returncode

    def launch_headless(self, project_root: Path, prompt: str, **kwargs) -> str:
        """Run `claude --print` for a single non-interactive prompt."""
        import uuid
        env = os.environ.copy()
        env["PLAYBOOK_SESSION_ID"] = self._session_id or str(uuid.uuid4())
        env["PLAYBOOK_PROJECT_ROOT"] = str(project_root)
        result = subprocess.run(
            ["claude", "--print", prompt],
            cwd=project_root, env=env, capture_output=True, text=True, **kwargs
        )
        return result.stdout

    # ── Capabilities ─────────────────────────────────────────────────────────

    def detect_capabilities(self) -> ProviderCapabilities:
        """All Claude Code capabilities are True. Validate session log base exists."""
        log_base = self._session_log_base()
        return ProviderCapabilities(
            provider="claude",
            has_user_prompt_hook=True,
            has_pre_tool_hook=True,
            has_post_tool_hook=True,
            has_stop_hook=True,
            session_id_in_payload=True,
            session_log_format="jsonl",
            session_log_base=log_base if log_base and log_base.exists() else None,
        )

    # ── Chat log ─────────────────────────────────────────────────────────────

    def session_log_path(self) -> Optional[Path]:
        """~/.claude/projects/<slug>/<session_id>.jsonl"""
        base = self._session_log_base()
        if base is None:
            return None
        path = base / f"{self._session_id}.jsonl"
        return path if path.exists() else None

    def read_new_messages(self, since_offset: int) -> tuple[list[str], int]:
        """Read user messages from session JSONL since byte offset.

        Filters:
        - isMeta=True records (local command output injections)
        - content starting with noise prefixes (slash commands, task-notifications)
        - empty content after stripping
        """
        log_path = self.session_log_path()
        if log_path is None:
            return [], since_offset

        messages: list[str] = []
        new_offset = since_offset

        try:
            with open(log_path, "rb") as f:
                f.seek(since_offset)
                for raw_line in f:
                    new_offset += len(raw_line)
                    try:
                        obj = json.loads(raw_line.decode("utf-8", errors="replace"))
                    except json.JSONDecodeError:
                        continue

                    if obj.get("type") != "user":
                        continue
                    if obj.get("isMeta"):
                        continue

                    content = obj.get("message", {}).get("content", "")
                    if isinstance(content, list):
                        parts = [
                            c.get("text", "")
                            for c in content
                            if isinstance(c, dict) and c.get("type") == "text"
                        ]
                        text = " ".join(parts).strip()
                    else:
                        text = str(content).strip()

                    if not text:
                        continue
                    if any(text.startswith(p) for p in self._NOISE_PREFIXES):
                        continue

                    messages.append(text)
        except OSError:
            pass

        return messages, new_offset

    # ── Internal ─────────────────────────────────────────────────────────────

    def _session_log_base(self) -> Optional[Path]:
        """Compute ~/.claude/projects/<slug>/ from project root."""
        slug = str(self._project_root).replace("/", "-")
        base = Path.home() / ".claude" / "projects" / slug
        return base

    @classmethod
    def from_hook_stdin(cls, stdin_json: dict, project_root: Path) -> "ClaudeAdapter":
        """Construct adapter from a hook's parsed stdin payload.

        Usage in hook scripts (once wired in T112):
            import json, sys
            payload = json.load(sys.stdin)
            adapter = ClaudeAdapter.from_hook_stdin(payload, find_project_root())
        """
        session_id = stdin_json.get("session_id", "default")
        return cls(session_id=session_id, project_root=project_root)
