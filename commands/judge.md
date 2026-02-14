---
description: Spawn a blind judge to evaluate an idea, plan, or implementation
argument-hint: <what to evaluate>
allowed-tools: [Read, Glob, Grep, Task]
---

# Judge

Spawn a blind evaluator to judge something. The judge is a separate agent
that reads the repo but has no access to the conversation.

**The user wants to evaluate:** $ARGUMENTS

## Instructions

Follow the full judge protocol from the `judge` skill (skills/judge/SKILL.md):

1. State what's being judged — write it out for the user
2. Write a self-contained judge prompt with: subject, files to read, evaluation questions, output path
3. **Always include `MIND_MAP.md`** as the first file — it's the project's institutional memory
4. Spawn the judge via `Task(subagent_type="general-purpose")`
5. Write verdict to `.agent/tasks/NNN-name/judge-<topic>.md` (if active task) or `docs/judge-<topic>.md`
6. Review the verdict together with the user
