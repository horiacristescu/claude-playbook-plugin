#!/usr/bin/env python3
"""ref-integrity.py — structural verifier for MIND_MAP.md (+ MIND_MAP_OVERFLOW.md).

Bundled with the `merge` skill. Pure stdlib (Python 3.10+, no third-party deps).
Run after a semantic mind-map merge to deterministically catch the structural
defects that plan/panel reviews repeatedly caught by hand.

NON-DIFFERENTIAL CHECKS (always — operate on the final files alone):
  1. MIND_MAP node ids are contiguous 1..N with no duplicates.
  2. OVERFLOW node ids are a SUBSET of MIND_MAP ids. Overflow is sparse, NOT
     contiguous — asserting contiguity on it is a false failure, so we don't.
  3. ↗-contract: every `↗`-marked MIND_MAP node has a matching overflow entry,
     and overflow has no orphan nodes (covered by #2).
  4. Every `[N]` reference (either file) resolves to a defined MIND_MAP node.
     Fenced code blocks and inline `code` are stripped first, and `[0]` is
     excluded (it comes from prose like ${BASH_SOURCE[0]}, never a node id),
     so code fragments such as sys.argv[1] / list[99] don't false-positive.

DIFFERENTIAL CHECKS (need merge context — opt-in; skipped with a note otherwise):
  5a. --remap OLD:NEW,...  The renumbered (NEW) nodes must carry no stale
      `[OLD]` self-references. This catches the "silent stale overflow" defect
      (a stale ref still *resolves*, so check #4 alone passes it).
  5b. --base <git-ref>     A dangling `[[slug]]` link fails ONLY if it is NEW
      relative to the base mind-map; inherited forward-markers are valid. Without
      --base, dangling `[[slug]]` links are reported as warnings (non-fatal).

EXIT: 0 = clean (warnings allowed); 1 = hard findings; 2 = usage/IO error.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

_NODE_DEF = re.compile(r'^\[(\d+)\]')          # a node DEFINITION (line start)
_REF = re.compile(r'\[(\d+)\]')                # any [N] reference
_SLUG = re.compile(r'\[\[([^\]]+)\]\]')        # a [[slug]] link
_TITLE = re.compile(r'^\[\d+\]\s*\*\*(.+?)\*\*')
_HEADING = re.compile(r'^#{1,6}\s')


def _strip_code(text: str) -> str:
    """Drop fenced code blocks and inline backtick spans before ref scanning."""
    out, in_fence = [], False
    for line in text.splitlines():
        if line.lstrip().startswith('```'):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return re.sub(r'`[^`]*`', '', '\n'.join(out))


def _node_start_lines(text: str) -> list[tuple[int, int]]:
    """Fence-aware node-definition line scan — the ONE node-boundary detector
    shared by `_node_ids`, `_node_bodies`, and `_defined_slugs`. (A vendored copy
    of the tasks-package `_node_starts`: ref-integrity ships standalone in the
    skill dir and cannot import from `tasks`.)

    Returns ``[(line_index, node_id)]`` for every ``^[N]`` line NOT inside a ```
    code fence, so a fenced ``[9]`` example line is never a ghost node. Line model
    is ``text.split('\\n')`` (matches the legacy `_node_bodies` reconstruction)."""
    starts: list[tuple[int, int]] = []
    in_fence = False
    for i, line in enumerate(text.split('\n')):
        if line.lstrip().startswith('```'):
            in_fence = not in_fence
            continue
        if not in_fence and (m := _NODE_DEF.match(line)):
            starts.append((i, int(m.group(1))))
    return starts


def _node_ids(text: str) -> list[int]:
    """Node-definition ids in document order (repeats kept → enables dup check).
    Fence-aware: a fenced ``[N]`` line is not counted."""
    return [nid for _, nid in _node_start_lines(text)]


