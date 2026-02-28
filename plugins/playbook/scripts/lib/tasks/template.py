"""Composable template components for task.md files.

Each methodology clause is a function returning a markdown string.
Templates are rendered by composing components in order.

Usage:
    from tasks.template import render_template
    content = render_template(num=1, title="My Task", task_type="feature")
"""
from __future__ import annotations

from tasks.core import PLAYBOOKS


# ---------------------------------------------------------------------------
# Components — each returns a markdown string
# ---------------------------------------------------------------------------

def header(num: int, title: str) -> str:
    return f"# {num:03d} - {title}"


def sticker() -> str:
    return """\
> **Gate discipline:** One gate \u2192 do work \u2192 check box \u2192 next gate.
> Never batch. Never backfill. The document IS the execution trace.
> **Closing a gate:** check the box, append your outcome. Never replace the original text.
> Design Phase = orientation (one gate, brief answer). Work Plan = real work (one gate, full effort).
> If you see the same gate 5+ times in the hook echo, you're drifting \u2014 STOP and update."""


def status() -> str:
    return """\
## Status
pending

> **Before filling this in:** run `.claude/bin/tasks work <N>` to activate this task. Hooks won't enforce until activated."""


def intent_why_refs(playbook: str) -> str:
    return f"""\
## Intent
(what we want to achieve \u2014 the outcome, not the activity)

## Why
(why this matters now \u2014 urgency, context, what breaks if delayed)

## References
- [ ] Context: `grep -Ein "keyword1|keyword2" MIND_MAP.md` \u2192 paste relevant excerpts below
- Origin: Mxxx
- Playbook: {playbook}
- Note: Don't hardcode task numbers in plans \u2014 `.claude/bin/tasks new` auto-increments.

---"""


def design_phase_intro() -> str:
    return """\
## Design Phase

> **Write a 1-sentence answer for each gate.** A bare checkmark means you skipped it.
> Complete these gates before writing the work plan.
> (The `/playbook` skill has workflow patterns if you need a reference.)"""


def chat_log_research() -> str:
    return """\
### Chat Log Research
- [ ] Scan for user context: Run `tasks context <N>` to get attributed messages. If no results, scan recent chat: `grep -E '^\\*\\*\\[M' .agent/chat_log.md | tail -20 | sed 's/\\*\\*//g' | cut -c -200`. Pull useful details — user quotes, constraints, context — into the References section above. The user's actual words are the ground truth for Intent."""


def understand() -> str:
    return """\
### Understand
- [ ] Restate the request in my own words. What does the user actually want?
- [ ] Critique: Am I solving the stated problem or a different one I find more interesting?
- [ ] What would "done" look like? How will we know the task succeeded?
- [ ] What are you assuming about the existing code/architecture that you haven't verified?
- [ ] What is OUT of scope for this task?"""


def structure() -> str:
    return """\
### Structure
- [ ] What kind of work is this? (build / investigate / evaluate / decide / combination?) If combination, what's the sequence? If >15 gates or uncertain approach, where do you stop and validate direction?"""


def reflection_gates() -> str:
    return """\
### Reflection Gates
- [ ] Wrote task-specific check questions (Bad: "is this working?" Good: "Does the output include the progress counter?" \u2014 the answer should require evidence, not just yes/no)
- [ ] Before the riskiest step: what would make you stop and reconsider?
- [ ] If judging quality before building: is the gap worth closing?"""



def verify() -> str:
    return """\
### Verify
- [ ] Review the work plan. If a likely growth point exists, add it to the plan now.
- [ ] Does the work plan include moments where you stop and question your approach \u2014 not just execute?
- [ ] Checkpoint: Would a fresh agent understand this task and execute it well?
- [ ] The work plan below has the right granularity (not too coarse, not micro-steps)"""


def design_phase() -> str:
    """Compose all design phase subsections."""
    parts = [
        design_phase_intro(),
        chat_log_research(),
        understand(),
        structure(),
        reflection_gates(),
        verify(),
    ]
    return "\n\n".join(parts)


def judge_section() -> str:
    return """\
## Judge
- [ ] Run `.claude/bin/tasks judge <N>` \u2014 wait for it to finish (it edits this file). Re-read this file to see its findings below, then address valid concerns by revising Work Plan gates.

(judge findings appear here)

---"""


