"""Monitor sensor — incremental JSONL reader with compact extraction.

Reads session JSONL from byte offset, extracts a compact timeline of events
(user messages, assistant text, tool calls with phase tags). Designed to be
called in a loop by the monitor agent or entry point script.

Usage as module:
    from sensor import read_new_events, format_compact

Usage as CLI (for testing):
    python3 sensor.py <session.jsonl> [--from-start] [--poll [--pid PID]]
"""
import json
import os
import sys
import time
from pathlib import Path


# ── Extraction (adapted from session-analyzer.py) ────────────────────────


def _extract_detail(tool: str, inp: dict) -> str:
    """Compact one-line detail for a tool call."""
    if tool in ("Read", "Glob"):
        fp = inp.get("file_path", inp.get("path", ""))
        return fp.split("/")[-1] if "/" in fp else fp
    elif tool in ("Edit", "Write", "NotebookEdit"):
        fp = inp.get("file_path", "")
        fname = fp.split("/")[-1] if "/" in fp else fp
        old = inp.get("old_string", "")[:40].replace("\n", "↵") if "old_string" in inp else ""
        return f"{fname}" + (f" [{old}...]" if old else "")
    elif tool == "Bash":
        return inp.get("command", "")[:80].replace("\n", "; ")
    elif tool == "Grep":
        return f"/{inp.get('pattern', '')[:30]}/"
    elif tool == "Skill":
        return inp.get("skill", "")
    elif tool in ("TaskOutput", "TaskCreate", "TaskUpdate", "TaskGet"):
        return inp.get("task_id", inp.get("description", ""))[:40]
    elif tool in ("ToolSearch", "WebSearch", "WebFetch"):
        return inp.get("query", inp.get("url", ""))[:60]
    return ""


def classify_phase(tool: str) -> str:
    """Classify tool into work phase: orient/execute/verify/meta."""
    if tool in ("Read", "Grep", "Glob", "WebSearch", "WebFetch", "ToolSearch"):
        return "orient"
    elif tool in ("Edit", "Write", "NotebookEdit"):
        return "execute"
    elif tool == "Bash":
        return "verify"
    elif tool in ("Skill", "TaskCreate", "TaskUpdate", "TaskGet",
                   "TaskList", "TaskOutput", "TaskStop"):
        return "meta"
    return "other"


PHASE_TAG = {"orient": "O", "execute": "E", "verify": "V", "meta": "M", "other": "?"}


# ── Noise filters ────────────────────────────────────────────────────────

_NOISE_PREFIXES = (
    "/", "!", "<command-", "<local-command", "<task-notification",
)


def _is_noise(text: str) -> bool:
    return any(text.startswith(p) for p in _NOISE_PREFIXES)


# ── Incremental reader ───────────────────────────────────────────────────