def _node_bodies(text: str) -> dict[int, str]:
    """node_id -> full body (multi-line preserved). Node STARTS are fence-aware
    (a fenced ``[N]`` line is not a boundary). A trailing non-node section (e.g.
    ``## Legacy``) is trimmed at the first heading after the first line — but
    headings INSIDE a fenced code block (e.g. a ``# comment`` line) don't count,
    so a code example in an overflow body isn't truncated."""
    bodies: dict[int, str] = {}
    all_lines = text.split('\n')
    starts = _node_start_lines(text)
    for k, (idx, nid) in enumerate(starts):
        end_idx = starts[k + 1][0] if k + 1 < len(starts) else len(all_lines)
        lines = all_lines[idx:end_idx]
        end = len(lines)
        in_fence = False
        for i in range(1, len(lines)):
            if lines[i].lstrip().startswith('```'):
                in_fence = not in_fence
                continue
            if not in_fence and _HEADING.match(lines[i]):
                end = i
                break
        bodies[nid] = '\n'.join(lines[:end]).strip()
    return bodies


_SUMMARY_AFTER_TITLE = re.compile(r'^\[\d+\]\s+\*\*.+?\*\*\s*↗')   # [N] **title** ↗ …
_SUMMARY_TERMINAL = re.compile(r'↗\s*$')                          # … full detail ↗


def _is_summary(body: str) -> bool:
    """True if the node is a ``↗`` summary. The marker appears in one of two
    conventional positions — right after the bolded title (``[N] **title** ↗``)
    OR at the very end of the definition line (``… full detail ↗``). Matching
    only these positions (not any ``↗`` on the line) means a node whose *prose*
    mentions ``↗`` (e.g. one documenting the ↗ contract) is not mistaken for a
    summary node."""
    first = body.split('\n', 1)[0] if body else ''
    if _SUMMARY_AFTER_TITLE.match(first):
        return True
    # For the terminal form, strip inline-code spans first so a backticked `↗`
    # mentioned in prose (e.g. "the summary marker is `↗`") isn't read as the
    # marker. A real trailing marker ("… full detail ↗") is not in backticks.
    return bool(_SUMMARY_TERMINAL.search(re.sub(r'`[^`]*`', '', first)))


