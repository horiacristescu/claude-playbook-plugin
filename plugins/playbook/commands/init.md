---
description: Initialize or upgrade a project for playbook workflow
argument-hint: "[project name]"
allowed-tools: [Read, Write, Edit, Bash, Glob, Grep]
---

# Playbook Init

Initialize this project for playbook-managed workflow. Safe to re-run (idempotent) — upgrades template sections without losing project-specific content.

**Project name:** $ARGUMENTS (use the directory name if not provided)

## Instructions

Perform these steps in order. Skip any step where the artifact already exists and is up-to-date.

### 1. Create `.claude/scripts/tasks` wrapper

Check if `.claude/scripts/tasks` exists. If not, create it:

```bash
mkdir -p .claude/scripts
```

Write `.claude/scripts/tasks` with this content — a wrapper that finds and delegates to the plugin's tasks CLI:

```bash
#!/bin/bash
# Playbook tasks CLI wrapper — delegates to plugin's tasks script
PLUGIN_TASKS="$(find ~/.claude/plugins -path '*/playbook/scripts/tasks' -type f 2>/dev/null | head -1)"
if [ -z "$PLUGIN_TASKS" ]; then
    echo "Error: playbook plugin not found." >&2
    echo "Install: /plugin marketplace add horiacristescu/claude-playbook-plugin" >&2
    exit 1
fi
exec "$PLUGIN_TASKS" "$@"
```

Then `chmod +x .claude/scripts/tasks`.

If the wrapper already exists, check if it delegates to the plugin. If it's a different script (e.g., from a manual install), leave it alone.

### 2. Create or update CLAUDE.md

Read the template from the plugin: find the file matching `**/playbook/scripts/CLAUDE.md.template` inside `~/.claude/plugins/`.

**If CLAUDE.md does not exist:** Write the template as CLAUDE.md. Replace "Project Name" with the actual project name.

**If CLAUDE.md already exists:** Read both files. The template has sections marked with `<!-- PLAYBOOK TEMPLATE -->` comments. Follow the merge instructions in the template header: update playbook sections to match the template, preserve all project-specific content. Don't duplicate sections that already match.

### 3. Create MIND_MAP.md if missing

If `MIND_MAP.md` does not exist at project root, create it with this scaffold:

```markdown
# Mind Map — [Project Name]

## What This Project Does
(Brief description)

## Architecture
(Key components, how they connect)

## Decisions
(Important choices made and why)

## History
(Chronological log of significant changes)
```

If it already exists, **do not modify it**.

### 4. Create .agent/ directory if missing

```bash
mkdir -p .agent/tasks
```

If `.agent/` already exists, leave it alone.

### 5. Verify

Run `.claude/scripts/tasks bootstrap` to verify everything works. Report what was created or updated.
