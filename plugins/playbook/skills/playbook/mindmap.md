# Mind Map — Format Reference

## Node Format

```
[N] **Title** - Text with [link-nr] embedded naturally. Dense but readable,
3-8 sentences per node. Every sentence adds information.
```

Every `MIND_MAP.md` starts with this header:

```markdown
> **For AI Agents:** This mind map is your primary knowledge index. Read overview
> nodes [1-5] first, then follow links [N] to find what you need. Always reference
> node IDs. When you make changes, update outdated nodes immediately — especially
> overview nodes. Add new nodes only for genuinely new concepts. Keep it compact
> (20-50 nodes typical). The mind map wraps every task: consult it, rely on it,
> update it.
```

## Node Hierarchy

| Level | Nodes | Content |
|-------|-------|---------|
| Foundation | 1-5 | Project overview, core theory, data flow, frontend arch, backend arch |
| Systems | 6-15 | Major subsystems, key features, components |
| Implementation | 16-20 | Tech stack, history, workflow, roadmap, principles |
| Deep dives | 21+ | Algorithms, optimization, error handling, performance |

## Link Principles

- Links `[N]` flow naturally in sentences, not listed at the end
- Every node links to 2-3+ others; important nodes link to 5-10
- Bidirectional: if [5] mentions [12], [12] should mention [5]
- Clusters form around major concepts (all visualization nodes link to each other)
- Progressive paths: [1]→[2]→[7]→[8] should form a coherent learning path

**Density targets:**
- Overview nodes: 5-10 links
- System nodes: 3-7 links
- Implementation nodes: 2-5 links
- Specialized nodes: 2-4 links

## Writing Style

- Use specific names: "The PCAVisualizer class in pcaVisualizer.ts" not "the visualizer"
- Include numbers: "5-10 features", "23,000+ lines"
- Reference actual filenames, function names, variable names
- Explain WHY decisions were made, not just WHAT exists
- 100-300 words per node — substantial but scannable

## Node Templates

**System node:**
```markdown
[N] **System Name** - Brief definition and purpose [parent-node]. The system
consists of COMPONENT1 which handles TASK1 [detail-node], COMPONENT2 for TASK2
[detail-node]. Implementation resides in path/to/files using TECHNOLOGY
[tech-node]. Integrates with EXTERNAL_SYSTEM [integration-node] through
API_METHOD. Key parameters include PARAM1 (range, default) and PARAM2
(type, purpose) [parameter-node].
```

**Implementation node:**
```markdown
[N] **Algorithm Name** - Technical description [parent-node][theory-node].
The ClassName in path/to/file.ts implements APPROACH [architecture-node].
Steps: first STEP1 [step1-node], then STEP2 [step2-node], finally STEP3
producing OUTPUT [step3-node]. Parameters include PARAM1 (type, default)
and PARAM2 (range, effect). Performance: TIME_COMPLEXITY for typical
datasets [performance-node].
```

**History node:**
```markdown
[N] **Development History** - Evolved over TIMESPAN from DATE1 to DATE2
[overview-node]. Commit HASH1 (DATE) created initial structure with FRAMEWORK
[theory-node]. Commit HASH2 (DATE) was the major milestone: N insertions
creating SYSTEM1 [node], SYSTEM2 [node]. Commit HASH3 (DATE) introduced
FEATURE [feature-node]. Total: N commits, ~N lines, transforming from
STATE1 to STATE2 [principle-node].
```

## Quality Checklist

- All major systems/features have nodes
- Every significant file/component is mentioned
- Development history captured with commit hashes
- Every node has 2+ links, important nodes 5+
- Links embedded naturally in text, bidirectional where appropriate
- Each node has a clear title, first sentence defines the concept
- Actual file/function names used, not generic descriptions
