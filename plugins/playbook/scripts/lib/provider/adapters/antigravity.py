"""
AntigravityAdapter — provider adapter for Google's Antigravity CLI (`agy`).

agy v1.0.2 (Go-based, brew cask) replaces the legacy `gemini` binary
(sunsets 2026-06-18). It stores state under ~/.gemini/antigravity/ — bootstrap
file is GEMINI.md (auto-loaded by agy from project cwd, same convention as
~/.gemini/GEMINI.md at user scope).

Hook surface: agy v1.0.2 has a Claude-compatible plugin loader that accepts
PreToolUse / PostToolUse / UserPromptSubmit / Stop hooks via project-local
plugin manifests. install_hooks writes the manifest (T134 W5a).

Session identity: no AGY_SESSION_ID env var. Resolution order:
  1. $PLAYBOOK_SESSION_ID (set by scripts/playbook-agy wrapper — preferred)
  2. PID-walk fallback: find 'agy' in parent process chain, use pid-<N>

Session transcript: JSONL at
    ~/.gemini/antigravity/brain/<uuid>/.system_generated/logs/transcript.jsonl
Records of interest: source=USER_EXPLICIT, type=USER_INPUT — content wrapped
in <USER_REQUEST>...</USER_REQUEST>, optionally followed by <ADDITIONAL_METADATA>
and <USER_SETTINGS_CHANGE> blocks.

Panel-review participation: single variant (None) — agy v1.0.2's argparser
rejects -m and --model outright (probed and confirmed; the LLM-suggested
~/.config/antigravity/config.toml profile mechanism does not exist). Single
judge uses whatever model the user has set in agy's UI (Gemini 3.5 Flash by
default). When upstream ships -m, switch to ["gemini-3.5-flash", "gemini-3.1-pro"].
"""

from __future__ import annotations
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from ..adapter import ProviderAdapter
from ..capabilities import ProviderCapabilities, SessionFacts
from ..policy import Decision


_USER_REQUEST_RE = re.compile(
    r"<USER_REQUEST>(.*?)</USER_REQUEST>",
    re.DOTALL,
)


