---
name: merge
description: >
  Run a complete, verified branch merge in a playbook-managed repo end-to-end —
  no task wrapper, no design gates, no review tail for a clean merge. Use this
  whenever you `git merge` between branches in a repo with `MIND_MAP.md` (and
  usually `MIND_MAP_OVERFLOW.md`) at the root and per-user `.agent/<user>/`
  namespaces, especially before a PR or when pulling main. It handles the three
  things a plain `git merge` gets wrong — silent per-user cross-contamination,
  prose conflict markers in the mind map, and stale OVERFLOW node numbering —
  then runs bundled deterministic verification (ref-integrity + contamination
  + backend byte-identity) and stops for you to push, or pushes itself with
  `--push` once every gate is green.
argument-hint: <source-branch> [target-branch] [--push]
---

# Merge (playbook merge)

## What this skill is for

A playbook-managed repo namespaces agent runtime under `.agent/<user>/<files>`
so multiple humans (or one human's multiple workstations) can share the repo,
and keeps its institutional memory in `MIND_MAP.md` plus an optional
`MIND_MAP_OVERFLOW.md`. When two such lineages converge — merging `main` into a
working branch before a PR, or a feature branch into `main` — a plain `git
merge` gets four things wrong:

1. **MIND_MAP.md three-way merge** produces conflict markers in narrative prose
   that no one can resolve mechanically.
2. **Per-user file renames** (`.agent/chat_log.md` → `.agent/<userA>/chat_log.md`
   on one branch vs `.agent/<userB>/chat_log.md` on the other) trigger
   rename/rename conflicts. Git *also* content-merges using the shared ancestor
   and **writes the merged blob to BOTH destinations**, silently
   cross-contaminating each user's file. Naive `git add .agent/` ships it.
3. **`MIND_MAP_OVERFLOW.md` goes stale silently.** When only one side touched it
   since the merge base, git takes that copy verbatim *with no conflict marker* —
   carrying pre-renumber node numbers that now contradict `MIND_MAP.md`.
4. **Install-specific policy files** (canonically `.agent/current_user`) tracked
   early in some installs should be gitignored; the merge is the moment to fix it.

This skill is **self-contained**: it runs the whole procedure — the parts that
need judgment (the MIND_MAP/OVERFLOW semantic merge) and the parts that are
mechanical (contamination detection via `tasks merge-doctor`, structural checks
via the bundled `ref-integrity.py`, backend byte-identity, tests) — in one pass.
**A clean merge needs no Playbook task, no design-phase gates, and no
plan/impl/panel review tail.** Those reviews repeatedly caught only MIND_MAP /
OVERFLOW traps; with those baked into the checklist below and a bundled
verifier, they're unnecessary for a clean merge (opt into them only for a
risky/large one — see *Optional reviews*).

## How rename/rename causes silent cross-contamination

Encode this verbatim and don't paraphrase it away — it is the load-bearing
explanation for everything that follows:

> A rename/rename conflict in git is *also* a content-merge in disguise. Git
> computes a three-way content merge using the merge base as ancestor and
> writes the merged blob to **both** destination paths. For per-user files
> (`chat_log.md`, `task.md`) the two destinations are not the same logical
> file — they are separate user lineages that should diverge by design. The
> content merge produces conflict markers inside files that have no conflict
> at the path level. Worse, when the two sides only *append* to the file
> (as chat logs do), the three-way content merge can succeed without any
> conflict markers at all and still write contaminated content to both
> destinations. **Reset each destination to its own branch's version.**

The mechanical detector for this lives in `tasks merge-doctor` and you should
run it after the merge and before committing.

## MIND_MAP_OVERFLOW.md — the gotchas that make plan-review unnecessary

`MIND_MAP_OVERFLOW.md` holds the full text of nodes that appear in `MIND_MAP.md`
only as a one-line `↗` summary. Every real-world merge defect a review caught was
one of these. Encode them as a checklist — they are the heart of this skill:

