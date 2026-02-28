# Claude Playbook

A Claude Code plugin that lets you grant your agent more autonomy — by making its work observable, aligned, and safe.

## The problem

You want your agent to handle substantial work independently. But autonomy without alignment produces drift — the agent builds the wrong thing, forgets context, skips verification. So you hold the reins tight and review everything, which defeats the purpose.

The way out: give the agent structure that keeps it aligned, then let it run. Safety first (it can't break things), alignment second (it's working on what you asked), observability third (you can see what happened). With those in place, you can trust the agent with more.

Three ideas make this work.

## task.md — a scripting language for agents

The task document is the central artifact. Everything orbits it.

A task.md is a program that the agent executes. Gates are instructions. Checkboxes are the program counter. The agent works top to bottom: read gate, do work, check box with findings, next gate. The checked document IS the execution trace — you can read it at any point and know exactly where things stand.

But it's more than a checklist. The same file captures your intent, the agent's plan, checkpoint reflections, and the judge's review. It's an intent document, a workbook, and an audit trail at the same time. A judge agent reviews the plan before work begins — it sees the codebase but not the conversation, so there's no anchoring or pressure to agree. Multiple agents across sessions can pick up the same task.md and continue where the last one left off.

The plan and execution co-evolve through the document. The agent edits the plan as it learns — gates get annotated with findings, new steps get added when discoveries demand them, sections get removed when reality reveals they're unnecessary. You don't need a state machine when you have a document that tracks its own state.

Tasks are the natural unit of work. "Add user authentication." "Fix the parser bug." "Investigate the memory leak." Small tasks are fine. Bash commands, doc edits, quick fixes don't need a task — the structure is for work that benefits from it.

Workflow enforcement makes the structure hold. The agent can't edit code without an active task. Can't skip steps. Can't call work done with steps left unchecked. These are system-level constraints, not suggestions — hooks block the action, not warn about it. A chat log records every message you send with timestamps and IDs, so drift becomes visible after the fact even if it wasn't caught in the moment.

<p align="center"><img src="assets/task_lifecycle.png" width="700" alt="Task lifecycle: 1. Task Creation (human + agent), 2. Plan Review (headless judge), 3. Build + Test (yolo worker + chat steering), 4. Work Review (headless judge), then back to 1"></p>

You can steer anytime — your messages arrive between steps. "Wrong approach," "skip that," "focus on X" — the agent adjusts the remaining steps.

## The mind map — memory as a markdown graph

`MIND_MAP.md` is the project's persistent memory — a flat list of numbered nodes with `[N]` cross-references. A graph structure in plain text, kept under 10KB so the agent can load it whole at session start. This makes bootstrap quick and cheap — the agent orients from the mind map instead of thrashing around the codebase. Your twentieth session benefits from what was learned in the first.

The format is designed for git: one line per node means clean diffs, easy grep, and append-only growth. No section headers, no hierarchy — just nodes and links. It tracks intent from the top down (what we're trying to build and why) and implementation from the bottom up (what we learned by building it). It's a router: the agent reads it to orient, follows cross-references to go deeper, and updates it after completing work.

Here's what a few nodes look like in practice:

> **[1] Project Overview** — Claude Playbook packages an agent steering methodology as a distributable plugin **[2]**. The core insight: the solution to agent autonomy is text (templates, patterns, questions), not code (frameworks, state machines, containers) **[18]**. Empirically refined across 60+ tasks...
>
> **[5] Task System** — Each task is a folder containing a living document that IS the execution trace **[19]**. Design Phase → Work Plan → Pre-review. Task types map to playbook patterns: feature → Build, explore → Investigate, review → Evaluate...
>
> **[13] Mind Map** — Persistent knowledge graph in MIND_MAP.md. Routing nodes **[1-4]** serve as table of contents. Records WHY not just WHAT — the reasoning that can't be recovered from code alone...
>
> **[19] Document-Driven Execution** — A task.md is a complete computational model: checkboxes = state, sections = memory, templates = instruction set, agent = interpreter **[5]**. The final task.md is both the record of what happened and the program that drove it...

Every node links to related nodes. The agent can follow **[5]** from the overview to the task system details, then **[19]** to the execution model. Architecture, decisions, context, reasoning — the things that get lost between sessions now persist across them.

## Testing — the enabler of autonomy

<p align="center"><img src="assets/reactive_test_environment.png" width="600" alt="An AI agent in a go-kart racing inside concentric tire barriers labeled Unit Tests, Integration Tests, and E2E Tests, with a Safe Zone in the center"></p>

Tests aren't quality assurance. They're the mechanism that lets you grant more trust.

Agents lack human intuition about whether code is working. Tests make consequences observable in real time. Without tests, the agent navigates blind. With tests, it perceives and adapts. The better the tests, the longer the agent can run unsupervised.

The plugin treats tests and documentation as more important than code. The agent writes tests before or alongside new features, not after. This isn't a methodology preference — it's a prerequisite for autonomy. You can't safely let the agent run if you can't see what it's doing.

The sandbox is the other half of the safety equation. It wraps Claude in OS-level write containment — your project directory is writable, `.git` is read-only, everything else is blocked at the kernel level. No Docker. Tests catch logical errors; the sandbox catches blast radius. Even in sandbox mode, sessions stay interactive — you're always in the loop, observing and steering, not launching a headless process and hoping for the best.

## Install

```
claude plugin marketplace add horiacristescu/claude-playbook-plugin
```

Then in any project, tell the agent `/playbook:init`.

## Two agents, one task

The intended setup uses two agents working from the same task document. The **orchestrator** runs outside the sandbox — it dishes out work, writes the plan, reviews the result, and commits. The **sandbox agent** runs in bypass-permissions mode inside the seatbelt — it picks up the task.md as its guide and does the implementation and judging. The task document is the handoff point: the orchestrator writes the plan, the sandbox agent executes against it, the orchestrator accepts or pushes back on the result.

## When not to use it

Quick questions, one-line fixes, shell commands, doc tweaks — anything that doesn't need multiple steps. The plugin is for work where structure pays for itself: features, refactors, investigations, multi-file changes.

Refined across 60+ tasks on a real codebase. Works best when you want to say "build this" and check back later, rather than watching every step.