def work_plan() -> str:
    return """\
## Work Plan

> For each work section: what could go wrong? How will you know it worked? (specific check, not "looks good")
> Standard feature: 6-8 work gates + tests. If >15 gates, validate the approach first.

(write work gates here)

---"""


def pre_review() -> str:
    return """\
## Pre-review
- [ ] All tests pass
- [ ] No debug artifacts
- [ ] MIND_MAP.md updated if new insights emerged"""


def parked() -> str:
    return """\
## Parked
(Findings or ideas that emerged during work but are out of scope. Describe each with enough context for a future task to pick it up.)

---"""


def judge_prompt(task_path: str, inline_context: bool = False,
                 mode: str = "plan") -> str:
    """Return the blind judge prompt for plan or implementation review.

    Args:
        task_path: Relative path to the task.md file (e.g. .agent/tasks/001-foo/task.md)
        inline_context: If True, say context is "provided below" (for backends
            without system prompt support, e.g. Codex). Default False = "in your system prompt".
        mode: "plan" for pre-implementation review, "impl" for post-implementation review.
    """
    context_location = "provided below" if inline_context else "provided in your system prompt"

    # Extract task number from path (e.g. .agent/tasks/042-foo/task.md -> 042)
    import re as _re
    _tn = _re.search(r'/(\d{3})-', task_path)
    task_number = _tn.group(1) if _tn else None
    intent_check = ""
    if task_number:
        intent_check = (
            f"If .agent/chat_log.md exists, run `tasks context {task_number}` to see the user's original messages. "
            "Check whether the task addresses what the user actually asked for, not just the agent's interpretation. "
        )

    if mode == "impl":
        return (
            "You are a senior engineer reviewing a COMPLETED implementation. "
            f"The MIND_MAP.md and task.md are {context_location}. "
            "Read the source files changed by this task (look at the Work Plan gates for paths). "
            f"{intent_check}"
            "Review through four lenses: "
            "(1) Simplify — what's unnecessary or over-engineered? What can be removed? "
            "(2) Self-critique — does the code actually fulfill the stated Intent? What would a skeptic say? "
            "(3) Bug scan — find actual bugs, edge cases, race conditions, or security issues. "
            "(4) Prove it works — cite file:line evidence showing correctness, or construct a concrete scenario showing failure. "
            "Be specific and adversarial — your job is to find problems, not approve. "
            "Max 5 findings, Critical and Important only — drop Minor. "
            "Each finding: cite file:line, 1-2 sentences stating the problem, 1 sentence stating the fix. No elaboration. "
            f"Then edit {task_path}: "
            "(1) replace the entire contents of the ## Judge section (everything between '## Judge' and the next '##' heading) with your findings — this is idempotent on reruns."
        )

    return (
        "You are a senior engineer reviewing a PLAN — no code has been written yet. "
        f"The MIND_MAP.md and task.md are {context_location}. "
        "Read the source files referenced in the plan to understand existing patterns. "
        f"{intent_check}"
        "Then critique the plan through four lenses: "
        "(1) Intent alignment — will this approach actually fulfill the stated Intent? What's missing or underspecified? "
        "(2) Failure modes — what will go wrong that isn't addressed? Construct a concrete failing scenario. "
        "(3) Simplify — is anything over-engineered? What can be dropped? "
        "(4) Prove it — cite file:line evidence for claims about existing code. No hand-waving. "
        "Be specific and adversarial — your job is to find problems, not approve. "
        "Max 5 findings, Critical and Important only — drop Minor. "
        "Each finding: cite file:line, 1-2 sentences stating the problem, 1 sentence stating the fix. No elaboration. "
        f"Then edit {task_path}: "
        "(1) replace the entire contents of the ## Judge section (everything between '## Judge' and the next '##' heading) with your findings — this is idempotent on reruns, "
        "(2) revise the ## Work Plan gates to address Critical and Important findings."
    )


