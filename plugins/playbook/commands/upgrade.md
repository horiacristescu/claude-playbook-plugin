---
description: Upgrade the playbook plugin to the latest version
allowed-tools: [Bash, Read, Edit]
---

# Upgrade Playbook Plugin

Upgrade the installed playbook plugin to the latest release.

## Instructions

Run these commands in sequence. Stop on any failure.

### 1. Check current version

```bash
cat ~/.claude/plugins/marketplaces/claude-playbook-marketplace/plugins/playbook/.claude-plugin/plugin.json 2>/dev/null || echo "Not installed"
```

### 2. Remove old plugin

```bash
claude plugin marketplace remove claude-playbook-marketplace
```

### 3. Re-add marketplace and install

```bash
claude plugin marketplace add horiacristescu/claude-playbook-plugin
claude plugin install playbook@claude-playbook-marketplace
```

### 4. Run /init to update project files

Run `/init` to merge any new CLAUDE.md sections and update project wrappers. This is safe to re-run — it's idempotent.

### 5. Verify

```bash
cat ~/.claude/plugins/marketplaces/claude-playbook-marketplace/plugins/playbook/.claude-plugin/plugin.json
```

Report the old and new version numbers.
