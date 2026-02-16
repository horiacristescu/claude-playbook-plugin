---
name: playbook
description: >
  Composable workflow patterns for task execution. Five core patterns (Build,
  Investigate, Evaluate, Decide, UI Debug) plus structural reflection gates.
  Pick what fits, compose as needed. The rhythm matters more than the content.
argument-hint: [pattern-name]
---

# Playbook

## Rhythm

Every task follows a rhythm: **push → stop → push → stop → close**.

Pushing is doing the work. Stopping is questioning the work. Tasks that are
all push produce shallow output. Tasks that alternate push and stop produce
depth. Mature adoption shows the full pattern: Design Phase as foundation,
quantified checkpoints, critique at every transition. The reflection gates
below enforce the stops.

### Reflection Gates

Insert these between work sections. They are not optional. Their purpose
is mode-switching: forcing the shift from collecting to interpreting. Without
them, the agent stays in action mode and produces shallow output.

**Critique** (use FIRST — before diving in, and when output feels thin):
```
- [ ] Critique: What's shallow here? What am I avoiding? What would a skeptic ask?
```
Critique was the highest-leverage gate in the first task that used it. Starting
with "what's wrong with the previous approach?" shaped everything that followed.
Use it at task start, not just when struggling.

**Checkpoint** (after completing a work section):
```
- [ ] Checkpoint: What did I just learn that I didn't expect? Is the approach working? Scope check: has scope changed from the original Intent? Continue, replan, or split?
```
The question "what didn't I expect?" is more generative than "is this working?"
It forces noticing, not just confirming. **Investigation checkpoints must include
a concrete example** — "Given input X, parse returns Y with structure Z" — not
just "I understand the interface." Run-007 said "I understand parse()" then got
the data model wrong. A concrete example would have caught it.

**Replan** (when reality diverges from the plan):
```
- [ ] Replan: Original assumption was X. Reality is Y. New approach:
```

Use Critique at the start and at transitions. Use Checkpoint after every
pattern section. Use Replan when stuck or when critique reveals a gap.

### Emergent Gate Types

- **Parked:** Explicit "not doing this" with enough context for a future task to pick it up. Prevents scope creep while preserving work.
- **Extension:** Task spawns new scope within the document, with Critique at the boundary. Scope grows explicitly, not silently.
- **Adversarial examples:** "Who was right?" with quoted evidence. Tests the model against reality, not just coverage metrics.
- **Quantified checkpoints:** Numbers, not yes/no. "57 tests doing real work" beats "tests look good."

---

## Design Phase

The single biggest quality predictor across 14 analyzed tasks. Tasks with
thorough Design Phases produced better work than tasks that rushed them —
regardless of pattern choice or task complexity.

Design Phase lives in the base template. The playbook's job is to explain why
it matters and how to do it well:

- **Write 1-sentence answers**, not bare checkmarks. A checked box with no
  annotation means you skipped the thinking.
- **Define OUT of scope** before writing gates. Scope creep starts when
  boundaries aren't explicit.
- **Write task-specific checkpoint questions at authoring time.** Bad: "is this
  working?" Good: "Does the output include the progress counter?" The quality
  of checkpoints is determined at task creation, not during execution.
- **If >15 gates or uncertain approach:** add Decide gates before Build. Don't
  plan 20 steps in the dark.

The Design Phase is orientation, not ceremony. Complete it quickly but honestly.
The goal is to catch misframing (picking the wrong pattern), overscoping
(planning for 82 files when 10 suffice), and missing reflection gates before
work begins.

---

## Patterns

### Build

Step-test interleave. For implementing features, refactoring, any code work.

```markdown
## Build: [what]

- [ ] Step 1: [what to implement]
- [ ] Test: [what to verify]
- [ ] Checkpoint: [task-specific question — what did I learn? scope still matching Intent?]
- [ ] Step 2: [what to implement]
- [ ] Test: [what to verify]
- [ ] Checkpoint: [what's surprising? scope changed? continue, replan, or split?]

### Pre-review
- [ ] All tests pass
- [ ] No debug artifacts
- [ ] MIND_MAP.md updated if new insights emerged
```

**Disciplines:**
- Small steps. Each step has a test.
- If tests break, revert and try smaller.
- No feature work during refactoring, no refactoring during feature work.
- For hooks/CLI/integrations: test by running the real system, not just unit tests.
- Right-sizing: 2-step change → 4-5 gates. Standard feature → 6-8 gates. Research → 10-15 gates. 20+ gates → have you validated the approach first?

---

### Investigate

Observe-hypothesize-test-conclude. For debugging, exploring, researching.

