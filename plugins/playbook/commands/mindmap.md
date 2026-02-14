---
description: Generate a mind map by analyzing the current codebase
allowed-tools: [Read, Write, Edit, Bash, Glob, Grep, Task]
---

Generate a populated `MIND_MAP.md` for this project by following the mind map generation process.

## Process

1. **Propose an outline first** — list planned node IDs and titles. Wait for user approval before writing.
2. **Follow the three phases** from the `mindmap-gen.md` skill: scan codebase, mine git history, construct map.
3. **Use the format** from the `mindmap.md` skill: single-line nodes, natural linking, routing nodes [1-5].
4. **Write to `MIND_MAP.md`** in the project root. If one exists, replace the scaffold content but preserve any existing populated nodes.

## Key Rules

- Each node must be a **single line** (grep-friendly)
- Target **20-50 nodes** with cross-links
- Routing nodes [1-5] link to everything else
- Links embedded naturally in text, not clustered at end
- Include git history with commit hashes
- Reference actual file paths, function names, numbers
