---
name: monitor
description: >
  Conversation monitor — a second Claude agent that watches the front agent's
  work from outside and nudges when trajectory goes wrong. Reads session JSONL
  incrementally, maintains a compact judgment trace, delivers nudges via hook.
argument-hint: [off | start]
---

# Monitor

## What It Does

Launches a **conversation monitor** — a second Claude agent that watches your
current conversation from outside and injects steering nudges when it notices
the trajectory going wrong.

The monitor runs as its own process, reads the session JSONL incrementally,
maintains a compact judgment trace, and writes nudges that the front agent
sees via a `PostToolUse` hook.

## Why

LLMs collapse toward the user's frame across turns — mirroring vocabulary,
accepting premises, producing responses that feel like engagement but are
accommodation. Trajectory patterns are invisible at turn granularity. Seeing
them requires a vantage point outside the sequence.

Per-turn instructions can't fix this because they ask the drifting model to
monitor its own drift. An external monitor can watch the arc.

## Usage

**Start the monitor:**
```
/monitor
```
Launches `.agent/monitor/monitor.py --session-id <current-session> --interactive`
in the background. Writes its PID to `.agent/monitor/.pid`.

**Stop the monitor:**
```
/monitor off
```
Runs `python3 .agent/monitor/monitor.py stop`, which sends SIGTERM to the
running monitor process. Falls back to `.shutdown` marker if signal fails.

## What the Monitor Watches

- **User messages** — what you asked for, how you corrected the agent
- **Agent text** — compact previews of what the agent said
- **Tool calls with phase tags** — orient (O), execute (E), verify (V), meta (M)
- **Work spans** — sequences of tool calls between user messages

The sensor extracts all this incrementally from `~/.claude/projects/<slug>/<session>.jsonl`.

## What the Monitor Writes

Two files the monitor owns:
- `.agent/monitor/pids/<session-id>/session.md` — compact trace + judgment
- `.agent/monitor/pids/<session-id>/nudge.md` — outbox for the hook

Plus persistent state:
- `.agent/monitor/MONITOR_MIND_MAP.md` — accumulated user knowledge
- `.agent/monitor/rules.md` — steering rules learned from observation

## Delivery

The monitor writes nudges to `.agent/monitor/pids/<session-id>/nudge.md`.
A non-plugin `PostToolUse` hook (registered in `.claude/settings.json`) reads
the nudge on the front agent's next tool call, emits it as `additionalContext`,
and logs `[MONITOR→<session-id>]` to chat_log.

**Note:** UserPromptSubmit `additionalContext` is broken in current CC
(plugin and non-plugin hooks affected, issue #12151). PostToolUse is the
working injection point. Nudges arrive on the agent's next tool call, not
between user message and LLM response.

## When It Nudges

Silence is the default. Most turns need no intervention.

The monitor nudges when it sees:
- **Accommodation** — agent mirroring the user's frame without resistance
- **Gap blindness** — missing considerations nobody caught
- **Premature convergence** — closing before alternatives explored
- **Ground re-covering** — re-deriving what was already established
- **Phase imbalance** — orient-heavy without execute, edit-heavy without verify
- **Rule triggers** — patterns in `rules.md` firing

Rules start empty and accumulate from real observations. The monitor is not
pre-coded with problems you haven't seen.

## Lifecycle

1. `/monitor` — launch background process, record PID
2. Sensor loop: poll JSONL → extract events → reason via `claude -p` → write
3. Each wake: update session.md, maybe nudge, maybe add rule
4. `/monitor off` — SIGTERM to the process, final session.md entry, clean exit
5. Idle timeout: if no new JSONL for 5min, check front agent PID liveness; if dead, exit

## Architecture

- `.agent/monitor/` — monitor's home (r/w for monitor, r/o everywhere else)
- `.agent/monitor/sensor.py` — incremental JSONL reader + compact extractor
- `.agent/monitor/monitor.py` — main loop (bootstrap → sensor → reason → write)
- `.agent/monitor/CLAUDE.md` — monitor's orientation document
- `.agent/monitor/sandbox.sb` — seatbelt profile (macOS)
- `.claude/hooks/monitor-nudge.sh` — injection hook (registered in settings.json)

## Limitations (v1)

- Claude JSONL only (Codex extension deferred)
- PostToolUse injection timing (not UserPromptSubmit) — upstream bug
- `rules.md` starts empty and fills slowly
- Single-project scope (cross-project memory deferred)
