---
description: Initialize or upgrade a project for playbook workflow
argument-hint: "[project name]"
allowed-tools: [Read, Write, Edit, Bash, Glob, Grep]
---

# Playbook Init

Initialize this project for playbook-managed workflow. Safe to re-run (idempotent) — upgrades template sections without losing project-specific content.

**Project name:** $ARGUMENTS (use the directory name if not provided)

## Instructions

Perform **every** step in order.

### 1. Run mechanical setup

Find and run the plugin's `scripts/init` script, which handles: `.claude/settings.json` permissions, `.agent/tasks/` directory, `MIND_MAP.md` stub, and `.claude/bin/` wrappers.

```bash
INIT_SCRIPT="$(find ~/.claude/plugins -path '*/playbook/scripts/init' -type f 2>/dev/null | head -1)"
if [ -z "$INIT_SCRIPT" ]; then
    echo "Error: playbook plugin not found." >&2
    exit 1
fi
bash "$INIT_SCRIPT" "<project name>"
```

Check the output. If it reports any failures, stop and fix before continuing.

### 2. Create or update CLAUDE.md

This is the step that requires intelligence — the rest was mechanical.

Find the template: `find ~/.claude/plugins -path '*/playbook/scripts/CLAUDE.md.template' -type f 2>/dev/null | head -1`

**If CLAUDE.md does not exist:** Write the template as CLAUDE.md. Replace "Project Name" with the actual project name.

**If CLAUDE.md already exists:** Read both files. Follow the merge instructions in the template header: update playbook sections to match the template, preserve all project-specific content. Don't duplicate sections that already match.

### 3. Generate mind map if stub

If `MIND_MAP.md` contains only a stub (just a `# Mind Map` heading with no real content), run `/mindmap` to generate it from the codebase.

If it already has substantive content, leave it alone.

### 4. Verify

Run `.claude/bin/tasks bootstrap` to verify everything works. Report what was created or updated.
