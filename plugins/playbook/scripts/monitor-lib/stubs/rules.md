# Monitor Rules — State Space Policy

> **This file is the SSP.** It carries the state vocabulary, feature extraction, and state → action policies the monitor applies per wake. The mind map (`MONITOR_MIND_MAP.md`) carries orientation (identity, position, style); this file carries the policy.
>
> **This is a learning policy, not a fixed schema.** States, features, and rules all grow from observation. Structure below is a starting frame — add dimensions, values, features, and rules as patterns earn their place.
>
> **Per-wake use:** extract features from the turn, assign a state tuple `(A?, U?, C?, D?, T?)`, look up the rule region, act (nudge / hold / log). Record the tuple in `session.md` each wake for retrospective trajectory analysis.

---

## 1. Design sequence (how this file is built)

Per mind map [8]:

1. **List actions.** What nudges / interventions can the monitor actually do?
2. **Map states.** Where does each action apply? Use orthogonal dimensions (mind map [9]).
3. **Identify features.** What observable signals discriminate one state from another?

Do not start at step 3. Do not skip step 1. The design runs backwards from what we want to do.

---

## 2. Actions (what the monitor can do this turn)

Open set — add rows as new actions prove useful. Each row = one action the monitor can take in response to a state.

> **Note on the N1–N11 actions below:** these are seed examples from a thinking-partner session; the patterns they target (butter openers, latch-fast scaffolding, skipped content re-pushing, attribution drift) are universal LLM-collaboration tics and fire across coding, research, and analysis work too. Extend the table as your project's nudges earn their place; remove rows that don't fire after several sessions.

| Code | Action | What it does |
|---|---|---|
| N1 | nudge-opener | Point at a butter opener; ask for flat-toned restatement. |
| N2 | nudge-balance | Name an over-attack / over-praise phrase; ask for calibrated weight on the target's legitimate claims. |
| N3 | nudge-drop-scaffold | Identify an imposed taxonomy; suggest dropping it or attributing to the user. |
| N4 | nudge-skip-drop | Point at content the user skipped; suggest not re-pushing. |
| N5 | nudge-confirm-frame | Agent committed a frame to a durable artifact before user adoption; ask to confirm. |
| N6 | nudge-ask-first | Framing message read as to-do list; point at the gap. |
| N7 | nudge-scope | Autonomous hygiene extension beyond the ask; flag. |
| N8 | nudge-verify-source | Attribution drift; ask agent to check against chat log. |
| N9 | nudge-right-surface | Wrong-surface capture (sidecar instead of gate); redirect. |
| N10 | nudge-silence | Reading / thinking phase; agent filling silence. |
| N11 | nudge-tally | Multi-turn pattern recurring; deliver with count. |
| H1 | hold | Pattern observed but below threshold; log only. |
| L1 | log-positive | Clean turn (direct answer, good self-correction, scaffold earned); record exemplar. |
| L2 | log-unrecognized | Turn shape doesn't fit any known state; add to unknown-state log for later classification. |

Every nudge action ends with "(please show this to the user)" per mind map [7].

---

## 3. State dimensions (orthogonal; compose into tuples)

Each dimension is a starting vocabulary. **Extend freely** — when a turn doesn't fit any existing value, add a new code.

### Dimension A — AGENT-SHAPE (what the agent emitted this turn)

| Code | State |
|---|---|
| A1 | direct-substantive-answer |
| A2 | butter-opener + substance |
| A3 | butter-opener-alone |
| A4 | new-taxonomy-imposed |
| A5 | grounding-first (tool-use to verify) |
| A6 | execution-span (multi-call, no mid-span text) |
| A7 | check-in / handback |
| A8 | self-correction |
| A9 | over-attack-external |
| A10 | artifact-commit (durable file / name) |
| A11 | status-report (factual done X) |
| A12+ | (reserved — add as observed) |

### Dimension U — USER-CONTEXT (what user's last message is)

