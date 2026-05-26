"""Unified sandbox launcher for headless and interactive agent invocations.

Single source of truth for write-containment when running any of the supported
CLI agents (claude, codex, agy, pi). Backends: macOS seatbelt (sandbox-exec) and
Linux bubblewrap (bwrap). Stdlib only.

Callers (cli.py judge dispatch, adapter run_headless_judge, bin/sandbox shim)
import from here; do not re-implement profile generation elsewhere.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# Priority order for default_agent() — first available wins.
_AGENT_ORDER: tuple[str, ...] = ("claude", "codex", "agy", "pi")

# Binary names per agent. Pi may resolve via the `omlx` launcher when `pi` itself
# is absent (omlx launches pi via os.execvpe, inheriting our sandbox).
_AGENT_BINARIES: dict[str, tuple[str, ...]] = {
    "claude": ("claude",),
    "codex": ("codex",),
    "agy": ("agy",),
    "pi": ("pi", "omlx"),
}

# Per-agent bypass-flag injection. These are appended to argv at the top level
# (i.e. before any subcommand). Codex's bypass is technically an `exec`
# subcommand flag — callers building codex argv must insert it AFTER the
# `exec` token themselves; bypass_args() returns the flag string for the map,
# the launcher routes accordingly.
_BYPASS_FLAGS: dict[str, list[str]] = {
    "claude": ["--dangerously-skip-permissions"],
    "agy": ["--dangerously-skip-permissions"],
    "codex": ["--dangerously-bypass-approvals-and-sandbox"],
    "pi": [],
}

# Home-relative directories that must be writable across all agents.
# Union of: claude state, codex state, gemini/agy transcripts, omlx server data,
# pi config, generic tool caches, macOS Library.
_HOME_RW_SUBPATHS: tuple[str, ...] = (
    ".codex",
    ".gemini",
    ".omlx",
    ".pi",
    ".cache",
    ".local",
    "Library",
)

# Top-level paths (non-home) that must be writable.
_SYSTEM_RW_PATHS: tuple[str, ...] = (
    "/tmp",
    "/private/tmp",
    "/var/folders",
    "/private/var/folders",
    "/dev",
)


@dataclass(frozen=True)
class AgentInfo:
    name: str
    binary_path: str | None  # absolute path if found, else None
    via: str | None          # "direct" | "omlx" | None


def detect_agents() -> dict[str, AgentInfo]:
    """Probe which agent CLIs are installed. Returns map agent → AgentInfo."""
    out: dict[str, AgentInfo] = {}
    for agent, binaries in _AGENT_BINARIES.items():
        found: str | None = None
        via: str | None = None
        for bin_name in binaries:
            path = shutil.which(bin_name)
            if path:
                found = path
                via = "direct" if bin_name == agent else bin_name
                break
        out[agent] = AgentInfo(name=agent, binary_path=found, via=via)
    return out


def default_agent() -> str:
    """First available agent by priority. Raises if none installed."""
    agents = detect_agents()
    for name in _AGENT_ORDER:
        if agents[name].binary_path:
            return name
    raise RuntimeError(
        "No supported agent found on PATH (looked for: "
        + ", ".join(sum(_AGENT_BINARIES.values(), ()))
        + ")"
    )


def is_sandboxed() -> bool:
    """True if already inside our sandbox — skip re-wrapping to avoid nesting.

    Sole signal: PLAYBOOK_SANDBOXED=1 in env. `run()` always exports this
    before exec'ing the child, so any nested invocation always sees it. The
    earlier cwd-prefix heuristic (`/tmp/eval-`) was dropped because it could
    skip wrapping when the launching process's cwd happened to be in the
    eval tree but the target project_root was elsewhere.
    """
    return os.environ.get("PLAYBOOK_SANDBOXED") == "1"


def bypass_args(agent: str) -> list[str]:
    """Per-agent bypass-flag injection (copy — callers may mutate)."""
    if agent not in _BYPASS_FLAGS:
        raise ValueError(f"Unknown agent: {agent!r}")
    return list(_BYPASS_FLAGS[agent])


def _normalize_rw(extra_rw: Iterable[str] | None) -> list[str]:
    if not extra_rw:
        return []
    return [str(Path(p).resolve()) for p in extra_rw]


def build_seatbelt_profile(
    project_dir: Path | str,
    git_dir: Path | str | None,
    extra_rw: Iterable[str] | None = None,
) -> str:
    """Generate a macOS seatbelt profile: allow default, deny writes except
    project_dir, system temp/dev, per-agent home subpaths, and extra_rw paths.
    Then deny .git writes within the project.
    """
    project = str(Path(project_dir).resolve())
    home = str(Path.home())
    rw_paths = _normalize_rw(extra_rw)

    require_nots: list[str] = [f'        (require-not (subpath "{project}"))']
    for sys_path in _SYSTEM_RW_PATHS:
        require_nots.append(f'        (require-not (subpath "{sys_path}"))')
    # ~/.claude and ~/.claude.json* — regex covers both.
    require_nots.append(
        f'        (require-not (regex #"^{home}/\\.claude"))'
    )
    for sub in _HOME_RW_SUBPATHS:
        require_nots.append(
            f'        (require-not (subpath "{home}/{sub}"))'
        )
    for rw in rw_paths:
        require_nots.append(f'        (require-not (subpath "{rw}"))')

    profile_lines = [
        "(version 1)",
        "(allow default)",
        "(deny file-write*",
        "    (require-all",
        *require_nots,
        "    )",
        ")",
    ]
    if git_dir:
        git_resolved = str(Path(git_dir).resolve())
        profile_lines.append(f'(deny file-write* (subpath "{git_resolved}"))')

    return "\n".join(profile_lines)


def build_bwrap_argv(
    project_dir: Path | str,
    git_dir: Path | str | None,
    target_argv: list[str],
    extra_rw: Iterable[str] | None = None,
) -> list[str]:
    """Generate the bwrap argv: read-only root, bind project + tmp + per-agent
    home subpaths read-write, bind git_dir read-only.
    """
    project = str(Path(project_dir).resolve())
    home = Path.home()
    rw_paths = _normalize_rw(extra_rw)

    argv = ["bwrap", "--ro-bind", "/", "/", "--proc", "/proc", "--dev", "/dev"]
    argv += ["--bind", project, project, "--bind", "/tmp", "/tmp"]

    write_log_dir = home / ".local" / "share" / "playbook"
    write_log_dir.mkdir(parents=True, exist_ok=True)
    argv += ["--bind", str(write_log_dir), str(write_log_dir)]

    if git_dir:
        git_resolved = str(Path(git_dir).resolve())
        argv += ["--ro-bind", git_resolved, git_resolved]

    # Pre-create + bind per-agent home subpaths.
    for sub in (".claude", *_HOME_RW_SUBPATHS):
        target = home / sub
        target.mkdir(parents=True, exist_ok=True)
        argv += ["--bind", str(target), str(target)]

    for rw in rw_paths:
        Path(rw).mkdir(parents=True, exist_ok=True)
        argv += ["--bind", rw, rw]

    argv += list(target_argv)
    return argv


def _compose_agent_argv(agent: str, agent_args: list[str]) -> list[str]:
    """Build the final binary argv with per-agent bypass-flag injection at the
    correct position. Codex needs its bypass AFTER the `exec` subcommand.
    """
    bypass = bypass_args(agent)
    if not bypass:
        return [agent, *agent_args]

    if agent == "codex":
        # Find `exec` token; insert bypass after it. If no `exec`, prepend.
        if "exec" in agent_args:
            idx = agent_args.index("exec") + 1
            return ["codex", *agent_args[:idx], *bypass, *agent_args[idx:]]
        return ["codex", *bypass, *agent_args]
    # claude / agy: top-level flag, prepend before user args.
    return [agent, *bypass, *agent_args]


def _git_dir_of(project_dir: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return (project_dir / result.stdout.strip()).resolve()
    except FileNotFoundError:
        pass
    return None


def run(
    agent: str,
    agent_args: list[str],
    project_root: Path | str,
    extra_rw: Iterable[str] | None = None,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
    check: bool = False,
    **kwargs,
) -> subprocess.CompletedProcess:
    """Run an agent under sandbox containment. Composes bypass-flag injection
    into argv, generates seatbelt/bwrap wrapping, exports PLAYBOOK_SANDBOXED=1
    in child env. If already inside a sandbox (nesting), skips wrapping but
    still injects bypass flags.
    """
    project = Path(project_root).resolve()
    child_env = dict(os.environ) if env is None else dict(env)
    child_env["PLAYBOOK_SANDBOXED"] = "1"

    inner_argv = _compose_agent_argv(agent, agent_args)

    if is_sandboxed():
        # Already inside outer sandbox — exec target directly.
        wrapped = inner_argv
    elif platform.system() == "Darwin" and shutil.which("sandbox-exec"):
        git_dir = _git_dir_of(project)
        profile = build_seatbelt_profile(project, git_dir, extra_rw)
        wrapped = ["sandbox-exec", "-p", profile, *inner_argv]
    elif shutil.which("bwrap"):
        git_dir = _git_dir_of(project)
        wrapped = build_bwrap_argv(project, git_dir, inner_argv, extra_rw)
    else:
        # No sandbox primitive available — exec directly with bypass.
        # Callers wanting strict containment must check is_sandboxed() upstream.
        wrapped = inner_argv

    return subprocess.run(
        wrapped,
        cwd=str(project),
        env=child_env,
        capture_output=capture_output,
        check=check,
        **kwargs,
    )


def _format_agent_matrix(agents: dict[str, AgentInfo]) -> str:
    rows = []
    for name in _AGENT_ORDER:
        info = agents[name]
        if info.binary_path:
            tag = f"✓ {info.binary_path}"
            if info.via and info.via != "direct":
                tag += f" (via {info.via})"
        else:
            tag = "—"
        rows.append(f"  {name:8s} {tag}")
    return "\n".join(rows)


def _main(argv: list[str]) -> int:
    """CLI entry: python3 -m provider.sandbox [--list-agents | --print-profile |
    --agent X --] <agent-args>."""
    import argparse

    parser = argparse.ArgumentParser(prog="provider.sandbox", add_help=True)
    parser.add_argument("--agent", default=None,
                        help="Agent to launch (default: auto-detect)")
    parser.add_argument("--list-agents", action="store_true",
                        help="Print capability matrix and exit")
    parser.add_argument("--print-profile", action="store_true",
                        help="Print seatbelt profile to stdout and exit")
    parser.add_argument("--rw", action="append", default=[],
                        help="Extra read-write path (repeatable)")
    parser.add_argument("--project-root", default=None,
                        help="Project root (default: cwd)")
    parser.add_argument("agent_args", nargs=argparse.REMAINDER,
                        help="Args passed verbatim to the agent binary")

    args = parser.parse_args(argv)

    if args.list_agents:
        print("Sandbox agent capability matrix:")
        print(_format_agent_matrix(detect_agents()))
        return 0

    project = Path(args.project_root or Path.cwd()).resolve()

    if args.print_profile:
        print(build_seatbelt_profile(project, _git_dir_of(project), args.rw))
        return 0

    agent = args.agent or default_agent()
    forwarded = list(args.agent_args)
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]

    result = run(agent, forwarded, project, extra_rw=args.rw)
    return result.returncode


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