```markdown
## Investigate: [what]

### Observation
- **What I see:**
- **What I expected:**
- **Evidence:**

- [ ] Symptom documented

### Round 1: [focus area]
- **Hypothesis:** [state before testing]
- **Test:** [what to check]
- **Result:** [what happened]
- [ ] Checkpoint: converging or scattering? [task-specific question]

### Round 2: [focus area] (if needed)
- **Hypothesis:**
- **Test:**
- **Result:**
- [ ] Checkpoint: [task-specific question] Scope still matching Intent?

(add rounds as needed — if position stops changing for 2 rounds, stop)

### Conclusion
- **Root cause / finding:**
- **Evidence that confirms:**
- **What I didn't know before:**
- **What emerged that I didn't expect:**

- [ ] Conclusion supported by evidence
```

**Disciplines:**
- For incident/bug tasks: start with a Problem Summary (timeline, impact, what's known) before the hypothesis table.
- State the hypothesis before testing it. No fishing.
- Auto-revert failed fix attempts (debugger mode).
- Convergence check: if position stops changing for 2 rounds, stop (researcher mode).
- Go broad first (structure, stack, entry points), then drill deep (explorer mode).

---

### Evaluate

Pre-check, lenses, verdict. For reviewing, testing, assessing quality.

```markdown
## Evaluate: [what]

### Pre-check
- [ ] Artifact exists and is complete
- [ ] Context understood (intent, constraints)

### Lenses
| Lens | Assessment | Issues |
|------|------------|--------|
|      |            |        |
|      |            |        |
|      |            |        |

Common lenses: intent alignment, architecture, test quality, security,
readability, edge cases, adversarial inputs, property invariants.

- [ ] All relevant lenses assessed
- [ ] Checkpoint: am I being thorough or just checking boxes?

### Verdict
- **Decision:** [PASS/FAIL or APPROVE/REJECT]
- **If reject, required changes:**
- **If approve, caveats:**
```

**Disciplines:**
- Think adversarially: how could this fail? What assumptions are implicit?
- Logic over style. Leave formatting to linters.
- One-shot judgment. No iterative negotiation.
- Spec-derived tests beat implementation-derived tests.

**For systematic audits** (>3 lenses, like an 8-lens test suite review):
cite file:line evidence per assessment. The verdict is a prioritized list of
findings, not a single PASS/FAIL. Add a Sufficiency gate before acting on gaps.

---

### Decide

Options, comparison, commitment. For planning, architecture choices, tradeoffs.

**For minor decisions, use inline format:**
```
- [ ] Decide: A is ___. B is ___. Choosing ___ because ___.
```

**For major decisions (hard to reverse, multiple stakeholders, architectural), use the full pattern:**

```markdown
## Decide: [what]

### Context
- **Why this decision now:**
- **Constraints:**
- **Reversibility:** [easy | hard | one-way]

### Options
| Option | Pros | Cons | Cost to try | Cost to undo |
|--------|------|------|-------------|--------------|
| A:     |      |      |             |              |
| B:     |      |      |             |              |

- [ ] At least 2 options with pros/cons
- [ ] Critique: am I anchored on one option? What's the strongest case for the other?

### Decision
- **Chosen:**
- **Primary reason:**
- **What would make us revisit:**
- [ ] Decision recorded
```

**Disciplines:**
- Strongest counterevidence required, not just supporting evidence.
- Fine decomposition for uncertain work, coarse for known patterns.
- Each sub-task should be solvable in one session.

---

### UI Debug

Script-probe-screenshot-diagnose-fix. For UI bugs that don't resolve on first try.

**When to use:** A UI fix looks correct in source but doesn't work in the browser.
Stop reading code. Write a Playwright test that observes the actual DOM.

```markdown
## UI Debug: [what's broken]

- [ ] Prefetch API — is server data correct?
- [ ] Script app to broken state (navigate, click, wait for render)
- [ ] Probe DOM: `$$eval` for className, computed styles, `data-*` attributes
- [ ] Screenshot to `/tmp/debug-<name>.png`
- [ ] Diagnose: write root cause before fixing
- [ ] Fix, re-run probe, convert debug test to regression test
```

**Disciplines:**
- DOM is truth, source code is theory. One `$$eval` beats ten minutes of code reading.
- Add `data-*` attributes to expose render-time values — read them back in the test.
- Prefetch the API first. Half of "frontend bugs" are actually wrong server data.
- Screenshot + DOM probe cover both layers (CSS and structure). Always do both.
- Convert debug test to regression test before closing.

Starter template, techniques, and reference example: [`ui-debug.md`](ui-debug.md)

---

## Composing Patterns

Tasks rarely fit one pattern cleanly. Compose:

- **Feature:** Decide (if multiple approaches) → Build → Evaluate (self-review)
- **Bug fix:** Investigate → Build (the fix) → Evaluate (verify fix + no regression)
- **Research:** Investigate (rounds) → Decide (what to do with findings)
- **Refactor:** Evaluate (current state) → Build (restructure) → Evaluate (same behavior?)
- **Spike:** Decide (what to try) → Build (prototype) → Evaluate (feasible?)
- **Incident:** Problem Summary → Investigate (triage) → Build (fix) → Evaluate (verify + no regression) → Build (harden)
- **UI bug:** Build (attempt fix) → UI Debug (if fix fails — probe DOM, diagnose) → Build (targeted fix)

Insert **Checkpoint** gates between pattern transitions. Insert **Critique** when shifting from investigation to implementation (the highest-risk transition — premature closure).

**Evaluate → Build needs a sufficiency gate.** Before acting on gaps found during Evaluate, ask: "Is the gap worth closing, or is the current state sufficient?" Evaluate will always find gaps. The question is whether they matter enough to justify the cost of closing them. Add this gate between Verdict and Build: `- [ ] Sufficiency: gaps found are cosmetic / gaps materially affect correctness — action justified?`

**When redoing work:** Start with Critique of the previous attempt. One redo
started by critiquing the prior attempt's shallow findings — that single gate
shaped the entire second pass. Without it, the redo would have been the same
as the first attempt, just slower.

**Customize checkpoint text.** Generic "is this working?" produces generic answers.
Write task-specific checkpoint questions at task creation time (e.g., "what did
depth reveal that sampling missed?" not just "approach working?").

---

## Commit Gate

After all work patterns complete, before code becomes permanent:

```markdown
### Commit
- [ ] Evidence trail: each pattern section filled, not skipped
- [ ] Tests passing now (re-run, don't trust memory)
- [ ] No debug artifacts, temp files, or accidental changes
- [ ] Diff contains only intended changes
- [ ] Mind map updated with what changed and what was learned
```

---

## Anti-Patterns (from real experience)

- **All push, no stop.** One task cleared 8 gates in sequence with no replanning — produced confidently wrong findings. The same task redone with reflection gates replanned once and caught 3 inflated claims. This is the default mode; it takes ~10-15 tasks to outgrow.
- **Coding during discussion.** If the task is in Decide or Investigate, don't write production code. The agent's default is to start coding when a problem is described.
- **Churning on failure.** If tests fail twice on the same approach, Replan — don't loop. The agent's default is to retry harder, not rethink.
- **Skipping the critique.** The first critique gate ever used was the single highest-leverage gate in that task. It turned a redo into a targeted investigation. The gate that feels unnecessary is the one that matters most.
- **Premature closure.** Moving from Investigate to Build before the conclusion is solid. This is the highest-risk transition in any task.
- **Pre-imposed categories.** One task imposed evaluative buckets before scanning; data was fitted to headings. Let categories emerge from the data instead.
- **Action bias after Evaluate.** Evaluate always finds gaps — you can always find untested code, unhandled edge cases, missing docs. The question isn't "are there gaps?" but "is the gap worth closing?" The Evaluate → Build composition has a ratchet toward action. The missing gate: "Is the current state sufficient for the stated intent, or does the gap materially affect safety/correctness?" If the verdict is PARTIAL PASS and the gaps are edge cases that don't happen in practice, the right action is STOP, not BUILD.
- **Over-planning without validation.** One task planned 22 gates; the right answer was 2 lines of code. If you're writing 15+ gates without a Decide gate first, you're planning in the dark. Validate the approach before committing to a large plan.
- **Working without a task.** Gates are invisible without a task file — this is the highest-leverage failure because everything downstream (reflection, checkpoints, gate echo) depends on a task existing. Create or activate a task before doing work.
- **Overscoping.** The agent defaults to maximal scope; the user must constrain. Start with the smallest sample that answers the question. One analysis scoped from 82 files to 10 — and 10 was enough.

---

## Mind Map

Format reference and generation guide for project mind maps (`MIND_MAP.md`).

- **Format:** Node structure, hierarchy, link principles, templates — [`mindmap.md`](mindmap.md)
- **Generation:** Codebase analysis → populated mind map in three phases — [`mindmap-gen.md`](mindmap-gen.md)

---

> Evidence base: patterns and anti-patterns derived from 14 tasks across 2 projects. Details: `.agent/tasks/084-workflow-pattern-analysis/findings.md`