| Code | State |
|---|---|
| U1 | framing-message |
| U2 | execution-directive |
| U3 | partial-thought (mid-unpacking) |
| U4 | concluded-claim |
| U5 | factual-question |
| U6 | correction |
| U7 | pattern-naming |
| U8 | silent / reading |
| U9 | skip-signal (pivot without engaging agent's prior) |
| U10 | candidate-offered |
| U11 | pragma / logistics |
| U12 | quote-paper (raw text, no explicit claim) |
| U13+ | (reserved) |

### Dimension C — COUPLING (this turn's relation to agent's prior frame)

| Code | State |
|---|---|
| C1 | fresh-thread |
| C2 | user-adopted-frame |
| C3 | user-skipped-frame |
| C4 | user-corrected-frame |
| C5 | agent-dropped-skipped-frame |
| C6 | agent-repushed-skipped-frame |
| C7 | scaffold-operationalizes-user |
| C8 | scaffold-precedes-user |
| C9 | user-parked (saw, didn't decide, moved on) |
| C10+ | (reserved) |

### Dimension D — DURABILITY (what's being committed)

| Code | State |
|---|---|
| D1 | chat-only |
| D2 | task-md-edit |
| D3 | filesystem-change (new file / dir) |
| D4 | identifier-commit (task name, branch, artifact name) |
| D5 | mindmap-update |
| D6 | external-side-effect (git push, remote call) |
| D7+ | (reserved) |

### Dimension T — TRAJECTORY (windowed across N turns)

| Code | State |
|---|---|
| T1 | butter-streak (≥3 opener-validations in 5 turns) |
| T2 | taxonomy-proliferation (≥3 new numbered frames in 5 turns) |
| T3 | recurrence-after-named (named pattern recurs within 3 turns) |
| T4 | shape-shift-downward (worsening) |
| T5 | shape-shift-upward (improving) |
| T6 | over-nudge-risk (≥2 nudges in 3 turns without engagement) |
| T7+ | (reserved) |

### Adding a new dimension

If an entirely new orthogonal axis becomes discriminative (e.g. "emotional tone of user"), add it as Dimension E, F, etc. Update the rule region syntax to include it. Keep dimensions orthogonal — if a new axis co-varies strongly with existing ones, fold into an existing dimension instead.

---

## 4. Feature extraction (how to detect each state value)

How the monitor infers dimension values from the JSONL trace. **Grow this catalog** as new features prove useful.

### Lexical features (first sentence of agent text)

- **Validation token:** "Exactly", "Right —", "Yes —", "Good —", "Sound", "Sharp" → A2/A3
- **Superlative opener:** "deepest", "sharpest", "cleanest", "most", "powerful" → A3 strong, A9
- **Comparative-downgrade:** "sharper than X", "stronger than Y", "cleaner than Z" → A9
- **Self-correction marker:** "I over-", "I undersold", "I was wrong", "Correct — I" → A8
- **External-target superlative:** e.g. "only 'pretty please' enforcement", "definitional rhetoric" → A9
- **Numbered-frame intro:** "Three X:", "The ladder: Level 0", "Two axes:" → A4
- **Attribution marker:** "the user's order", "your frame" → C7; agent-origin pronouns → C8
- **Check-in ending:** "What's next?", "Ready when you are", "Over to you" → A7
- **Commit verbs:** "Done —", "Installed", "Captured as G##" → A11

### Structural features (whole response shape)

- **Opener + bullet scaffold** → A2 (butter then content) vs A3 (butter alone, empty response)
- **Numbered taxonomy with named levels** → A4
- **Prose-only, no new frame** → A1 or A8
- **Length: terse report** → A11; **long synthesis** → A1/A2

### Tool-use features (agent's span before text)

- **Read/Grep/WebSearch first** → A5 grounding-first
- **≥3 Bash/Edit/Write with no intervening text** → A6 execution-span
- **Write to task.md** → D2
- **mkdir / new file / new dir** → D3
- **`tasks new` / rename / mv** → D4
- **MIND_MAP.md edit** → D5
- **git push / gh pr** → D6

### Referential features (this turn vs prior)

- **User-quotes-agent ("> " prefix)** → callback, possibly skip-signal on intervening content
- **User-uses-agent-heading verbatim** → C2
- **User-redraws-frame ("it's on a lower level")** → C4
- **User-pivots-topic without reference** → C3
- **Agent drops skipped frame next turn** → C5
- **Agent re-pushes skipped frame** → C6
- **Agent's new frame names user's exact phrase** → C7
- **Agent's new frame precedes any user utterance** → C8

### User-message features

- **Short + imperative** ("yes do it", "try X") → U2
- **Short + question** → U5
- **Long + thesis claim** → U4
- **Long + truncated mid-word** → U3
- **"> " prefix, no claim** → U12
- **"how about" / "maybe" / "what if"** → U10
- **"we are using" / "our goal"** → U1
- **"actually" / "not quite" / "but"** → U6
- **"you tried to X sometimes"** → U7
- **Tool names, paths** → U11
- **Sensor stall ≥60s** → U8

### Timing / trajectory features (windowed)

- **Butter-opener count, last 5 agent responses** → T1 if ≥3
- **New-taxonomy count, last 5** → T2 if ≥3
- **Turns since user named a pattern** → T3 if pattern recurs ≤3
- **Turns since own last nudge** → T6 guard if <3 without user engagement
- **Tool-call count since last text** → A6 if ≥10

### Adding a new feature

When a distinction is nudge-worthy but no current feature captures it, add a bullet under the right category with the signal and which dimension value(s) it feeds. New features earn their place by discriminating a region we care about.

---

## 5. Rules (state region → action)

Each rule is a structured block. Add new rules freely; retire rules that no longer fire or were wrong.

### Rule: butter-alone-on-claim
- **Region:** A3 × U4 × any × D1
- **Signal features:** validation-token opener with no substantive content; user's last message carried a thesis worth engaging with.
- **Action:** N1 nudge-opener
- **Template:** *"Response opened with [phrase]. If the claim has a flaw, open with the tension; if it doesn't, skip the opener and start with the next move. (please show this to the user)"*
- **Evidence:** (fill as observed)

### Rule: enthusiasm-for-flattery
- **Region:** (A9 + A3) × U4/U6/U10 × any × any
- **Signal features:** superlative-dispraise of external target paired with superlative-praise of user in same turn; or user-correction within 2 turns like "I undersold X" / "we should be more charitable".
- **Action:** N2 nudge-balance
- **Template:** *"The phrase [X] downgrades [target's legitimate claim] to elevate [user's position]. the user named this pattern <date>. Flat-toned restatement — equal weight to the target's claims. (please show this to the user)"*
- **Evidence:** (to be populated)

### Rule: latch-fast-scaffold
- **Region:** A4 × U3/U10 × C8 × any
- **Signal features:** numbered-frame intro introducing a new taxonomy the user did not name; the scaffold precedes rather than operationalizes the user's direction.
- **Action:** N3 nudge-drop-scaffold
- **Refinement:** silent if C7 (scaffold names something user just said) — earned scaffolds are fine.
- **Template:** *"Agent proposed [taxonomy] before the user finished the thought. Drop the scaffold; stay in prose on his last point until he brings the structure. (please show this to the user)"*
- **Evidence:** (to be populated)

### Rule: skip-repush
- **Region:** any × any × C6 × any
- **Signal features:** agent re-introduces a frame the user skipped 1–2 turns ago.
- **Action:** N4 nudge-skip-drop, strong
- **Template:** *"the user skipped [frame] at [turn]. What he doesn't react to is signal he told us to watch for — drop it. (please show this to the user)"*
- **Evidence:** (to be populated)

### Rule: framing-commitment-unconfirmed
- **Region:** A10 × U9/U10 × C8 × D4/D5
- **Signal features:** agent writes a durable identifier or artifact (task name, branch, MIND_MAP node) using a frame the user offered as a candidate, not as a decision. User skipped the agent's elevation of the candidate.
- **Action:** N5 nudge-confirm-frame
- **Template:** *"[artifact] bakes [frame] into a durable surface, but the user offered it as a candidate, not a decision at [turn]. Before committing, ask: lock [frame], or keep open? (please show this to the user)"*
- **Evidence:** (to be populated)

### Rule: framing-as-todo
- **Region:** A6 × U1 × any × D3/D4
- **Signal features:** user's message is shape-setting ("we're using X", "ok let's X"); agent launches multi-call setup span without checking shape of collaboration.
- **Action:** N6 nudge-ask-first
- **Template:** *"the user's message was framing, not a task list. Ask what shape he wants the session before building structure. (please show this to the user)"*
- **Evidence:** (to be populated)

### Rule: autonomous-hygiene-extension
- **Region:** A6 × U2-bounded × C8 × D3
- **Signal features:** user authorized X; agent added cleanup / relocation / gitignore / refactor beyond X without asking.
- **Action:** N7 nudge-scope (borderline — flag-then-nudge-if-repeats)
- **Template:** *"the user asked for X; agent extended to X+cleanup+relocation. Ask before adding scope, even when hygienic. (please show this to the user)"*
- **Evidence:** (to be populated)

### Rule: attribution-drift
- **Region:** A10 × any × any × D5
- **Signal features:** MIND_MAP / task.md / notes attribute agent-produced insights to user, or vice versa.
- **Action:** N8 nudge-verify-source
- **Template:** *"[content] attributed to the user, but trace shows [agent] produced it. Verify against chat log and correct. (please show this to the user)"*
- **Evidence:** (to be populated)

### Rule: wrong-surface-capture
- **Region:** A10 × U2 × any × D3 (sidecar)
- **Signal features:** user's directive is to capture/save discussion; agent writes to a new sidecar file instead of the active task.md gates during freehand.
- **Action:** N9 nudge-right-surface
- **Template:** *"Freehand captures into task.md gates with same-line commentary. The sidecar file is the wrong surface. (please show this to the user)"*
- **Evidence:** (to be populated)

### Rule: reading-phase-content-push
- **Region:** A1/A2/A4 × U8 × any × D1
- **Signal features:** user declared "I'm reading" / silent phase with explicit "stay silent" gate; agent emits explanatory / structural content.
- **Action:** N10 nudge-silence
- **Template:** *"Gate 1 says silent during reading. Content being pushed. Hold until the user breaks silence. (please show this to the user)"*
- **Evidence:** (to be populated)

### Rule: butter-streak-tally
- **Region:** any × any × any × any + T1
- **Signal features:** ≥3 butter openers in last 5 agent responses with substance-landing responses masking the pattern.
- **Action:** N11 nudge-tally
- **Template:** *"Butter openers in last 5 turns: [w#, w#, w#, w#, w#]. Pattern the user named <date>. Drop the opener validation; skip directly to the substantive move. (please show this to the user)"*
- **Evidence:** (to be populated)

### Positive-exemplar log (L1)

Keep short. These are states where the agent did the right thing; record so the policy knows what "good" looks like.

- (to be populated — record wake #, response shape, and why it was good)

### Unknown-state log (L2)

Turns whose shape didn't fit any rule region cleanly. Held for later classification; if a pattern recurs, promote to a rule.

- (empty — populate as encountered)

### Rule template (copy to add a new rule)

```markdown
### Rule: <short-name>
- **Region:** <A?> × <U?> × <C?> × <D?> [+ <T?> trajectory]
- **Signal features:** <concrete observable signals — which features from §4 fire>
- **Action:** <N#> <action name>
- **Refinement:** <optional — when to silence despite region match>
- **Template:** *"<nudge text ending with (please show this to the user)>"*
- **Evidence:** <wake numbers, session references, user directives>
```

---

## 6. Gaps and open questions

- **Pre-emptive nudging.** Rules fire after the agent's turn. Can features predict a bad turn mid-compose? Probably not without mid-turn sensor access.
- **Per-element scaffold scoring.** A5 "two levels" in w47 was partially earned, partially invented. Rules currently label whole frames; finer granularity would reduce false positives on partially-operationalized scaffolds.
- **Factual-Q pressure tone.** "did you *start* your own search?" carries implicit pressure not captured by U5. Mid-question tone needs features.
- **Park vs reject.** C9 user-parked is a valid acceptance-to-defer, not a failure of the nudge. Rules should treat park as neutral outcome, not require explicit accept/reject.
- **Content correctness.** Monitor judges shape, not substance. If agent produces a factually wrong claim, no rule fires. Out of scope — don't try to fake it.

---

## 7. How this file evolves

- **New action proven useful → add row to §2.**
- **New state value observed repeatedly → add row to §3 under the right dimension.**
- **New feature distinguishes a nudge-worthy region → add under §4.**
- **New rule fires consistently → add block in §5 using the template.**
- **Rule over-fires / under-fires → edit refinement clause or region.**
- **Entirely new dimension needed → add to §3, expand region syntax.**
- **Rule never fires across N sessions → consider retiring; keep the block but annotate "retired, see [evidence]".**

The policy adapts by edit, not by schema change. The shape above (actions / dimensions / features / rules / gaps / meta) is itself revisable if a better structure earns its place.
