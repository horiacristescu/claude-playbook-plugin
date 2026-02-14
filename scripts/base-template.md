# {num:03d} - {title}

> **Gate discipline:** One gate → do work → check box → next gate.
> Never batch. Never backfill. The document IS the execution trace.
> **Closing a gate:** check the box, append your outcome. Never replace the original text.
> Design Phase = orientation (one gate, brief answer). Work Plan = real work (one gate, full effort).
> If you see the same gate 5+ times in the hook echo, you're drifting — STOP and update.

## Status
pending

## Intent
(what we want to achieve — the outcome, not the activity)

## Why
(why this matters now — urgency, context, what breaks if delayed)

## References
- [ ] Context: `grep -Ein "keyword1|keyword2" MIND_MAP.md` → paste relevant excerpts below
- Origin: Mxxx
- Playbook: {playbook}
- Note: Don't hardcode task numbers in plans — `tasks new` auto-increments.

---

## Design Phase

> **Write a 1-sentence answer for each gate.** A bare checkmark means you skipped it.
> Complete these gates before writing the work plan.
> (The `/playbook` skill has workflow patterns if you need a reference.)

### Understand
- [ ] Restate the request in my own words. What does the user actually want?
- [ ] Critique: Am I solving the stated problem or a different one I find more interesting?
- [ ] What would "done" look like? How will we know the task succeeded?
- [ ] What is OUT of scope for this task?

### Structure
- [ ] What kind of work is this? (building something, finding something out, judging quality, choosing between options — or a combination?)
- [ ] If it's a combination: what's the sequence? Where might you jump to building before you've understood enough?
- [ ] If the approach is uncertain or the plan is long (>15 gates): where should you stop and decide whether to continue? Don't plan 20 steps if you haven't validated the direction.

### Reflection Gates
- [ ] Wrote task-specific check questions (Bad: "is this working?" Good: "Does the output include the progress counter?" — the answer should require evidence, not just yes/no)
- [ ] Before the riskiest step: what would make you stop and reconsider?
- [ ] If judging quality before building: is the gap worth closing?

### Extension Demonstrations
> When reality diverges from your plan, expand task.md with new gates.
> These examples show what such expansions look like.
> Each example needs: a trigger condition (*when* does this fire?), a sequence of steps with thinking checkpoints, and a task-specific question.
> **Replace them with 3 adapted to your task's Intent.**
>
> **Research Study** — *if investigation reveals an unexpected dimension:*
> Investigate: round per dimension → Checkpoint: converging? what's missing?
> → Extension: add dimension (or Critique: "what am I not seeing?")
> → Investigate: new round → Build: synthesize findings
> → Evaluate: lenses (completeness, depth, transferability) → Sufficiency: gaps worth closing?
>
> **Deep Debug** — *if initial fix attempt fails:*
> Build: attempt fix based on reading code → if fix fails → stop coding
> → Investigate: observe actual behavior (probes/logs, not source)
> → Hypothesis → Probe → Result → Checkpoint: root cause found or still guessing?
> → Build: targeted fix from root cause → Evaluate: regression test, convert probe to permanent test
>
> **Audit** — *if patterns emerge across sampled instances:*
> Investigate: sample instances (don't cover everything)
> → Checkpoint: patterns emerging? categories forming?
> → Critique: am I imposing categories or letting them emerge?
> → Investigate: test patterns against remaining instances → Checkpoint: converging or scattering?
> → Build: write findings with evidence per pattern
> → Evaluate: adversarial — "who was right?" with quoted evidence

- [ ] Replaced the 3 generic examples above with 3 adapted to this task's Intent. Removed the "Replace them" instruction line.

### Verify
- [ ] Review the work plan against extension demonstrations above. If a growth point is likely enough, add it to the plan now. Revise before executing.
- [ ] Does the work plan include moments where you stop and question your approach — not just execute?
- [ ] Checkpoint: Would a fresh agent understand this task and execute it well?
- [ ] The work plan below has the right granularity (not too coarse, not micro-steps)

---

## Work Plan

> Design phase complete. Write your work gates below.
> **For each section of work, answer two questions before writing the steps:**
> 1. What could go wrong here? (Write it down — this becomes your risk check.)
> 2. How will you know it worked? (Write a specific, falsifiable check — not "looks good" but "X contains Y".)
>
> **Between sections, ask:** Is your plan still right? If what you've learned contradicts an assumption, say so and revise the plan. Don't keep executing a plan you know is wrong.
> **Before wrapping up, ask:** What might you be wrong about? What did you assume that you haven't verified?

(write work gates here)

---

## Pre-review
- [ ] All tests pass
- [ ] No debug artifacts
- [ ] MIND_MAP.md updated if new insights emerged

## Parked
(Findings or ideas that emerged during work but are out of scope. Describe each with enough context for a future task to pick it up.)

---

## Standing Orders
- **Expand dynamically**: When you discover something you'll need to do, write new gates immediately — don't wait until you get there.
- **Steer openly**: If your direction changes, edit your open (unchecked) gates to reflect reality. The plan is alive, not a contract.
- **Never defer awareness**: The moment you realize work exists, capture it. Forgetting is the failure mode, not having too many gates.
