# Claude Playbook

A Claude Code plugin that lets you grant your agent more autonomy — by making its work observable, aligned, and safe.

## The idea

You want your agent to handle substantial work independently. But autonomy without alignment produces drift — the agent builds the wrong thing, forgets context, skips verification. So you hold the reins tight and review everything, which defeats the purpose.

The way out: give the agent structure that keeps it aligned, then let it run. Safety first (it can't break things), alignment second (it's working on what you asked), observability third (you can see what happened). With those in place, you can trust the agent with more.

## How it works

**The task document** (`task.md`) is the central artifact. Everything orbits it. It captures your intent, the agent's plan, the execution trace, checkpoint reflections, and the judge's review — all in one file. The agent works through it step by step, recording what it finds. You can read it to see exactly where things stand. A judge agent can review it independently. Multiple agents across sessions can pick it up and continue. It's an intent document, a workbook, and an audit trail at the same time.

Tasks are the natural unit of work. "Add user authentication." "Fix the parser bug." "Investigate the memory leak." Small tasks are fine. Bash commands, doc edits, quick fixes don't need a task — the structure is for work that benefits from it.

**Tests come first.** The plugin treats tests and documentation as more important than code. The agent writes tests before or alongside new features, not after. Tests are the agent's eyes — without them, it navigates blind. With them, you can grant more autonomy because consequences are observable in real time.

**The mind map** (`MIND_MAP.md`) is the project's persistent memory. Architecture, decisions, context, reasoning — the things that get lost between sessions. The agent reads it at session start and updates it after completing work. Your twentieth session benefits from what was learned in the first. No more re-explaining the codebase.

**The chat log** records every message you send with timestamps and IDs. You can trace what you asked for, when, and how the agent interpreted it. It's the accountability layer — drift becomes visible after the fact even if it wasn't caught in the moment.

**Workflow enforcement.** The agent can't edit code without an active task. Can't skip steps. Can't call work done with steps left unchecked. These are system-level constraints, not suggestions — the agent cannot bypass them. This is what makes autonomy safe: the structure holds even when the agent's judgment doesn't.

**The sandbox** wraps Claude in OS-level write containment. Your project directory is writable, `.git` is read-only, everything else is blocked at the kernel level. No Docker. The agent can't break what it can't reach. Even in sandbox mode, sessions stay interactive — you're always in the loop, observing and steering, not launching a headless process and hoping for the best.

**The judge** is an independent agent that reviews a plan before work begins. It sees the codebase but not the conversation — no anchoring, no pressure to agree. It catches gaps the planning agent missed.

## Install

```
claude plugin marketplace add horiacristescu/claude-playbook-plugin
```

Then in any project, tell the agent `/playbook:init`.

## The task lifecycle

```
┌───────────────────┐         ┌────────────────────┐
│  1. TASK CREATION │ ──────► │  2. PLAN REVIEW    │
│  (human + agent)  │         │  (automated, judge)│
│                   │         │                    │
│  chat log research│         │                    │
│  restate intent   │         │  intent aligned?   │
│  define "done"    │         │  scope clear?      │
│  scope / risks    │         │  risks identified? │
│  write work plan  │         │  tests adequate?   │
└───────────────────┘         └─────────┬──────────┘
          ▲                             │
          │                             ▼
┌───────────────────┐         ┌────────────────────┐
│  4. WORK REVIEW   │ ◄────── │  3. BUILD + TEST   │
│  (automated)      │         │  (automated)       │
│                   │         │                    │
│  tests pass?      │         │  step ──► test     │
│  no debris?       │         │    │        │      │
│  mind map updated?│         │    ▼     pass/fail │
│  intent satisfied?│         │  step ──► test     │
│                   │         │    │               │
│  ──► commit       │         │  checkpoint:       │
│  ──► next task    │         │    adjust ──────┘  │
└───────────────────┘         └────────────────────┘
```

The loop reads clockwise. Left column is language testing (is the intent right? is the result right?). Right column is execution testing (does the plan hold up? does the code work?). Review findings feed back into the next task.

You can steer anytime — your messages arrive between steps. "Wrong approach," "skip that," "focus on X" — the agent adjusts the remaining steps.

## Two agents, one task

The intended setup uses two agents working from the same task document. The **orchestrator** runs outside the sandbox — it dishes out work, writes the plan, reviews the result, and commits. The **sandbox agent** runs in bypass-permissions mode inside the seatbelt — it picks up the task.md as its guide and does the implementation and judging. The task document is the handoff point: the orchestrator writes the plan, the sandbox agent executes against it, the orchestrator accepts or pushes back on the result.

## When not to use it

Quick questions, one-line fixes, shell commands, doc tweaks — anything that doesn't need multiple steps. The plugin is for work where structure pays for itself: features, refactors, investigations, multi-file changes.

Refined across 60+ tasks on a real codebase. Works best when you want to say "build this" and check back later, rather than watching every step.
