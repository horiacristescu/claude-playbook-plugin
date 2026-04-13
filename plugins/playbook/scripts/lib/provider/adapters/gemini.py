"""
GeminiAdapter — capability stub for Google Gemini CLI.

INVESTIGATION STATUS (as of T111): all fields are UNVERIFIED.

What was found:
  ~/.gemini/ contains: settings.json, state.json, oauth_credentials.json
  No session JSONL, no rollout files, no conversation logs found on disk.
  No hook configuration directory found.

What would need to be true for full implementation:
  1. SESSION LOG: Gemini must write conversation JSONL to disk (path TBD).
     Without this, read_new_messages() can only return [].
  2. SCRIPT HOOKS: Gemini must support scriptable hooks (PreToolUse,
     UserPromptSubmit, or AfterAgent equivalent) for enforcement.
     Without this, task discipline is advisory (GEMINI.md instructions only).
  3. SESSION IDENTITY: A stable session ID source must be identified.
     Current fallback: PID-walk for 'gemini' process + wrapper UUID injection.

Bootstrap: GEMINI.md (created by install_bootstrap) teaches task discipline
and points to .claude/bin/tasks. Advisory only until hook model confirmed.

Integration: spec-only stub in T111. Full implementation requires dedicated
Gemini investigation task (T113+).
"""

from __future__ import annotations
import os
import subprocess
from pathlib import Path
from typing import Optional

from ..adapter import ProviderAdapter
from ..capabilities import ProviderCapabilities, SessionFacts
from ..policy import Decision


class GeminiAdapter(ProviderAdapter):
    """Capability stub for Gemini CLI. All capabilities unknown/False until investigated."""

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
        return "GEMINI.md"

    def install_bootstrap(self, project_root: Path) -> None:
        """Write GEMINI.md teaching task discipline (advisory).

        GEMINI.md auto-loading by Gemini is unverified.  Content is advisory
        only until hook model and bootstrap mechanism are confirmed.
        Does not overwrite an existing GEMINI.md.
        """
        from tasks.template import gemini_md_template
        target = project_root / "GEMINI.md"
        if not target.exists():
            target.write_text(gemini_md_template(), encoding="utf-8")

    # ── Hooks ─────────────────────────────────────────────────────────────────

    def install_hooks(self, project_root: Path) -> None:
        """No-op: Gemini hook model is unverified. No hooks to install."""

    def uninstall_hooks(self, project_root: Path) -> None:
        """No-op: no Gemini hooks installed."""

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def launch_interactive(self, project_root: Path, **kwargs) -> int:
        """Launch `gemini` TUI with PLAYBOOK_SESSION_ID pre-set."""
        import uuid
        env = os.environ.copy()
        env["PLAYBOOK_SESSION_ID"] = self._session_id or str(uuid.uuid4())
        env["PLAYBOOK_PROJECT_ROOT"] = str(project_root)
        result = subprocess.run(["gemini"], cwd=project_root, env=env, **kwargs)
        return result.returncode

    def launch_headless(self, project_root: Path, prompt: str, **kwargs) -> str:
        """Run `gemini` for a single non-interactive prompt (flag unverified)."""
        import uuid
        env = os.environ.copy()
        env["PLAYBOOK_SESSION_ID"] = self._session_id or str(uuid.uuid4())
        env["PLAYBOOK_PROJECT_ROOT"] = str(project_root)
        # TODO: verify correct headless flag for Gemini CLI
        result = subprocess.run(
            ["gemini", "--print", prompt],
            cwd=project_root, env=env, capture_output=True, text=True, **kwargs
        )
        return result.stdout

    # ── Capabilities ─────────────────────────────────────────────────────────

    def detect_capabilities(self) -> ProviderCapabilities:
        """All capability flags False/unknown — Gemini hook model unverified.

        session_log_format="none": no session log files found on disk at ~/.gemini/.
        session_log_base=None: no log directory identified.
        All has_* flags False: no script hooks observed.

        Requires dedicated investigation to determine actual capabilities.
        """
        return ProviderCapabilities(
            provider="gemini",
            has_user_prompt_hook=False,
            has_pre_tool_hook=False,
            has_post_tool_hook=False,
            has_stop_hook=False,
            session_id_in_payload=False,
            session_log_format="none",
            session_log_base=None,
        )

    # ── Chat log ─────────────────────────────────────────────────────────────

    def session_log_path(self) -> Optional[Path]:
        """Always None — no session log format identified for Gemini."""
        return None

    def read_new_messages(self, since_offset: int) -> tuple[list[str], int]:
        """Always returns empty — no session log available."""
        return [], since_offset

    # ── Class method ─────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls, project_root: Path) -> "GeminiAdapter":
        """Construct adapter using best available session ID source.

        Priority:
        1. PLAYBOOK_SESSION_ID (set by bin/playbook-gemini wrapper)
        2. PID-walk to find 'gemini' parent process
        """
        from .codex import _pid_walk_session_id
        session_id = os.environ.get("PLAYBOOK_SESSION_ID", "")
        if not session_id:
            session_id = _pid_walk_session_id(provider_names=["gemini"])
        return cls(session_id=session_id, project_root=project_root)
