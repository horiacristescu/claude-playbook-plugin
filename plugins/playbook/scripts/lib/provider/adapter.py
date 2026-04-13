"""
ProviderAdapter — abstract base class for all provider adapters.

Each concrete adapter (ClaudeAdapter, CodexAdapter, GeminiAdapter) implements
this interface. The policy engine and hook scripts call the interface only —
never provider-specific code directly.

Integration: spec-only in T111. Concrete adapters are stubs.
Full implementations and hook wiring are T112+.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from .capabilities import ProviderCapabilities, SessionFacts
from .events import MessageEvent, ToolEvent, StopEvent
from .policy import Decision


class ProviderAdapter(ABC):
    """
    Contract every provider must satisfy to participate in Playbook enforcement.

    Grouped by concern:
        1. Identity   — session_id, project_root
        2. Bootstrap  — install_bootstrap, bootstrap_file_name
        3. Hooks      — install_hooks, uninstall_hooks
        4. Lifecycle  — launch_interactive, launch_headless
        5. Capability — detect_capabilities
        6. Chat log   — session_log_path, read_new_messages
        7. Hook entry — on_user_message, on_tool_use, on_stop
    """

    # ── 1. Identity ──────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def session_id(self) -> str:
        """Stable unique identifier for this running provider instance.

        Claude: from hook stdin payload (always available).
        Codex:  wrapper-injected UUID, or pid-<N> PID-walk fallback.
        Gemini: same as Codex fallback strategy.

        Must be stable for the entire session lifetime. Different concurrent
        instances of the same provider in the same project must return different IDs.
        """

    @property
    @abstractmethod
    def project_root(self) -> Path:
        """Absolute path to project root (directory containing .agent/tasks/).

        Derived from find_project_root() walk, not $PWD — hooks may fire from
        subdirectories. Never None; raises if project root cannot be found.
        """

    # ── 2. Bootstrap ─────────────────────────────────────────────────────────

    @abstractmethod
    def bootstrap_file_name(self) -> str:
        """Name of the provider's auto-loaded instruction file.

        Claude: "CLAUDE.md"  (exists today)
        Codex:  "AGENTS.md"  (missing today — created by install_bootstrap)
        Gemini: "GEMINI.md"  (missing today — speculative, unverified)
        """

    @abstractmethod
    def install_bootstrap(self, project_root: Path) -> None:
        """Write the provider's instruction file with Playbook workflow.

        Idempotent — safe to re-run. Called by `tasks init`.
        Claude: no-op (CLAUDE.md is managed separately).
        Codex:  writes AGENTS.md teaching task discipline and .claude/bin/tasks.
        Gemini: writes GEMINI.md stub (advisory only until hook model confirmed).
        """

    # ── 3. Hooks ─────────────────────────────────────────────────────────────

    @abstractmethod
    def install_hooks(self, project_root: Path) -> None:
        """Register Playbook hooks with the provider's hook system.

        Claude: writes hooks entries to .claude/settings.json.
        Codex:  enables codex_hooks globally and writes repo-local .codex/hooks.json.
        Gemini: writes .gemini/hooks.json (unverified format — stub only).
        """

    @abstractmethod
    def uninstall_hooks(self, project_root: Path) -> None:
        """Remove Playbook hook registrations."""

    # ── 4. Lifecycle ─────────────────────────────────────────────────────────

    @abstractmethod
    def launch_interactive(self, project_root: Path, **kwargs) -> int:
        """Start the provider in interactive TUI mode (user at terminal).

        Equivalent to running `claude`, `codex`, `gemini` in the project dir,
        but with PLAYBOOK_SESSION_ID and PLAYBOOK_PROJECT_ROOT pre-set.
        Returns the process exit code.
        """

    @abstractmethod
    def launch_headless(self, project_root: Path, prompt: str, **kwargs) -> str:
        """Run the provider for a single non-interactive prompt.

        Used for CI, sub-agents, evals. Returns the agent's response text.
        """

    # ── 5. Capabilities ───────────────────────────────────────────────────────

    @abstractmethod
    def detect_capabilities(self) -> ProviderCapabilities:
        """Probe the environment and return runtime-detected capabilities.

        Called once at session start. Result should be cached by the caller.
        Never called mid-session — capabilities are fixed once detected.
        Never probes the provider network or spawns subprocesses with side effects.
        """

    # ── 6. Chat log ───────────────────────────────────────────────────────────

    @abstractmethod
    def session_log_path(self) -> Optional[Path]:
        """Return path to provider's on-disk session log, or None if unavailable.

        Claude: ~/.claude/projects/<slug>/<session_id>.jsonl
        Codex:  resolved via SQLite state_5.sqlite WHERE cwd=project_root
        Gemini: None (no session log found on disk)
        """

    @abstractmethod
    def read_new_messages(self, since_offset: int) -> tuple[list[str], int]:
        """Read user messages from the session log since byte offset.

        Returns (messages, new_offset). Messages are cleaned text strings —
        noise filtered (isMeta, slash commands, AGENTS.md injections, etc.).
        Content-hash dedup is the caller's responsibility.

        Returns ([], since_offset) if session_log_path() is None.
        """

    # ── 7. Hook entry points ─────────────────────────────────────────────────

    def on_user_message(self, payload: dict) -> Decision:
        """Called from UserPromptSubmit hook (or file-based message delivery).

        Default: delegates to evaluate_message(). Adapters may override to
        parse provider-specific payload format before constructing MessageEvent.
        """
        from .policy import evaluate_message
        from .events import MessageEvent
        import datetime
        caps = self._get_capabilities()
        facts = self._load_session_facts()
        text = payload.get("user_message", payload.get("text", ""))
        event = MessageEvent(text=text, timestamp=datetime.datetime.utcnow())
        return evaluate_message(caps, facts, event)

    def on_tool_use(self, payload: dict) -> Decision:
        """Called from PreToolUse hook. Returns block/allow/warn/skip.

        Default: delegates to evaluate_tool_call(). Not called for providers
        without script-based pre-tool hooks (Codex, Gemini today).
        """
        from .policy import evaluate_tool_call
        from .events import ToolEvent
        caps = self._get_capabilities()
        facts = self._load_session_facts()
        tool_input = payload.get("tool_input", {})
        event = ToolEvent(
            tool_name=payload.get("tool_name", ""),
            tool_input=tool_input,
            file_path=tool_input.get("path", tool_input.get("file_path", "")),
            is_pre=True,
        )
        return evaluate_tool_call(caps, facts, event)

    def on_stop(self, payload: dict) -> Decision:
        """Called from Stop/AfterAgent/session-end hook.

        The universal enforcement point — every provider with any hook support
        must reach this. Returns block to prevent exit on violations.
        """
        from .policy import evaluate_stop
        from .events import StopEvent
        caps = self._get_capabilities()
        facts = self._load_session_facts()
        event = StopEvent(stop_reason=payload.get("stop_reason", ""))
        return evaluate_stop(caps, facts, event)

    # ── Internal ─────────────────────────────────────────────────────────────

    def _get_capabilities(self) -> ProviderCapabilities:
        """Return cached capabilities, detecting on first call.

        detect_capabilities() contract: "called once at session start, result
        cached by caller." This method is the cache — on_user_message/on_tool_use/
        on_stop all call _get_capabilities(), never detect_capabilities() directly.
        """
        if not hasattr(self, "_cached_capabilities"):
            self._cached_capabilities: ProviderCapabilities = self.detect_capabilities()
        return self._cached_capabilities

    def _load_session_facts(self) -> SessionFacts:
        """Load SessionFacts from disk. Called fresh on each hook invocation."""
        state_path = self.project_root / ".agent" / "sessions" / self.session_id / "current_state"
        task_number = None
        if state_path.exists():
            try:
                task_number = int(state_path.read_text().strip())
            except (ValueError, OSError):
                pass
        task_path = None
        if task_number is not None:
            # Locate task.md: match NNN-name where int(NNN) == task_number.
            # Uses int() comparison so "042-foo", "42-foo", "0042-foo" all match 42.
            tasks_dir = self.project_root / ".agent" / "tasks"
            if tasks_dir.exists():
                for d in tasks_dir.iterdir():
                    dash = d.name.find("-")
                    if dash > 0:
                        try:
                            if int(d.name[:dash]) == task_number:
                                task_path = d / "task.md"
                                break
                        except ValueError:
                            pass
        # Load persisted chat log offset for incremental reads
        chat_log_offset = self._load_chat_log_offset()
        return SessionFacts(
            session_id=self.session_id,
            project_root=self.project_root,
            active_task_number=task_number,
            active_task_path=task_path,
            chat_log_offset=chat_log_offset,
        )

    def _load_chat_log_offset(self) -> int:
        """Read persisted byte offset from .agent/sessions/<session_id>/chat_log_offset."""
        offset_path = self.project_root / ".agent" / "sessions" / self.session_id / "chat_log_offset"
        if offset_path.exists():
            try:
                return int(offset_path.read_text().strip())
            except (ValueError, OSError):
                pass
        return 0

    def save_chat_log_offset(self, offset: int) -> None:
        """Persist byte offset so the next hook call resumes from the right position.

        Called by hook scripts after read_new_messages() returns the new offset.
        Without this, each hook call starts from 0 and re-processes all messages.
        """
        offset_path = self.project_root / ".agent" / "sessions" / self.session_id / "chat_log_offset"
        try:
            offset_path.parent.mkdir(parents=True, exist_ok=True)
            offset_path.write_text(str(offset))
        except OSError:
            pass
