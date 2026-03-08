---
description: Commit, test, release plugin, and update local install
argument-hint: "[commit message]"
allowed-tools: [Read, Bash, Glob, Grep]
---

# Release

Commit the dev repo, run tests, release the plugin to the public repo, and update the local install.

**Commit message:** $ARGUMENTS (default: summarize changes from git diff)

## Instructions

Perform **every** step in order. Stop on any failure.

### 1. Pre-flight checks

```bash
git status --short
git diff --stat
```

If there are no staged or unstaged changes, report "nothing to release" and stop.

### 2. Run tests

```bash
python3.12 -m pytest tests/ -v
```

If any tests fail, stop and report. Do not proceed to commit.

### 3. Commit dev repo

Stage all relevant changed files (not `.agent/bash_history`, not `.playwright-mcp/`). Draft a concise commit message summarizing the changes (or use the user-provided message). Commit.

### 4. Run release script

```bash
bash bin/release "<commit message>"
```

This syncs distributable files to `release/`, then commits and pushes the release repo to the public plugin repo — all in one script. If it reports "No changes to release", that's fine — skip to step 6.

### 5. Update local plugin install

```bash
cd ~/.claude/plugins/marketplaces/claude-playbook-marketplace && git pull
```

### 6. Report

Show:
- Dev repo commit hash and message
- Release repo commit hash (if changed)
- Whether local plugin was updated