def standing_orders() -> str:
    return """\
## Standing Orders
- **Expand dynamically**: When you discover something you'll need to do, write new gates immediately \u2014 don't wait until you get there.
- **Steer openly**: If your direction changes, edit your open (unchecked) gates to reflect reality. The plan is alive, not a contract.
- **Never defer awareness**: The moment you realize work exists, capture it. Forgetting is the failure mode, not having too many gates."""


# ---------------------------------------------------------------------------
# CLAUDE.md init template
# ---------------------------------------------------------------------------

def claude_md(title: str) -> str:
    """Generate CLAUDE.md content for `tasks init`."""
    return f"""\
# {title}

## Start Here

```bash
tasks bootstrap          # loads mind map, skills, pending tasks
```

Then **ask the user** what they want to work on. Don't autonomously pick a task.

## CLI

```bash
tasks work <number>              # activate task, hook starts tracking
tasks work done                  # deactivate when finished
tasks new <type> <name>          # create task — does NOT activate
tasks judge <number>             # blind plan review by independent agent
tasks list [--pending]           # task overview
tasks status                     # current gate position
tasks bootstrap                  # orientation: mind map + skills + pending
```

## Don't

- Create task directories manually — always `tasks new`
- Edit `.agent/current_state` — use `tasks work <N>` / `tasks work done`
- Edit `## Status` in task.md directly — use `tasks work done`
- Skip task.md checkboxes — they're your observable progress
- Start coding without an active task — blocked by hook until `tasks work <N>`
- Use EnterPlanMode or plan files — use `tasks new <type> <name>` instead, the task.md IS the plan
"""


# ---------------------------------------------------------------------------
# Bootstrap briefing
# ---------------------------------------------------------------------------

def identity_preamble() -> str:
    """One-line framing shown at the top of bootstrap."""
    return "You are a coding assistant working with a task management harness."


def mind_map_header() -> str:
    """Navigation header shown before full mind map at bootstrap."""
    return (
        "Project knowledge graph. Nodes cross-reference with [N] IDs.\n"
        "Full map below — drill into a node: grep '^\\[N\\]' MIND_MAP.md\n"
        "Format spec: /mindmap skill"
    )



def workflow_briefing() -> str:
    """Workflow rules shown at task activation (tasks work <N>)."""
    return """\
- One gate at a time: read gate → do work → check box → next gate
- Pattern templates in task.md ARE the work plan — fill them in, don't skip"""


def cli_reference() -> str:
    """CLI quick reference shown at bootstrap."""
    return """\
Tasks CLI:
  tasks work <N>           activate task (start here)
  tasks work done          mark done + deactivate
  tasks new <type> <name>  create task (doesn't activate)
  tasks judge <N>          blind plan review
  tasks list [--pending]   show tasks
  tasks status             current gate position"""


# ---------------------------------------------------------------------------
# CLI usage
# ---------------------------------------------------------------------------

def usage_text() -> str:
    """Usage text for `tasks --help`."""
    types = ", ".join(sorted(PLAYBOOKS.keys()))
    return f"""\
Usage: tasks <command> [args]

Commands:
  init                Create CLAUDE.md for this project
  bootstrap           Load mind map + skills + pending tasks
  work <number>       Set active task (e.g. tasks work 058)
  new <type> <name>   Create task with playbook template
  list [--pending]    List all tasks with status
  status              Show head position for active tasks
  judge <number>      Run blind plan-review judge on a task

Task types: {types}

Examples:
  tasks work 058
  tasks new feature add-auth
  tasks judge 001
  tasks list --pending"""


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------

def render_template(num: int, title: str, task_type: str | None = None) -> str:
    """Compose all components into a complete task.md template.

    Args:
        num: Task number (will be zero-padded to 3 digits)
        title: Task title (will be title-cased in header)
        task_type: Optional task type for playbook reference

    Returns:
        Complete task.md content as a string
    """
    pattern_name = PLAYBOOKS.get(task_type) if task_type else None
    playbook_ref = f"playbook/{pattern_name}" if pattern_name else "(none)"

    parts = [
        header(num, title),
        sticker(),
        status(),
        intent_why_refs(playbook_ref),
        design_phase(),
        judge_section(),
        work_plan(),
        pre_review(),
        parked(),
        standing_orders(),
    ]

    return "\n\n".join(parts) + "\n"