class AntigravityAdapter(ProviderAdapter):
    """Provider adapter for Antigravity CLI (`agy`)."""

    _BRAIN_DIR = Path.home() / ".gemini" / "antigravity" / "brain"

    def __init__(self, session_id: str, project_root: Path) -> None:
        self._session_id = session_id
        self._project_root = project_root
        self._transcript_path: Optional[Path] = None  # cached after first lookup

    # ── CLI identity ─────────────────────────────────────────────────────────

    @classmethod
    def binary_name(cls) -> str:
        return "agy"

    @classmethod
    def panel_variants(cls) -> list[Optional[str]]:
        # agy v1.0.2 has no -m flag; single judge uses UI-selected model.
        # When upstream ships -m: return ["gemini-3.5-flash", "gemini-3.1-pro"].
        return [None]

    def run_headless_judge(
        self,
        prompt: str,
        model: Optional[str],
        system_context: str,
        *,
        web_search: bool,
        timeout_secs: int,
    ) -> str:
        import shutil
        if not shutil.which(self.binary_name()):
            return f"(error: {self.binary_name()} not found on PATH)"
        full_prompt = f"{system_context}\n\n---\n\n{prompt}"
        # agy v1.0.2 quirks:
        #  - takes prompt via --print/-p flag (not stdin)
        #  - --print mode ignores cwd; needs --add-dir to expose the project tree
        #  - --print-timeout accepts Go-style duration strings (e.g. "300s")
        # Bypass flag (--dangerously-skip-permissions) prepended by sandbox.
        agent_args = [
            "--add-dir", str(self._project_root),
            "--print", full_prompt,
            "--print-timeout", f"{timeout_secs}s",
        ]
        env = os.environ.copy()
        env["PLAYBOOK_SESSION_ID"] = self._session_id or "judge"
        from provider import sandbox as _sandbox
        result = _sandbox.run(
            "agy", agent_args,
            project_root=self._project_root,
            env=env,
            capture_output=True, text=True, timeout=timeout_secs + 30,
        )
        return result.stdout or "(no output)"

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
        """Write GEMINI.md teaching agy the Playbook workflow.

        agy auto-loads ~/.gemini/GEMINI.md at user scope; project-local
        GEMINI.md is read by agy when run from project cwd. Does not
        overwrite an existing GEMINI.md.
        """
        from tasks.template import antigravity_md_template
        target = project_root / "GEMINI.md"
        if not target.exists():
            target.write_text(antigravity_md_template(), encoding="utf-8")

    # ── Hooks ─────────────────────────────────────────────────────────────────

    _PLUGIN_NAME = "claude-playbook"

    def install_hooks(self, project_root: Path) -> None:
        """Install Playbook hooks globally with agy via plugin manifest.

        agy plugins live in ~/.gemini/config/plugins/<name>/ and must be registered
        via `agy plugin install <src>` — direct file writes are not picked up.
        This method builds a cached manifest in ~/.cache/claude-playbook/agy-plugin/
        then invokes `agy plugin install` to register it globally. Idempotent —
        re-install if the plugin already exists (refreshes hook script paths).
        """
        import shutil
        agy_bin = shutil.which("agy")
        if not agy_bin:
            print("  agy plugin   skipped: 'agy' not on PATH")
            return

        scripts_dir = self._resolve_playbook_scripts_dir()
        if scripts_dir is None:
            print("  agy plugin   skipped: could not resolve Playbook scripts dir")
            return

        cache_dir = self._build_plugin_manifest(scripts_dir)
        self._register_with_agy(agy_bin, cache_dir)

    def uninstall_hooks(self, project_root: Path) -> None:
        """Remove Playbook agy plugin registration."""
        import shutil
        agy_bin = shutil.which("agy")
        if not agy_bin:
            return
        subprocess.run(
            [agy_bin, "plugin", "uninstall", self._PLUGIN_NAME],
            capture_output=True, text=True,
        )

    def _resolve_playbook_scripts_dir(self) -> Optional[Path]:
        """Locate the directory containing Playbook hook scripts.

        Resolution order:
          1. $CLAUDE_PLUGIN_ROOT/scripts (set by Claude plugin loader)
          2. Walk up from this file: src/provider/adapters/antigravity.py
             → <repo>/scripts when running from the dev checkout.
        """
        env_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
        if env_root:
            candidate = Path(env_root) / "scripts"
            if candidate.exists():
                return candidate
        # Walk up from src/provider/adapters/antigravity.py
        here = Path(__file__).resolve()
        # adapters → provider → src → repo root
        for parent in here.parents:
            candidate = parent / "scripts"
            if (candidate / "task-gate-hook").exists():
                return candidate
        return None

    def _build_plugin_manifest(self, scripts_dir: Path) -> Path:
        """Write the agy plugin manifest under ~/.cache/claude-playbook/agy-plugin/.

        Returns the manifest root path suitable for `agy plugin install <path>`.
        """
        from tasks.core import VERSION
        cache_dir = Path.home() / ".cache" / "claude-playbook" / "agy-plugin" / self._PLUGIN_NAME
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "plugin.json").write_text(
            json.dumps({"name": self._PLUGIN_NAME, "version": VERSION}, indent=2),
            encoding="utf-8",
        )
        hooks_dir = cache_dir / "hooks"
        hooks_dir.mkdir(exist_ok=True)

        def _entry(script: str, matcher: Optional[str] = None) -> dict:
            entry: dict = {
                "hooks": [{
                    "type": "command",
                    "command": str(scripts_dir / script),
                    "timeout": 5000,
                }],
            }
            if matcher is not None:
                entry["matcher"] = matcher
            return entry

        hooks_doc = {
            "hooks": {
                "PreToolUse":        [_entry("task-gate-hook",  matcher=".*")],
                "PostToolUse":       [_entry("state-echo-hook", matcher=".*")],
                "UserPromptSubmit":  [_entry("chat-log-hook")],
                "Stop":              [_entry("stop-hook")],
            }
        }
        (hooks_dir / "hooks.json").write_text(
            json.dumps(hooks_doc, indent=2), encoding="utf-8",
        )
        return cache_dir

    def _register_with_agy(self, agy_bin: str, cache_dir: Path) -> None:
        """Invoke `agy plugin install <cache_dir>`. Idempotent w.r.t. agy's state."""
        # Uninstall first to guarantee a refresh (script paths may have changed
        # since the previous install). Ignore errors — plugin may not exist yet.
        subprocess.run(
            [agy_bin, "plugin", "uninstall", self._PLUGIN_NAME],
            capture_output=True, text=True,
        )
        result = subprocess.run(
            [agy_bin, "plugin", "install", str(cache_dir)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"  agy plugin   installed ({self._PLUGIN_NAME})")
        else:
            print(f"  agy plugin   install failed: {result.stderr.strip()}")

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def launch_interactive(self, project_root: Path, **kwargs) -> int:
        """Launch `agy` TUI with PLAYBOOK_SESSION_ID pre-set."""
        import uuid
        env = os.environ.copy()
        env["PLAYBOOK_SESSION_ID"] = self._session_id or str(uuid.uuid4())
        env["PLAYBOOK_PROJECT_ROOT"] = str(project_root)
        result = subprocess.run(["agy"], cwd=project_root, env=env, **kwargs)
        return result.returncode

    def launch_headless(self, project_root: Path, prompt: str, **kwargs) -> str:
        """Run `agy --print` for a single non-interactive prompt.

        Uses --add-dir to expose the project tree — agy v1.0.2 --print mode
        runs in its own scratch dir and ignores cwd otherwise.
        """
        import uuid
        env = os.environ.copy()
        env["PLAYBOOK_SESSION_ID"] = self._session_id or str(uuid.uuid4())
        env["PLAYBOOK_PROJECT_ROOT"] = str(project_root)
        result = subprocess.run(
            ["agy", "--add-dir", str(project_root),
             "--print", prompt, "--print-timeout", "300s"],
            cwd=project_root, env=env, capture_output=True, text=True, **kwargs,
        )
        return result.stdout

    # ── Capabilities ─────────────────────────────────────────────────────────

    def detect_capabilities(self) -> ProviderCapabilities:
        """agy v1.0.2: plugin-system hooks (Claude-compatible schema, probed).

        Hook surface confirmed via `agy plugin validate` accepting Claude-shape
        hooks/hooks.json + binary strings (`PreToolUse`, `PostToolUse`, `Stop`,
        `HooksPanel`). Capability flags reflect plugin-installable hooks; actual
        hook firing under agy requires install_hooks (W5a) + smoke test (W6b).
        """
        log_base = self._BRAIN_DIR
        return ProviderCapabilities(
            provider="antigravity",
            has_user_prompt_hook=True,
            has_pre_tool_hook=True,
            has_post_tool_hook=True,
            has_stop_hook=True,
            session_id_in_payload=False,
            session_log_format="jsonl",
            session_log_base=log_base if log_base.exists() else None,
        )

    # ── Chat log ─────────────────────────────────────────────────────────────

    def session_log_path(self) -> Optional[Path]:
        """Find most recent transcript JSONL referencing the project cwd.

        Walks ~/.gemini/antigravity/brain/<uuid>/.system_generated/logs/transcript.jsonl
        and returns the file with newest mtime whose content mentions project_root.
        Verification is content-based (cwd appears in early USER_INPUT or tool_calls)
        because agy doesn't tag transcripts with cwd metadata directly.
        """
        if self._transcript_path is not None:
            return self._transcript_path
        if not self._BRAIN_DIR.exists():
            return None
        cwd_str = str(self._project_root)
        candidates: list[tuple[float, Path]] = []
        for brain_dir in self._BRAIN_DIR.iterdir():
            transcript = brain_dir / ".system_generated" / "logs" / "transcript.jsonl"
            if transcript.exists():
                candidates.append((transcript.stat().st_mtime, transcript))
        candidates.sort(reverse=True)  # newest first
        for _, path in candidates:
            try:
                # Read first ~8KB to check for cwd reference
                with open(path, "rb") as f:
                    head = f.read(8192).decode("utf-8", errors="replace")
                if cwd_str in head:
                    self._transcript_path = path
                    return path
            except OSError:
                continue
        return None

    def read_new_messages(self, since_offset: int) -> tuple[list[str], int]:
        """Read user messages from agy transcript since byte offset.

        Filters: source=USER_EXPLICIT, type=USER_INPUT.
        Cleans: unwraps <USER_REQUEST>...</USER_REQUEST>; strips trailing
        <ADDITIONAL_METADATA> / <USER_SETTINGS_CHANGE> blocks.
        Returns ([], since_offset) if no transcript found.
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
                    if obj.get("source") != "USER_EXPLICIT":
                        continue
                    if obj.get("type") != "USER_INPUT":
                        continue
                    content = obj.get("content", "")
                    if not isinstance(content, str):
                        continue
                    # Prefer the explicit <USER_REQUEST> wrapper when present;
                    # fall back to raw content stripped of trailing metadata blocks.
                    m = _USER_REQUEST_RE.search(content)
                    if m:
                        text = m.group(1).strip()
                    else:
                        # Strip trailing <ADDITIONAL_METADATA>...</ADDITIONAL_METADATA>
                        # and <USER_SETTINGS_CHANGE>...</USER_SETTINGS_CHANGE> blocks.
                        text = re.sub(
                            r"<(ADDITIONAL_METADATA|USER_SETTINGS_CHANGE)>.*?</\1>",
                            "",
                            content,
                            flags=re.DOTALL,
                        ).strip()
                    if text:
                        messages.append(text)
        except OSError:
            pass

        return messages, new_offset

    # ── Class method ─────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls, project_root: Path) -> "AntigravityAdapter":
        """Construct adapter using best available session ID source.

        Priority:
        1. PLAYBOOK_SESSION_ID (set by scripts/playbook-agy wrapper)
        2. PID-walk to find 'agy' parent process
        """
        from .codex import _pid_walk_session_id
        session_id = os.environ.get("PLAYBOOK_SESSION_ID", "")
        if not session_id:
            session_id = _pid_walk_session_id(provider_names=["agy"])
        return cls(session_id=session_id, project_root=project_root)