def _slugify(title: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')


def _defined_slugs(text: str) -> set[str]:
    """Title slugs from real node-definition lines ONLY (fence-aware). A fenced
    ``[99] **Fake Title**`` must NOT define ``fake-title`` — otherwise it would
    mask a genuinely dangling ``[[fake-title]]`` link as resolved."""
    lines = text.split('\n')
    return {_slugify(m.group(1))
            for idx, _ in _node_start_lines(text)
            if (m := _TITLE.match(lines[idx]))}


def _dangling_slugs(text: str, defined: set[str]) -> set[str]:
    # Strip fenced/inline code first (mirrors `_refs`): a `[[slug]]` inside a code
    # example is illustrative, not a real link. This matters now that
    # `_defined_slugs` is fence-aware — a fenced `[N] **Fake Title**` no longer
    # defines `fake-title`, so a fenced `[[fake-title]]` would otherwise surface
    # as a false-positive dangling link.
    return {s for s in _SLUG.findall(_strip_code(text)) if _slugify(s) not in defined}


def _refs(text: str) -> set[int]:
    """All [N] refs in prose (code stripped, [0] excluded)."""
    return {int(n) for n in _REF.findall(_strip_code(text)) if int(n) != 0}


def _parse_remap(spec: str) -> dict[int, int]:
    remap: dict[int, int] = {}
    for pair in spec.split(','):
        pair = pair.strip()
        if not pair:
            continue
        old, _, new = pair.partition(':')
        remap[int(old)] = int(new)
    return remap


def _git_show(ref: str, path: str) -> str | None:
    try:
        proc = subprocess.run(
            ['git', 'show', f'{ref}:{path}'],
            capture_output=True, text=True, encoding='utf-8',
            errors='replace', check=False,
        )
    except OSError:
        return None
    return proc.stdout if proc.returncode == 0 else None


def check(main_path: Path, overflow_path: Path,
          remap: dict[int, int] | None = None,
          base: str | None = None) -> tuple[list[str], list[str], list[str]]:
    """Return (findings, warnings, notes)."""
    findings: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []

    main_text = main_path.read_text(encoding='utf-8', errors='replace')
    main_ids_list = _node_ids(main_text)
    main_ids = set(main_ids_list)
    main_bodies = _node_bodies(main_text)

    # 1. MIND_MAP contiguity (1..N) + no dups + no ids < 1
    dups = sorted({i for i in main_ids_list if main_ids_list.count(i) > 1})
    if dups:
        findings.append(f"MIND_MAP: duplicate node id(s): {dups}")
    bad_low = sorted(i for i in main_ids if i < 1)
    if bad_low:
        findings.append(f"MIND_MAP: invalid node id(s) < 1 (ids start at 1): {bad_low}")
    valid_ids = {i for i in main_ids if i >= 1}
    if valid_ids:
        missing = sorted(set(range(1, max(valid_ids) + 1)) - valid_ids)
        if missing:
            findings.append(
                f"MIND_MAP: ids not contiguous 1..{max(valid_ids)} — missing {missing}")
    elif not main_ids:
        findings.append("MIND_MAP: no node definitions found")

    # OVERFLOW (optional)
    overflow_text = ''
    overflow_ids: set[int] = set()
    overflow_bodies: dict[int, str] = {}
    if overflow_path.exists():
        overflow_text = overflow_path.read_text(encoding='utf-8', errors='replace')
        ov_ids_list = _node_ids(overflow_text)
        overflow_ids = set(ov_ids_list)
        overflow_bodies = _node_bodies(overflow_text)
        ov_dups = sorted({i for i in ov_ids_list if ov_ids_list.count(i) > 1})
        if ov_dups:
            findings.append(f"OVERFLOW: duplicate node id(s): {ov_dups}")
        # 2. subset (sparse is fine; orphans are not)
        orphans = sorted(overflow_ids - main_ids)
        if orphans:
            findings.append(
                f"OVERFLOW: orphan node(s) absent from MIND_MAP: {orphans}")
    else:
        notes.append(f"{overflow_path.name} not present — overflow checks skipped")

    # 3. ↗-contract: every ↗ MIND_MAP node must have an overflow entry
    arrow_ids = {i for i, body in main_bodies.items() if _is_summary(body)}
    if arrow_ids:
        if not overflow_path.exists():
            findings.append(
                f"↗-contract: MIND_MAP has ↗ node(s) {sorted(arrow_ids)} but no "
                f"{overflow_path.name}")
        else:
            missing_overflow = sorted(arrow_ids - overflow_ids)
            if missing_overflow:
                findings.append(
                    f"↗-contract: ↗ node(s) without an overflow entry: {missing_overflow}")

    # --- base context for the differential checks (W2 mirror, W3 archive) ---
    # Read the merge-base's MIND_MAP + overflow so we flag only what THIS merge
    # changed, not pre-existing repo conventions. A merge RENUMBERS local nodes,
    # so base ids are translated into HEAD's id-space via --remap before
    # comparing (base [28] == HEAD [35] under --remap 28:35). The differential is
    # on mismatch/missing *status* — NOT body equality — because a renumber
    # legitimately changes a node's content (its inner refs get remapped).
    base_ctx = None
    if base:
        base_main_text = _git_show(base, main_path.name)
        if base_main_text is None:
            # Fail closed: --base was requested for the gating checks but the ref
            # is unreadable — don't silently fall through to "looks clean".
            findings.append(
                f"--base {base}: cannot read {main_path.name} at that ref — "
                "differential mirror/archive checks cannot run (failing closed)")
        else:
            base_ov_text = _git_show(base, overflow_path.name) or ''  # absent = newly adopting overflow
            _to_head = (lambda b: remap.get(b, b)) if remap else (lambda b: b)
            base_ctx = {
                'main_bodies': _node_bodies(base_main_text),
                'ov_ids': set(_node_ids(base_ov_text)),
                'ov_bodies': _node_bodies(base_ov_text),
                'to_head': _to_head,
            }

    # 3b. byte-identical mirror: a NON-↗ node present in both files must match.
    # With --base, flag ONLY mirrors that NEWLY diverged this merge (a pre-existing
    # mismatch — same node mismatched in the remap-translated base — is suppressed);
    # without --base it's advisory (warning), never a permanent-red gate.
    if overflow_path.exists():
        head_mismatch = {
            i for i in (main_ids & overflow_ids)
            if not _is_summary(main_bodies.get(i, ''))
            and main_bodies.get(i) != overflow_bodies.get(i)}
        if base_ctx is not None:
            bm, bov_ids, bov = base_ctx['main_bodies'], base_ctx['ov_ids'], base_ctx['ov_bodies']
            base_mismatch = {
                base_ctx['to_head'](b) for b in (set(bm) & bov_ids)
                if not _is_summary(bm.get(b, '')) and bm.get(b) != bov.get(b)}
            new_mismatch = sorted(head_mismatch - base_mismatch)
            if new_mismatch:
                findings.append(
                    f"mirror: node(s) newly diverged from their overflow copy this merge: {new_mismatch}")
        elif head_mismatch:
            warnings.append(
                f"mirror: non-↗ node(s) differ from overflow (advisory — pass --base to gate): "
                f"{sorted(head_mismatch)}")

    # 3c. archive completeness (differential, --base only). Detect the base's
    # convention by whether it mirrors its full nodes into overflow:
    #   COMPLETE archive (ALL non-↗ base nodes mirrored) → HARD-flag new
    #     full nodes this merge added that aren't mirrored;
    #   PARTIAL archive (some but not all) → ambiguous convention → ADVISORY
    #     warning only (never gate — avoids re-creating "permanent-red" on a
    #     mid-transition base);
    #   SPARSE (no full-node mirrors) → convention not in use → silent.
    # Either way only NEWLY-unmirrored nodes are reported (already-unmirrored
    # nodes in the remap-translated base are subtracted).
    if base_ctx is not None:
        bm, bov_ids = base_ctx['main_bodies'], base_ctx['ov_ids']
        base_full = {b for b in bm if not _is_summary(bm.get(b, ''))}
        base_mirrored = {b for b in base_full if b in bov_ids}
        complete_archive = bool(base_full) and base_mirrored == base_full
        partial_archive = bool(base_mirrored) and not complete_archive
        if complete_archive or partial_archive:
            base_missing_full = {base_ctx['to_head'](b) for b in (base_full - base_mirrored)}
            head_missing_full = {
                i for i in main_ids
                if not _is_summary(main_bodies.get(i, '')) and i not in overflow_ids}
            new_unmirrored = sorted(head_missing_full - base_missing_full)
            if new_unmirrored and complete_archive:
                findings.append(
                    "archive: full node(s) added this merge but not mirrored into overflow "
                    f"(base keeps a complete archive): {new_unmirrored}")
            elif new_unmirrored:
                warnings.append(
                    "archive (advisory — base is a PARTIAL archive, convention unclear): "
                    f"possibly-unmirrored new full node(s): {new_unmirrored}")

    # 4. every [N] ref resolves to a defined MIND_MAP node
    all_refs = _refs(main_text) | _refs(overflow_text)
    broken = sorted(all_refs - main_ids)
    if broken:
        findings.append(f"refs: [N] reference(s) resolve to no node: {broken}")

    # 5a. (differential) the renumbered region carries no stale [OLD] refs.
    # Scan EVERY renumbered (NEW) node body for ANY old id in the remap — not
    # just the node's own paired old — so a cross-node sibling stale ref
    # (e.g. --remap 2:4,3:5 with [4] still pointing at [3]) is caught. Exclude a
    # node's own new id so a legitimate self-reference isn't flagged.
    if remap:
        old_ids = set(remap.keys())
        # Guard against a typo'd mapping (e.g. --remap 2:99 where [99] doesn't
        # exist): otherwise the scan finds nothing and exits clean while the real
        # renumbered node still holds stale refs.
        absent_new = sorted(n for n in set(remap.values()) if n not in main_ids)
        if absent_new:
            findings.append(f"--remap: NEW id(s) not defined in MIND_MAP: {absent_new}")
        stale: list[str] = []
        for new in sorted(set(remap.values())):
            for label, bodies in (("MIND_MAP", main_bodies), ("OVERFLOW", overflow_bodies)):
                body = bodies.get(new)
                if not body:
                    continue
                for old in sorted((old_ids & _refs(body)) - {new}):
                    stale.append(f"[{new}] ({label}) still refers to old [{old}]")
        if stale:
            findings.append("stale refs in renumbered region:\n    " + "\n    ".join(stale))
    else:
        notes.append("--remap not given — stale-self-ref check skipped "
                     "(a stale-but-resolving ref will NOT be caught)")

    # 5b. dangling [[slug]] links (NEW-only fail with --base, else warn)
    defined = _defined_slugs(main_text)
    dangling = _dangling_slugs(main_text, defined) | _dangling_slugs(overflow_text, defined)
    if dangling:
        base_dangling: set[str] = set()
        if base:
            base_text = _git_show(base, main_path.name)
            if base_text is None:
                warnings.append(f"--base {base}: could not read {main_path.name} at that ref "
                                "— treating all dangling slugs as warnings")
                base = None
            else:
                base_defined = _defined_slugs(base_text)
                base_dangling = _dangling_slugs(base_text, base_defined)
                # Current dangling slugs are collected from BOTH files, so the
                # inherited set must be too — else a slug that already dangled in
                # base OVERFLOW would be mis-flagged as NEW.
                base_ov = _git_show(base, overflow_path.name)
                if base_ov:
                    base_dangling |= _dangling_slugs(base_ov, base_defined)
        if base:
            new_dangling = sorted(dangling - base_dangling)
            inherited = sorted(dangling & base_dangling)
            if new_dangling:
                findings.append(f"[[slug]] NEW dangling link(s): {new_dangling}")
            if inherited:
                warnings.append(f"[[slug]] inherited dangling link(s) (pre-existing, OK): {inherited}")
        else:
            warnings.append(f"[[slug]] dangling link(s) (pass --base to fail only on NEW ones): {sorted(dangling)}")

    return findings, warnings, notes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog='ref-integrity.py',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Structural verifier for MIND_MAP.md (+ MIND_MAP_OVERFLOW.md). "
                    "Exit 0 = clean, 1 = findings, 2 = usage/IO error.",
        epilog="examples:\n"
               "  ref-integrity.py\n"
               "  ref-integrity.py MIND_MAP.md MIND_MAP_OVERFLOW.md\n"
               "  ref-integrity.py --remap 28:35,29:36,30:37\n"
               "  ref-integrity.py --remap 28:35 --base main\n")
    ap.add_argument('mind_map', nargs='?', default='MIND_MAP.md',
                    help='MIND_MAP path (default: MIND_MAP.md)')
    ap.add_argument('overflow', nargs='?', default='MIND_MAP_OVERFLOW.md',
                    help='overflow path (default: MIND_MAP_OVERFLOW.md)')
    ap.add_argument('--remap', metavar='OLD:NEW,...',
                    help='enable stale-self-ref check on renumbered nodes')
    ap.add_argument('--base', metavar='GIT_REF',
                    help='base ref so only NEW dangling [[slug]] links fail')
    args = ap.parse_args(argv)

    main_path = Path(args.mind_map)
    if not main_path.exists():
        print(f"error: {main_path} not found", file=sys.stderr)
        return 2

    try:
        remap = _parse_remap(args.remap) if args.remap else None
    except ValueError:
        print(f"error: --remap must be OLD:NEW,... integers, got {args.remap!r}", file=sys.stderr)
        return 2

    findings, warnings, notes = check(main_path, Path(args.overflow), remap, args.base)

    for n in notes:
        print(f"note: {n}")
    for w in warnings:
        print(f"warn: {w}")
    for f in findings:
        print(f"FAIL: {f}")

    if findings:
        print(f"\nref-integrity: {len(findings)} finding(s) — NOT clean")
        return 1
    print("\nref-integrity: clean"
          + (f" ({len(warnings)} warning(s))" if warnings else ""))
    return 0


if __name__ == '__main__':
    sys.exit(main())
