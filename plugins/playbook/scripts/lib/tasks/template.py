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

> **Before filling this in:** run `tasks work <N>` to activate this task. Hooks won't enforce until activated."""


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
- Note: Don't hardcode task numbers in plans \u2014 `tasks new` auto-increments.

---"""


def design_phase_intro() -> str:
    return """\
## Design Phase

> **Write a 1-sentence answer for each gate.** A bare checkmark means you skipped it.
> Complete these gates before writing the work plan.
> (The `/playbook` skill has workflow patterns if you need a reference.)"""


def understand() -> str:
    return """\
### Understand
- [ ] Restate the request in my own words. What does the user actually want?
- [ ] Critique: Am I solving the stated problem or a different one I find more interesting?
- [ ] What would "done" look like? How will we know the task succeeded?
- [ ] What is OUT of scope for this task?"""


def structure() -> str:
    return """\
### Structure
- [ ] What kind of work is this? (building something, finding something out, judging quality, choosing between options \u2014 or a combination?)
- [ ] If it's a combination: what's the sequence? Where might you jump to building before you've understood enough?
- [ ] If the approach is uncertain or the plan is long (>15 gates): where should you stop and decide whether to continue? Don't plan 20 steps if you haven't validated the direction."""


def reflection_gates() -> str:
    return """\
### Reflection Gates
- [ ] Wrote task-specific check questions (Bad: "is this working?" Good: "Does the output include the progress counter?" \u2014 the answer should require evidence, not just yes/no)
- [ ] Before the riskiest step: what would make you stop and reconsider?
- [ ] If judging quality before building: is the gap worth closing?"""


def extension_demonstrations() -> str:
    return """\
### Extension Demonstrations
> When reality diverges from your plan, expand task.md with new gates.
> These examples show what such expansions look like.
> Each example needs: a trigger condition (*when* does this fire?), a sequence of steps with thinking checkpoints, and a task-specific question.
> **Replace them with 3 adapted to your task's Intent.**
>
> **Research Study** \u2014 *if investigation reveals an unexpected dimension:*
> Investigate: round per dimension \u2192 Checkpoint: converging? what's missing?
> \u2192 Extension: add dimension (or Critique: "what am I not seeing?")
> \u2192 Investigate: new round \u2192 Build: synthesize findings
> \u2192 Evaluate: lenses (completeness, depth, transferability) \u2192 Sufficiency: gaps worth closing?
>
> **Deep Debug** \u2014 *if initial fix attempt fails:*
> Build: attempt fix based on reading code \u2192 if fix fails \u2192 stop coding
> \u2192 Investigate: observe actual behavior (probes/logs, not source)
> \u2192 Hypothesis \u2192 Probe \u2192 Result \u2192 Checkpoint: root cause found or still guessing?
> \u2192 Build: targeted fix from root cause \u2192 Evaluate: regression test, convert probe to permanent test
>
> **Audit** \u2014 *if patterns emerge across sampled instances:*
> Investigate: sample instances (don't cover everything)
> \u2192 Checkpoint: patterns emerging? categories forming?
> \u2192 Critique: am I imposing categories or letting them emerge?
> \u2192 Investigate: test patterns against remaining instances \u2192 Checkpoint: converging or scattering?
> \u2192 Build: write findings with evidence per pattern
> \u2192 Evaluate: adversarial \u2014 "who was right?" with quoted evidence

- [ ] Replaced the 3 generic examples above with 3 adapted to this task's Intent. Removed the "Replace them" instruction line."""


def verify() -> str:
    return """\
### Verify
- [ ] Review the work plan against extension demonstrations above. If a growth point is likely enough, add it to the plan now. Revise before executing.
- [ ] Does the work plan include moments where you stop and question your approach \u2014 not just execute?
- [ ] Checkpoint: Would a fresh agent understand this task and execute it well?
- [ ] The work plan below has the right granularity (not too coarse, not micro-steps)

---"""


def design_phase() -> str:
    """Compose all design phase subsections."""
    parts = [
        design_phase_intro(),
        understand(),
        structure(),
        reflection_gates(),
        extension_demonstrations(),
        verify(),
    ]
    return "\n\n".join(parts)


def work_plan() -> str:
    return """\
## Work Plan

> Design phase complete. Write your work gates below.
> **For each section of work, answer two questions before writing the steps:**
> 1. What could go wrong here? (Write it down \u2014 this becomes your risk check.)
> 2. How will you know it worked? (Write a specific, falsifiable check \u2014 not "looks good" but "X contains Y".)
>
> **Between sections, ask:** Is your plan still right? If what you've learned contradicts an assumption, say so and revise the plan. Don't keep executing a plan you know is wrong.
> **Before wrapping up, ask:** What might you be wrong about? What did you assume that you haven't verified?

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


def judge_prompt(task_path: str) -> str:
    """Return the blind judge prompt for plan review.

    Args:
        task_path: Relative path to the task.md file (e.g. .agent/tasks/001-foo/task.md)
    """
    return (
        "You are a senior engineer reviewing a PLAN — no code has been written yet. "
        "The MIND_MAP.md and task.md are provided in your system prompt. "
        "Read the source files referenced in the plan to understand existing patterns. "
        "Then critique the plan: "
        "(1) Will this approach actually fulfill the stated Intent? "
        "(2) What's missing or underspecified? "
        "(3) What will go wrong that isn't addressed? "
        "(4) Is anything over-engineered? "
        "Be specific and adversarial — your job is to find problems, not approve. "
        "Output numbered findings with severity (Critical/Important/Minor). "
        f"Then edit {task_path}: "
        "(1) replace '(judge findings appear here)' in the ## Judge section with your findings, "
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
"""


# ---------------------------------------------------------------------------
# Bootstrap briefing
# ---------------------------------------------------------------------------

def identity_preamble() -> str:
    """One-line framing shown at the top of bootstrap."""
    return "You are a coding assistant working with a task management harness."


def mind_map_header() -> str:
    """Navigation header shown before mind map routing nodes at bootstrap."""
    return (
        "Project knowledge graph. Nodes cross-reference with [N] IDs.\n"
        "Routing nodes [1-4] below — drill deeper: grep '^\\[N\\]' MIND_MAP.md\n"
        "Format spec: /mindmap skill"
    )


def extract_routing_nodes(content: str) -> str:
    """Extract routing nodes [1]-[4] from mind map content."""
    import re
    lines = content.splitlines()
    routing = []
    for line in lines:
        if re.match(r'^\[([1-4])\]\s', line):
            routing.append(line)
    return '\n\n'.join(routing)


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


def autonomy_nudge() -> str:
    """Nudge shown after bootstrap to prevent autonomous task selection."""
    return """\
IMPORTANT: Don't autonomously start tasks. Ask the user what to work on.
  User tells you: tasks work <N> | tasks new <type> <name> | or just chat"""


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
        work_plan(),
        pre_review(),
        parked(),
        standing_orders(),
    ]

    return "\n\n".join(parts) + "\n"