def read_new_events(jsonl_path: Path, since_offset: int,
                    stop_after_turn_end: bool = False) -> tuple[list[dict], int]:
    """Read new events from session JSONL since byte offset.

    Returns (events, new_offset). Each event is one of:
      {"type": "user", "ts": str, "preview": str}
      {"type": "assistant_text", "ts": str, "preview": str}
      {"type": "tool", "ts": str, "tool": str, "detail": str, "phase": str}
      {"type": "turn_end", "ts": str}   # emitted when assistant msg has stop_reason=end_turn

    Skips isMeta records and noise prefixes (slash commands, task notifications).

    If `stop_after_turn_end` is True, stops reading after processing the JSONL
    line that produced a turn_end event — the returned offset points past that
    line, so the next call resumes with the next turn.
    """
    events: list[dict] = []
    new_offset = since_offset

    try:
        with open(jsonl_path, "rb") as f:
            f.seek(since_offset)
            for raw_line in f:
                new_offset += len(raw_line)
                line_emitted_turn_end = False
                try:
                    d = json.loads(raw_line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue

                ts = d.get("timestamp", "")
                msg_type = d.get("type", "")

                if msg_type == "user":
                    if d.get("isMeta"):
                        continue
                    content = d.get("message", {}).get("content", "")
                    if not content:
                        content = d.get("content", "")
                    if isinstance(content, list):
                        texts = [b.get("text", "") for b in content
                                 if isinstance(b, dict) and b.get("type") == "text"]
                        content = " ".join(texts)
                    text = str(content).strip()
                    if not text or _is_noise(text):
                        continue
                    preview = text[:150].replace("\n", " ")
                    events.append({"type": "user", "ts": ts, "preview": preview})

                elif msg_type == "assistant":
                    msg = d.get("message", {})
                    content_blocks = msg.get("content", []) if isinstance(msg, dict) else []
                    for block in content_blocks:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text":
                            text = block.get("text", "").strip()
                            if text:
                                preview = text[:150].replace("\n", " ")
                                events.append({"type": "assistant_text", "ts": ts,
                                               "preview": preview})
                        elif block.get("type") == "tool_use":
                            tool = block.get("name", "unknown")
                            inp = block.get("input", {})
                            detail = _extract_detail(tool, inp)
                            phase = classify_phase(tool)
                            events.append({"type": "tool", "ts": ts, "tool": tool,
                                           "detail": detail, "phase": phase})
                        elif block.get("type") == "thinking":
                            # Content is empty in the JSONL (Anthropic anti-distillation).
                            # Emit a marker so the monitor sees that thinking occurred,
                            # even if it can't preview the reasoning text.
                            thinking_text = block.get("thinking", "")
                            preview = (thinking_text[:150].replace("\n", " ")
                                       if thinking_text else "(content redacted)")
                            events.append({"type": "assistant_thinking", "ts": ts,
                                           "preview": preview})
                    # Emit turn boundary marker if agent stopped its turn
                    if isinstance(msg, dict) and msg.get("stop_reason") == "end_turn":
                        events.append({"type": "turn_end", "ts": ts})
                        line_emitted_turn_end = True

                if stop_after_turn_end and line_emitted_turn_end:
                    break
    except OSError:
        pass

    return events, new_offset


# ── Compact formatting ───────────────────────────────────────────────────


def format_compact(events: list[dict]) -> str:
    """Format events into compact text for session.md.

    Groups tool calls into spans between user messages.
    """
    lines: list[str] = []
    span_tools: list[dict] = []
    span_trigger = ""
    span_num = 0

    def flush_span():
        nonlocal span_num
        if not span_tools:
            return
        span_num += 1
        lines.append(f"### Span {span_num}: {len(span_tools)} calls — \"{span_trigger}\"")
        for i, t in enumerate(span_tools, 1):
            tag = PHASE_TAG.get(t["phase"], "?")
            detail = t["detail"][:60]
            lines.append(f"  {i:>3}. [{tag}] {t['tool']:<10} {detail}")
        lines.append("")

    for event in events:
        if event["type"] == "user":
            flush_span()
            span_tools = []
            span_trigger = event["preview"][:80]
            lines.append(f"**USER:** {event['preview']}")
            lines.append("")
        elif event["type"] == "assistant_text":
            lines.append(f"**AGENT:** {event['preview']}")
            lines.append("")
        elif event["type"] == "assistant_thinking":
            lines.append(f"**[T] (thinking)** {event['preview']}")
            lines.append("")
        elif event["type"] == "tool":
            span_tools.append(event)

    flush_span()
    return "\n".join(lines)


# ── Offset persistence ───────────────────────────────────────────────────


def load_offset(offset_file: Path) -> int | None:
    """Load saved byte offset, or None if not found."""
    try:
        return int(offset_file.read_text().strip())
    except (OSError, ValueError):
        return None


def save_offset(offset_file: Path, offset: int) -> None:
    """Atomically save byte offset."""
    tmp = offset_file.with_suffix(".tmp")
    tmp.write_text(str(offset))
    tmp.rename(offset_file)


# ── Poll loop (for CLI / monitor entry point) ────────────────────────────


def wait_once(jsonl_path: Path, offset_file: Path, pid: int | None = None,
              from_start: bool = False, interval: float = 1.0,
              max_wait: float = 3600.0,
              stall_flush_seconds: float = 60.0) -> tuple[list[dict], int]:
    """Block until an agent turn ends, then return accumulated events and exit.

    Accumulates events across multiple reads without persisting offset.
    Flushes + persists only when:
      - a `turn_end` event is encountered (assistant msg with stop_reason=end_turn), OR
      - buffer non-empty and no new bytes for stall_flush_seconds (crashed agent), OR
      - pid is given and the process dies, OR
      - max_wait elapses.

    This gives the monitor the full turn as context, not fragment-by-fragment.
    """
    persisted_offset = load_offset(offset_file)
    if persisted_offset is None:
        if from_start:
            persisted_offset = 0
        else:
            try:
                persisted_offset = jsonl_path.stat().st_size
            except OSError:
                persisted_offset = 0
        save_offset(offset_file, persisted_offset)

    # Internal offset advances as we read, but persisted_offset only updates on flush
    internal_offset = persisted_offset
    buffer: list[dict] = []
    last_event_time = time.monotonic()
    start = time.monotonic()

    while time.monotonic() - start < max_wait:
        try:
            size = jsonl_path.stat().st_size
        except OSError:
            size = 0

        if size > internal_offset:
            # stop_after_turn_end: if the chunk contains a turn_end, read stops
            # at that line so next wake starts at the next turn.
            new_events, new_offset = read_new_events(
                jsonl_path, internal_offset, stop_after_turn_end=True
            )
            if new_offset > internal_offset:
                internal_offset = new_offset
            if new_events:
                last_event_time = time.monotonic()
                buffer.extend(new_events)
                # Turn boundary — flush and persist
                if any(e["type"] == "turn_end" for e in new_events):
                    save_offset(offset_file, internal_offset)
                    return buffer, internal_offset

        # Stall flush: buffer non-empty but no events for a while = crashed agent
        if buffer and time.monotonic() - last_event_time > stall_flush_seconds:
            buffer.append({"type": "stall_flush", "ts": "",
                           "note": f"flushed after {stall_flush_seconds:.0f}s silence"})
            sys.stderr.write(
                f"[sensor] stall_flush: agent silent {stall_flush_seconds:.0f}s, "
                f"flushing partial turn ({len(buffer)} events)\n"
            )
            save_offset(offset_file, internal_offset)
            return buffer, internal_offset

        # PID liveness
        if pid is not None:
            try:
                os.kill(pid, 0)
            except OSError:
                if buffer:
                    save_offset(offset_file, internal_offset)
                return buffer, internal_offset

        time.sleep(interval)

    # Max wait elapsed
    if buffer:
        save_offset(offset_file, internal_offset)
    return buffer, internal_offset


def poll_loop(jsonl_path: Path, offset_file: Path, pid: int | None = None,
              from_start: bool = False, interval: float = 1.0,
              idle_timeout: float = 300.0):
    """Block until new events, yield them, repeat.

    Exits cleanly if:
    - pid is given and the process is dead (os.kill(pid, 0) raises)
    - no new events for idle_timeout seconds and pid check fails
    """
    # Initialize offset
    offset = load_offset(offset_file)
    if offset is None:
        if from_start:
            offset = 0
        else:
            # Cold start: begin from current EOF
            try:
                offset = jsonl_path.stat().st_size
            except OSError:
                offset = 0
        save_offset(offset_file, offset)

    idle_since = time.monotonic()

    while True:
        # Check file size
        try:
            size = jsonl_path.stat().st_size
        except OSError:
            size = 0

        if size > offset:
            events, new_offset = read_new_events(jsonl_path, offset)
            if events:
                offset = new_offset
                save_offset(offset_file, offset)
                idle_since = time.monotonic()
                yield events
            elif new_offset > offset:
                # Bytes read but no events extracted (noise)
                offset = new_offset
                save_offset(offset_file, offset)

        # PID liveness check on idle timeout
        if time.monotonic() - idle_since > idle_timeout:
            if pid is not None:
                try:
                    os.kill(pid, 0)
                except OSError:
                    return  # front agent is dead
            idle_since = time.monotonic()  # reset, keep waiting

        time.sleep(interval)


# ── CLI ──────────────────────────────────────────────────────────────────


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    jsonl_path = Path(args[0])
    from_start = "--from-start" in args
    do_poll = "--poll" in args
    do_wait = "--wait-once" in args
    pid = None
    if "--pid" in args:
        idx = args.index("--pid")
        if idx + 1 < len(args):
            pid = int(args[idx + 1])

    offset_file_override = None
    if "--offset-file" in args:
        idx = args.index("--offset-file")
        if idx + 1 < len(args):
            offset_file_override = Path(args[idx + 1])

    session_id = None
    if "--session-id" in args:
        idx = args.index("--session-id")
        if idx + 1 < len(args):
            session_id = args[idx + 1]

    trace_file = None
    if "--trace-file" in args:
        idx = args.index("--trace-file")
        if idx + 1 < len(args):
            trace_file = Path(args[idx + 1])
            trace_file.parent.mkdir(parents=True, exist_ok=True)

    # Resolve offset file: explicit --offset-file wins; else derive from
    # --session-id; else error out. No shared default — that corrupts state
    # across PIDs (T119 Thread 4).
    if offset_file_override:
        offset_file = offset_file_override
    elif session_id:
        offset_file = Path(__file__).parent / "pids" / session_id / ".offset"
        offset_file.parent.mkdir(parents=True, exist_ok=True)
    else:
        print(
            "Error: must pass either --offset-file <path> or --session-id <id>.\n"
            "       No shared default is allowed (would corrupt state across PIDs).",
            file=sys.stderr,
        )
        sys.exit(2)

    def _emit(events: list[dict]):
        """Print compact events to stdout and optionally append to trace file."""
        if not events:
            return
        compact = format_compact(events)
        print(compact)
        if trace_file is not None:
            stamp = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())
            header = f"\n## Wake {stamp} ({len(events)} events)\n\n"
            with trace_file.open("a") as tf:
                tf.write(header)
                tf.write(compact)
                tf.write("\n")

    if do_poll:
        for events in poll_loop(jsonl_path, offset_file, pid=pid,
                                from_start=from_start):
            _emit(events)
            print("---")
            sys.stdout.flush()
    elif do_wait:
        # Block until new events, print them, exit (for interactive agent use)
        events, _ = wait_once(jsonl_path, offset_file, pid=pid,
                              from_start=from_start)
        if events:
            _emit(events)
        else:
            print("(no events — timeout or pid died)")
    else:
        # One-shot: read everything (or from offset)
        offset = 0 if from_start else (load_offset(offset_file) or 0)
        events, new_offset = read_new_events(jsonl_path, offset)
        if events:
            _emit(events)
            save_offset(offset_file, new_offset)


if __name__ == "__main__":
    main()
