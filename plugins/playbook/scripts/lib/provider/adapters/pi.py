"""
PiAdapter — provider adapter for the `pi` CLI (https://github.com/pi-os/pi).

In Playbook, pi is driven against a local LLM served by oMLX
(https://omlx.app/) — typically Qwen3.6-35B-A3B-4bit on Apple Silicon at
127.0.0.1:8000. The combination is useful for **volume work that is not time
sensitive** (PDF corpus labeling, batch evals) where hosted CLIs would be
slow or expensive. Useful up to ~100K context; beyond that, prefer claude
or codex.

Bootstrap file: AGENTS.md (pi reads it the same way codex does).

Hook surface: pi v0.73.0 has no plugin or hook system — install_hooks is a
no-op and pi sessions rely on the outer sandbox for containment, not on
in-process hooks. When upstream ships hooks, wire them here.

Headless invocation: `pi -p "<prompt>"` (or `--print`). Optional flags we
care about: `--provider <name>` (default `google`; we usually want `oss`
to hit the local oMLX endpoint), `--model <id>`, `--no-context-files`
(disable AGENTS.md/CLAUDE.md auto-load — useful for clean judge runs),
`--no-tools` (disable tools entirely), `--append-system-prompt`.

Pi reads its provider/model config from `~/.pi/agent/models.json` +
`settings.json` — oMLX writes these files before exec'ing pi. So a
production pi invocation in Playbook typically goes through `omlx launch
pi --` so omlx owns the config-file lifecycle.

Panel-review participation: single variant (None) — oMLX serves one model
at a time, so there's no point cycling models per panel slot.
"""

from __future__ import annotations
import os
import subprocess
from pathlib import Path
from typing import Optional

from ..adapter import ProviderAdapter
from ..capabilities import ProviderCapabilities


class PiAdapter(ProviderAdapter):
    """Provider adapter for the `pi` CLI driving a local oMLX-hosted model
    OR a hosted provider (OpenRouter) via pi's `--provider` flag."""

    #: Practical context limit before the LOCAL pi+Qwen variant degrades
    #: noticeably (per user report). Hosted variants (e.g. or-pi → DeepSeek)
    #: have their own larger limits set by the upstream provider.
    CONTEXT_LIMIT_TOKENS: int = 100_000

    #: Panel-variant label → (pi `--provider` value, pi `--model` value or None).
    #: `None` is the default local variant (oMLX serving Qwen at 127.0.0.1:8000).
    #: Add new hosted variants here — `panel_variants()` decides availability.
    _VARIANT_MAP: dict[Optional[str], tuple[str, Optional[str]]] = {
        None: ("oss", None),
        "or-pi": ("openrouter", "deepseek/deepseek-v4-flash"),
    }

    def __init__(self, session_id: str, project_root: Path) -> None:
        self._session_id = session_id
        self._project_root = project_root

    # ── CLI identity ─────────────────────────────────────────────────────────

    @classmethod
    def binary_name(cls) -> str:
        return "pi"

    @classmethod
    def is_available(cls) -> bool:
        """pi can be driven directly OR via `omlx launch pi` — either suffices.
        Override default `which(binary_name())` so panel discovery doesn't
        silently drop pi when only omlx is installed.
        """
        import shutil
        return shutil.which("pi") is not None or shutil.which("omlx") is not None

    @classmethod
    def panel_variants(cls) -> list[Optional[str]]:
        # Local variant (None) always available if pi is installed.
        # Hosted variants gated on the relevant API key being in env —
        # the agent inherits env from the parent shell, so a key in `bash`
        # is visible to pi without any config file.
        variants: list[Optional[str]] = [None]
        if os.environ.get("OPENROUTER_API_KEY"):
            variants.append("or-pi")
        return variants

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
        # Prefer omlx (handles config-file setup via os.execvpe), fall back to
        # direct pi if omlx is missing.
        if shutil.which("omlx") is None and shutil.which("pi") is None:
            return f"(error: neither omlx nor pi found on PATH)"
        # Resolve variant label → (--provider, --model). Unknown labels are
        # treated as raw model identifiers routed via openrouter (so callers
        # can pass `provider/model` strings ad-hoc).
        provider, model_id = self._VARIANT_MAP.get(model, ("openrouter", model))
        agent_args = [
            "-p", prompt,
            "--provider", provider,
            "--no-context-files",
            "--append-system-prompt", system_context,
        ]
        if model_id:
            agent_args[2:2] = ["--model", model_id]
        env = os.environ.copy()
        env["PLAYBOOK_SESSION_ID"] = self._session_id or "judge"
        from provider import sandbox as _sandbox
        result = _sandbox.run(
            "pi", agent_args,
            project_root=self._project_root,
            env=env,
            capture_output=True, text=True, timeout=timeout_secs,
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
        # pi auto-loads AGENTS.md from the project cwd (same convention codex
        # uses). Reuse the same content.
        return "AGENTS.md"

    def install_bootstrap(self, project_root: Path) -> None:
        """Write AGENTS.md (shared with codex) if not present."""
        from tasks.template import agents_md_template
        target = project_root / "AGENTS.md"
        if not target.exists():
            target.write_text(agents_md_template(), encoding="utf-8")

    # ── Hooks ─────────────────────────────────────────────────────────────────
    # pi v0.73.0 has no plugin/hook system. install_hooks/uninstall_hooks are
    # no-ops; outer sandbox is the only containment.

    def install_hooks(self, project_root: Path) -> None:
        return None

    def uninstall_hooks(self, project_root: Path) -> None:
        return None

    # ── Launch ───────────────────────────────────────────────────────────────

    def launch_interactive(self, project_root: Path, **kwargs) -> int:
        """Interactive pi session via omlx launch pi (preferred) or direct pi."""
        import shutil
        env = os.environ.copy()
        if shutil.which("omlx"):
            result = subprocess.run(
                ["omlx", "launch", "pi"], cwd=str(project_root), env=env, **kwargs,
            )
        else:
            result = subprocess.run(
                ["pi"], cwd=str(project_root), env=env, **kwargs,
            )
        return result.returncode

    def launch_headless(self, project_root: Path, prompt: str, **kwargs) -> str:
        """Headless one-shot via sandbox."""
        from provider import sandbox as _sandbox
        result = _sandbox.run(
            "pi", ["-p", prompt, "--provider", "oss", "--no-context-files"],
            project_root=project_root,
            capture_output=True, text=True, **kwargs,
        )
        return result.stdout or ""

    # ── Capabilities ────────────────────────────────────────────────────────

    def detect_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider="pi",
            has_user_prompt_hook=False,
            has_pre_tool_hook=False,
            has_post_tool_hook=False,
            has_stop_hook=False,
            session_id_in_payload=False,
            session_log_format="none",
            session_log_base=None,
        )

    # ── Session log / chat log ──────────────────────────────────────────────
    # pi has no on-disk session log we can parse — at least not in v0.73.0.
    # When upstream ships a session log, wire it here.

    def session_log_path(self) -> Optional[Path]:
        return None

    def read_new_messages(self, since_offset: int) -> tuple[list[str], int]:
        return [], since_offset