1. **One-sided OVERFLOW auto-merges to STALE numbers — renumber is MANUAL.**
   When only the local branch touched `OVERFLOW.md` since the base, `git merge`
   takes that copy verbatim with **no conflict marker**, so its `[N]` nodes keep
   the *old* (pre-renumber) numbers. After you renumber local nodes in
   `MIND_MAP.md` (e.g. `[28]-[33]` → `[35]-[40]`), `OVERFLOW.md` now contradicts
   it. **Detect:** `git diff "$base" <source> -- MIND_MAP_OVERFLOW.md` (`$base` =
   `git merge-base <target> <source>`, computed in Step 1) — empty
   output means one-sided (git silently took the local copy) → renumber needed.

2. **`tasks mindmap-sync` is REQUIRED to complete the archive — run it in the
   right order.** After the manual renumber (gotcha #1), run `mindmap-sync`
   READ-ONLY to preview, then `--fix` to archive the mirrors and sync drift.
   *Ordering matters:* running `--fix` on raw, pre-renumber state would copy
   canonical summaries over stale-numbered local node text and CORRUPT the
   overflow — so renumber first, preview read-only, then `--fix`. Don't skip it:
   it is the step that actually writes the archive (Step 6).

3. **The `↗` contract.** `↗` on a MIND_MAP node means "full text lives in
   `MIND_MAP_OVERFLOW.md`" (grep `^[N]`), NOT a task-trace pointer. A `↗` node
   MUST have a matching overflow entry. Full/anchor nodes (no `↗`) appear
   **byte-identical in both files** — that mirror is by design, not duplication
   to "fix." (`ref-integrity.py` enforces this contract.)

4. **Region-scoped ref remap ONLY — never blanket file-wide.** Renumber/remap
   refs only inside the local-origin nodes. Canonical (trunk) nodes legitimately
   reference canonical numbers and must be left untouched. Use an explicit
   OLD→NEW mapping table, then pass it to `ref-integrity.py --remap` to prove the
   renumbered region carries zero stale self-refs.

5. **Auto-merged nodes OUTSIDE the conflict block can carry stale refs.** Nodes
   git auto-merged (no marker) but that originated locally still hold pre-renumber
   refs — easy to miss. After the semantic merge, scan the **whole file** for
   stale refs (`ref-integrity.py` does this), not just the conflicted region.

6. **A same-length ref remap hides from char-count drift.** `[29][30]`→`[36][37]`
   is the same length, so don't trust a "no drift" report for ref correctness;
   `ref-integrity.py --remap` is the real net.

7. **Don't re-author a stale historical archive.** A stale `OVERFLOW[N]`
   historical log is pre-existing trunk drift, not the merge's job. **Narrow the
   `↗` pointer wording** to describe honestly what it points at — never overwrite
   the archive (that destroys real history).

8. **"Trunk wins" on contested numbering AND policy.** The shared trunk's node
   numbers stay canonical; local nodes renumber to the tail. Same tie-break for
   genuinely contested policy. **But the chat-log tie-break is NOT "adopt trunk's
   tracking" verbatim:** if trunk dropped the `.agent/**/chat_log.md` ignore, the
   correct synthesis is *keep the ignore AND grandfather any already-committed
   per-user log* (git never untracks a committed file, so the committed log stays
   tracked while every other user's log stays ignored). Reading it as "un-ignore
   everyone's chat_log" re-creates the exact `git add -A` footgun Step 4 warns
   about — leaving your own untracked log one stray `add -A` from a commit.
   (Verified on a real merge: baseline kept the ignore + grandfathered the
   committed log; "tracking adopted" was a misread of its `.gitignore`.)

## Continuation policy & the push gate

**Run the whole procedure continuously without asking the user.** Everything is
local, reversible state (`git reset --hard` undoes it) right up to the push.

- **Default (no `--push`):** run all the way through the merge commit + full
  verification, then **STOP and present** — show the merge diff, the new
  `MIND_MAP.md` head, and the `ref-integrity.py` report — and let the user run
  `git push` themselves. No mid-run idle gate; the push stays a human action.
- **`--push`:** the skill pushes automatically, but **ONLY when the full green
  gate passes** (see Step 8). If any gate is red, it stops and reports which —
  it never auto-pushes a merge that failed verification.

When `tasks merge-doctor` reports findings, read the labeled sections:
`[ACTIONABLE]` (fix it, re-run), `[EXPECTED]` (mid-merge surface the semantic
merge resolves — continue), `[INFORMATIONAL]` (untracked-not-ignored path — note,
don't surface as a problem). Trust the exit code: 0 = proceed, 1 = real problem.

## Usage

```
/playbook:merge <source-branch> [target-branch] [--push]
```

- `<source-branch>` — the branch whose changes you want to bring in.
- `[target-branch]` — where the result goes (default: `main`).
- `[--push]` — opt in to automatic push once every verification gate is green.

Both branch arguments are explicit so the agent never guesses — the direction of
a merge is too consequential to infer.

---

## Procedure

### Step 1 — Diagnose (read-only)

```bash
git status
git fetch <remote>
base=$(git merge-base <target> <source>)       # the shared ancestor — reused below + in Step 7
git log --oneline <source> ^<target>          # source-only commits
git log --oneline <target> ^<source>          # target-only commits (empty ⇒ fast-forward, skill N/A)
git diff <target>..<source> --stat
git ls-tree -d --name-only <target> -- .agent/ ; git ls-tree -d --name-only <source> -- .agent/
git diff "$base" <source> -- MIND_MAP_OVERFLOW.md   # empty ⇒ one-sided overflow (gotcha #1)
```

Decide: real divergent merge or fast-forward (if `git log <target> ^<source>` is
empty, just `git merge --ff-only` — this skill doesn't apply); which side has the
**richer MIND_MAP** (presence of `[[slug]]` links → node count → char count);
which **user namespaces** exist (union of both `ls-tree` outputs — keep every
one); the **upstream remote** (`git remote`; if not exactly one, ask).

### Step 2 — Sync target, then start the merge

```bash
git checkout <target>
git merge --ff-only <remote>/<target>          # if not a clean ff, surface to user
target_before=$(git rev-parse HEAD)            # pre-merge target tip — the backend-identity baseline (Step 7d / push-gate 4). Capture it NOW: after commit, <target> advances to HEAD, so `git diff <target> HEAD` is always empty (vacuous).
git merge --no-commit --no-ff <source>         # --no-commit: fix rename/rename before committing
git status
```

**Detached HEAD / no upstream:** if the target is a detached HEAD or has no
upstream (e.g. an experiment), skip the `--ff-only` remote-sync line and merge in
place; consider tagging or branching the result so it isn't garbage-collected.

### Step 3 — Mechanical unions

For non-mind-map conflicts that are unions (`.gitignore`, `pom.xml`, etc.),
resolve so **both sides survive**. For `.gitignore` prefer `**` globstar over
`*`, drop redundant entries, and add install-local files that must not be tracked
(`.agent/current_user`): `git rm --cached .agent/current_user 2>/dev/null || true`
then add it to `.gitignore`.

### Step 4 — Per-user rename/rename rescue (the dangerous step)

For each rename/rename conflict where each side lives under a different
`.agent/<user>/...`:

```bash
git add .agent/<userA>/ .agent/<userB>/
git rm -f .agent/<old-shared-path>
tasks merge-doctor <source> <target>           # mechanical contamination check
```

If `merge-doctor` flags a file, **reset it to its own branch's version**:

```bash
git show <target>:.agent/<userA>/<rel> > .agent/<userA>/<rel>
git show <source>:.agent/<userB>/<rel> > .agent/<userB>/<rel>
git add .agent/<userA>/<rel> .agent/<userB>/<rel>
```

Re-run `tasks merge-doctor` after each reset (it's idempotent). **Never
`git add -A`** — stage explicit paths. The footgun: the *rejected* "un-ignore
everyone's `chat_log`" reading of a chat-log policy (see gotcha #8 — the correct
rule is keep the `.agent/**/chat_log.md` ignore + grandfather only already-committed
logs) leaves your own log un-ignored AND untracked, one `add -A` from an accidental
commit.

> **Speed (optional): start the test suite now, in the background.** Once code
> conflicts are resolved here, the backend/code working tree is **frozen** — the
> only remaining edits (Steps 5–6) touch `MIND_MAP.md` / `MIND_MAP_OVERFLOW.md`,
> never code. So you can kick off the backend tests in the background and let them
> run *while* you do the semantic MIND_MAP merge, then just collect the result at
> Step 7(e) — overlapping ~90s of tests with work you'd do anyway. This is a pure
> wall-clock win, not a quality trade: the same suite runs on the same code state,
> and tests stay a hard gate. Launch so the log itself records the exit status
> (don't rely on `wait <pid>` — a later agent shell isn't the parent and can't wait
> on it):
> `bash -lc 'bash .claude/bin/run-backend-tests; echo "tests exit=$?"' > /tmp/merge-tests.log 2>&1 &`
> Then at Step 7(e) you read the `tests exit=` line back. **Precondition for trusting
> the result: you made no code edit after launching** (if you did, re-run at Step 7).
> A cautious agent can skip this and just run tests inline at Step 7(e).

### Step 5 — Semantic MIND_MAP merge

A textual three-way merge produces useless prose markers — synthesize instead
(same shape of work as `/playbook:mindmap`). Pick the richer side; fold in unique
content from the simpler side (new task/artifact nodes, a `merge-<source>`
history node, updated `git-timeline`, decision node for any policy change,
routing nodes mentioning both namespaces). Drop true duplicates. Remove every
`<<<<<<`/`=======`/`>>>>>>`. Then re-read the merged map cold: can you tell what
merged and what each side contributed? If not, the History node is too thin.

### Step 6 — OVERFLOW renumber + archive (the APPLY step — MANDATORY)

This step *writes* the overflow; Step 7 only *verifies* it. They are a
complementary pair — **neither replaces the other, and this one is not optional.**

1. If Step 1 showed a one-sided `OVERFLOW.md`, **manually renumber** its local
   nodes to match the renumbered `MIND_MAP.md` (gotcha #1), region-scoped only
   (gotcha #4).
2. **Archive every full (non-↗) node the merge added on either side** into
   overflow (the anchor-mirror convention — trunk's new full nodes included).
3. Run `tasks mindmap-sync` **READ-ONLY** to preview, then **`tasks mindmap-sync
   --fix`** to write the mirrors + sync drift (gotcha #2: read-only first, never
   `--fix` on raw pre-renumber state). Narrow — don't overwrite — any stale
   historical archive pointer (gotcha #7). **`--fix` also numerically sorts the
   OVERFLOW nodes** (newly-archived nodes land in `[N]` order, not appended at EOF) —
   so you no longer reorder the overflow by hand. The sort is byte-safe (node bodies
   preserved, only inter-node separators canonicalized; already-sorted files are a
   no-op) and distinct from gotcha #1's manual *renumber* (that's about MATCHING
   numbers to MIND_MAP.md; this is just ordering).

Skipping this step is the documented failure mode. Step 7's
`ref-integrity --base` will HARD-FAIL if a new full node went unarchived — but
`--fix` here is what actually closes it.

### Step 7 — Bundled verification (the VERIFY gate — replaces the review tail)

`ref-integrity.py --base` is THE structural sign-off: it checks the reference
graph AND, differentially against the merge-base, whether this merge left a full
node unmirrored (catching a skipped Step 6). Run, in order, fix anything red:

```bash
# (a) structural + differential integrity (contiguity, ↗-contract, refs resolve,
#     no stale self-refs in the renumbered region, and — vs --base — no mirror
#     drift / newly-unarchived full node). ref-integrity.py ships in THIS skill's
#     directory; run it from the repo root:
python3 <this-skill-dir>/ref-integrity.py --remap <OLD:NEW,...> --base "$base"   # $base from Step 1
#     ALWAYS pass --base "$base" — the differential mirror + archive-completeness
#     checks (which catch a skipped Step 6) only run with it. Omit ONLY --remap
#     when the merge did no renumber.

# (b) contamination / markers / legacy paths:
tasks merge-doctor <source> <target>            # exit 0

# (c) stranded conflict markers — merge-doctor (b) already covers this: it
#     detects line-start markers (<<<<<<< / >>>>>>>) in merge-touched files only,
#     so it won't false-positive on prose/markdown like a documented '<<<<<<' or a
#     markdown '=======' underline. Don't run a raw `grep <<<<<<` (it flags those).

# (d) backend / code identity — proof the merge introduced no code of its own.
#     Diff vs the PRE-MERGE target tip ($target_before from Step 2), NOT `<target>
#     HEAD` (vacuous: <target> == HEAD both before commit and after). Pre-commit,
#     omit the second ref so it diffs the merged WORKING TREE:
git diff "$target_before" -- backend/           # every hunk must be attributable to
#     source's incoming change OR target's local addition. Often additive-only — but
#     source MAY legitimately refactor/delete, so judge attribution, don't assume
#     "additive == clean": a hunk you can't trace to either side = botched conflict
#     resolution → re-resolve. (Post-commit the same check is `git diff "$target_before"
#     HEAD -- backend/`; see push-gate 4.)

# (e) test suite — run ONCE, preserving the real exit code. If you backgrounded it
#     after Step 4 (see the Step 4 speed note) AND made no code edit since, COLLECT
#     by reading the marker the launch wrote: `grep 'tests exit=' /tmp/merge-tests.log`
#     (don't `wait <pid>` — a new shell isn't the launcher's parent). Trust it ONLY
#     if the marker is present AND the code tree is unchanged since launch; otherwise
#     run inline now. run-backend-tests is committed non-executable on some repos
#     (invoke via `bash`), and a `| tail` would mask the failure (pipe exit = tail's
#     0); wrap in bash so the status survives any caller shell (zsh has no ${PIPESTATUS}):
bash -lc 'bash .claude/bin/run-backend-tests; rc=$?; echo "tests exit=$rc"; exit "$rc"'
```

### Step 8 — Commit, present, and (optionally) push

Create the merge commit (a real one, not a fast-forward) with a message that
documents what was reconciled, the per-user resets, the MIND_MAP/OVERFLOW
resolution, and `.gitignore` changes, ending with a `Verified:` line that states
what was *actually* found — `Verified: ref-integrity clean, merge-doctor SAFE,
backend byte-identical (or additive-only, per the diff), tests green`. Don't write
"byte-identical" when the diff was additive — the push gate accepts additive-only,
so the audit trail must say which one held.

Then **present**: `git show HEAD --stat`, the full `MIND_MAP.md` head, and the
`ref-integrity.py` report.

- **Default:** STOP here. Tell the user the exact command — `git push <remote>
  <target>` — and let them run it.
- **`--push`:** push automatically **iff the full green gate holds**:
  1. clean merge state — zero unmerged paths (stranded markers are covered by
     `merge-doctor` exit 0 in condition 3, which is line-start + merge-touched);
  2. `ref-integrity.py` exit 0;
  3. `tasks merge-doctor` exit 0;
  4. `git diff "$target_before" HEAD -- backend/` (and other code dirs) shows only
     changes you've attributed to source's incoming code or target's local additions
     — NOT `git diff <target> HEAD` (vacuous, see Step 7d). Any unattributable hunk
     blocks `--push`; stop and present for the user to judge;
  5. test command exit 0 — via the bash-wrapped Step 7(e) form that returns the
     real status, never a `| tail`-masked pipe (a backgrounded run counts only if
     its `tests exit=` marker is present and no code changed since launch).

  If any of (1)–(5) is red, **do not push** — stop and report which gate failed.

If the remote rejects the push (non-bare clone with `<target>` checked out),
surface it — the fix is in the remote; **do not change remote config without
explicit consent.**

---

## Optional reviews (risky / large merges only)

A clean merge does not need them. For a large or unusual merge you may opt in:
`tasks impl-review <N>` and `tasks panel-review <N> --timeout 800`. These add
little over a byte-identical backend + passing `ref-integrity.py`, and the
oversized prompt (two mind-map files + trace) is what made panel-review time out —
so reach for them deliberately, not by default.

## Pitfalls

- **Blind `git add -A` / `git add .agent/`.** Ships the cross-contaminated blob in
  both per-user dirs. Always `merge-doctor` between Step 4 and Step 8; stage
  explicit paths.
- **Amending a published commit.** The Step 1 chat-log tidy (if any) must be gated
  on `git branch -r --contains <source>` being empty. Never amend a pushed commit.
- **Fast-forwarding when a merge commit was intended.** Step 2 uses `--no-ff` so
  history keeps the "we converged here" marker.
- **`mindmap-sync --fix` on raw post-merge state.** Corrupts stale overflow —
  renumber manually first (gotcha #2).
- **Trusting "no content drift" for ref correctness.** Same-length ref remaps hide
  from char-count drift — `ref-integrity.py --remap` is the net (gotcha #6).
- **Pushing to a checked-out remote branch.** Git refuses to protect the remote's
  working tree; the fix is in the remote, with the user's consent.

## Parameterization

| Aspect | Source |
|---|---|
| Source / target branch | Explicit args (target default `main`) |
| `--push` | Off by default; opt in per-run |
| Upstream remote | Auto-detect single remote, else ask |
| User namespaces | Auto-detect via `git ls-tree -d --name-only <branch> -- .agent/` on both |
| Richer MIND_MAP side | Auto-detect ([[slug]] links → node count → char count) |
| MIND_MAP / OVERFLOW paths | `MIND_MAP.md`, `MIND_MAP_OVERFLOW.md` at repo root |
| OLD→NEW renumber map | Built by hand in Step 6; passed to `ref-integrity.py --remap` |
| Active-user marker | `.agent/current_user`, install-local (gitignored, Step 3) |

The only literal strings the skill needs are `.agent/`, `MIND_MAP.md`, and
`MIND_MAP_OVERFLOW.md`.

## Bundled tooling

- **`ref-integrity.py`** (ships in this skill dir; pure stdlib). Structural
  verifier for the mind map: MIND_MAP ids contiguous 1..N no dups; OVERFLOW ids a
  sparse subset; every `↗` node has an overflow entry, no orphans; every `[N]`
  resolves (code fences / inline code / `[0]` excluded). With `--remap OLD:NEW,…`
  it proves the renumbered region has no stale self-refs; with `--base <ref>` only
  NEW dangling `[[slug]]` links fail. Exit 0 clean / 1 findings. `--help` for usage.
- **`tasks merge-doctor <source> <target>`** — mechanical contamination check
  (per-user cross-contamination, stranded markers, legacy `.agent/` paths) with
  stratified `[ACTIONABLE]`/`[EXPECTED]`/`[INFORMATIONAL]` output. Inspects the
  in-progress merge (`MERGE_HEAD`) or the last merge commit. Exit 0 / 1.

## Out of scope

- **`CLAUDE.md` conflicts** — install-specific; whatever git auto-merges is the
  default, surface a real conflict to the user.
- **Dormant branches rebased onto a different ancestor** — bring up to date first.
- **Three-way (3+ parent) merges** — sequence into pairwise merges.
- **Automating the semantic MIND_MAP merge itself** — the richer-side heuristic
  picks a start; the merge needs prose judgment. Encode the procedure, don't
  synthesize the output.
