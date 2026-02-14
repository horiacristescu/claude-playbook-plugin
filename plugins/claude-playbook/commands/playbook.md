---
description: Show workflow patterns and task execution guidance
argument-hint: "[pattern-name]"
allowed-tools: [Read, Glob, Grep]
---

# Playbook

Show workflow patterns for task execution. Five core patterns: Build,
Investigate, Evaluate, Decide, UI Debug.

**User asked for:** $ARGUMENTS

## Instructions

Read the full playbook skill from skills/playbook/SKILL.md.

If the user specified a pattern name, show that pattern's details.
If no argument, show the pattern overview and ask which one they need.

Available patterns:
- **Build** — step-test interleave for implementing features
- **Investigate** — observe-hypothesize-test for debugging/research
- **Evaluate** — pre-check, lenses, verdict for reviewing quality
- **Decide** — options, comparison, commitment for architecture choices
- **UI Debug** — script-probe-screenshot for browser bugs

Also explain: reflection gates (Critique, Checkpoint, Replan), the Design Phase,
and how to compose patterns for complex tasks.
