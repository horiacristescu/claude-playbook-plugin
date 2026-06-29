#!/usr/bin/env python3
"""Point tests for `sort_overflow_by_id` (mindmap-sync's OVERFLOW numeric sorter).

Pure stdlib unittest (no hypothesis — honors the T135 stdlib-only invariant).
Covers the run-2 manual-reorder fix and every correctness concern the plan + impl
panels raised: separator collision, byte-parity, fence-awareness, CRLF, duplicate
ids, whole-content coverage, fail-closed.

Run: python3 tests/test_mindmap_sort.py   (from claude-playbook-plugin/)
"""
import sys
import unittest
from pathlib import Path

# Import the helper from the tasks package (cli.py guards its dispatch under __main__).
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "plugins/playbook"))
from tasks.cli import sort_overflow_by_id, _scan_overflow_ids  # noqa: E402


class TestSortOverflow(unittest.TestCase):
    def _ids(self, content):
        return _scan_overflow_ids(content)[0]

    # q1 — basic reorder
    def test_basic_reorder(self):
        out, changed, reason = sort_overflow_by_id("[3] three\n\n[1] one\n\n[2] two\n")
        self.assertTrue(changed)
        self.assertEqual(self._ids(out), [1, 2, 3])
        self.assertEqual(out, "[1] one\n\n[2] two\n\n[3] three\n")

    # q3 — idempotence: a second run on the result is a byte no-op
    def test_idempotent(self):
        out1, _, _ = sort_overflow_by_id("[3] c\n\n[1] a\n\n[2] b\n")
        out2, changed2, reason2 = sort_overflow_by_id(out1)
        self.assertFalse(changed2)
        self.assertEqual(out2, out1)
        self.assertEqual(reason2, "already sorted")

    # q3 — already-sorted (irregular spacing) → byte-identical no-op, never rewritten
    def test_already_sorted_noop_preserves_bytes(self):
        c = "[1] a\n\n\n[2] b\n\n[3] c\n"   # triple blank between 1 and 2
        out, changed, reason = sort_overflow_by_id(c)
        self.assertFalse(changed)
        self.assertEqual(out, c)            # irregular spacing untouched
        self.assertEqual(reason, "already sorted")

    # opus-1 — separator collision: last span without trailing newline must not
    # collide with the node it lands before.
    def test_no_separator_collision(self):
        out, changed, _ = sort_overflow_by_id("[2] two\n\n[1] one")  # no EOF newline
        self.assertTrue(changed)
        self.assertEqual(self._ids(out), [1, 2])
        # [1] and [2] must be on separate lines, not concatenated.
        self.assertNotIn("one[2]", out)
        self.assertIn("[1] one\n\n[2] two", out)

    # q4 — preamble + trailing `## Legacy` preserved and positioned
    def test_preamble_and_tail(self):
        c = "PREAMBLE LINE\n\n[2] two\n\n[1] one\n\n## Legacy\nold stuff\n"
        out, changed, _ = sort_overflow_by_id(c)
        self.assertTrue(changed)
        self.assertTrue(out.startswith("PREAMBLE LINE"))
        self.assertEqual(self._ids(out), [1, 2])
        self.assertIn("## Legacy\nold stuff", out)
        # Legacy stays after the last node, not moved with [2].
        self.assertLess(out.index("[2] two"), out.index("## Legacy"))

    # q5 — a `[9]`-looking line INSIDE a fence is not a node start; node moves whole
    def test_fenced_bracket_not_a_node(self):
        c = "[2] two\n```\n[9] fenced not-a-node\n```\n\n[1] one\n"
        out, changed, _ = sort_overflow_by_id(c)
        self.assertTrue(changed)
        self.assertEqual(self._ids(out), [1, 2])   # NOT [1, 2, 9]
        self.assertIn("```\n[9] fenced not-a-node\n```", out)

    # CRLF (opus-3 / codex-3) — line endings preserved on reorder
    def test_crlf_preserved(self):
        out, changed, _ = sort_overflow_by_id("[2] two\r\n\r\n[1] one\r\n")
        self.assertTrue(changed)
        self.assertEqual(out, "[1] one\r\n\r\n[2] two\r\n")
        self.assertNotIn("\n\n", out.replace("\r\n", ""))  # no bare LF separators

    # duplicate ids preserved as separate, stable blocks
    def test_duplicate_ids_stable(self):
        c = "[2] second\n\n[1] alpha\n\n[1] beta\n"
        out, changed, _ = sort_overflow_by_id(c)
        self.assertTrue(changed)
        self.assertEqual(self._ids(out), [1, 1, 2])
        # alpha precedes beta (stable) in the output
        self.assertLess(out.index("alpha"), out.index("beta"))

    # node bodies preserved byte-for-byte (only separators canonicalized)
    def test_bodies_byte_preserved(self):
        c = "[2] two\nline2 of two\n\n[1] one\nline2 of one\n"
        out, changed, _ = sort_overflow_by_id(c)
        self.assertTrue(changed)
        self.assertIn("[1] one\nline2 of one", out)
        self.assertIn("[2] two\nline2 of two", out)

    # fail-closed: unmatched code fence → unchanged (left byte-identical)
    def test_unmatched_fence_fails_closed(self):
        c = "[2] two\n```\nopen fence never closed\n\n[1] one\n"
        out, changed, reason = sort_overflow_by_id(c)
        self.assertFalse(changed)
        self.assertEqual(out, c)
        self.assertTrue(reason)   # some explanation, content untouched

    # fewer than 2 nodes → no-op
    def test_single_node_noop(self):
        c = "[1] only one\n"
        out, changed, reason = sort_overflow_by_id(c)
        self.assertFalse(changed)
        self.assertEqual(out, c)

    # opus-5 — cross-check: a normally-shaped file's sorted output, scanned, is ascending
    def test_cross_parser_ascending(self):
        c = "[5] e\n\n[3] c\n\n[10] j\n\n[1] a\n"
        out, changed, _ = sort_overflow_by_id(c)
        self.assertTrue(changed)
        ids = self._ids(out)
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(ids, [1, 3, 5, 10])   # numeric, not lexical

    # --- impl-panel findings (IF1/IF3) ---

    # IF1 — a `##` heading glued directly to the last node's prose (NO blank line
    # before it) stays part of that node and is NOT amputated to the file tail.
    def test_glued_heading_stays_in_node(self):
        c = "[2] two\n\n[1] one\n## Notes\nmore\n"
        out, changed, _ = sort_overflow_by_id(c)
        self.assertTrue(changed)
        # ## Notes travels WITH node [1] (now first), before node [2].
        self.assertLess(out.index("## Notes"), out.index("[2] two"))
        self.assertIn("[1] one\n## Notes\nmore", out)

    # IF1 — a blank-line-preceded heading inside a NON-last node is ambiguous → fail closed.
    def test_section_heading_in_nonlast_node_fails_closed(self):
        c = "[2] two\n\n## Subsection\nbody\n\n[1] one\n"
        out, changed, reason = sort_overflow_by_id(c)
        self.assertFalse(changed)
        self.assertEqual(out, c)

    # IF3 — a whitespace-only preamble (leading blank lines) is preserved exactly.
    def test_whitespace_preamble_preserved(self):
        c = "\n\n[2] b\n\n[1] a\n"
        out, changed, _ = sort_overflow_by_id(c)
        self.assertTrue(changed)
        self.assertTrue(out.startswith("\n\n"))
        self.assertEqual(self._ids(out), [1, 2])


class TestMindmapSyncCLI(unittest.TestCase):
    """IF6 — exercise the real `mindmap-sync --fix` write path end-to-end."""

    def _run_fix(self, tmp, main, overflow):
        import os
        import subprocess
        (tmp / ".agent" / "tasks").mkdir(parents=True)
        (tmp / "MIND_MAP.md").write_text(main, encoding="utf-8")
        # write overflow byte-exact (preserve any CRLF) via binary
        (tmp / "MIND_MAP_OVERFLOW.md").write_bytes(overflow.encode("utf-8"))
        env = dict(os.environ)
        env["PYTHONPATH"] = str(_HERE.parent / "plugins/playbook")
        subprocess.run(
            [sys.executable, "-m", "tasks.cli", "mindmap-sync", "--fix"],
            cwd=str(tmp), env=env, check=True,
            capture_output=True, text=True,
        )
        return (tmp / "MIND_MAP_OVERFLOW.md").read_bytes()

    def test_cli_sort_only(self):
        import tempfile
        main = "[1] one\n\n[2] two\n\n[3] three\n"
        ov = "[3] three\n\n[1] one\n\n[2] two\n"
        with tempfile.TemporaryDirectory() as d:
            out = self._run_fix(Path(d), main, ov).decode("utf-8")
        self.assertEqual(_scan_overflow_ids(out)[0], [1, 2, 3])

    def test_cli_append_then_sort(self):
        import tempfile
        main = "[1] one\n\n[2] two\n\n[3] three\n"
        ov = "[2] two\n\n[1] one\n"        # missing [3], out of order
        with tempfile.TemporaryDirectory() as d:
            out = self._run_fix(Path(d), main, ov).decode("utf-8")
        self.assertEqual(_scan_overflow_ids(out)[0], [1, 2, 3])

    def test_cli_already_sorted_byte_noop(self):
        import tempfile
        main = "[1] one\n\n[2] two\n\n[3] three\n"
        ov = "[1] one\n\n[2] two\n\n[3] three\n"
        with tempfile.TemporaryDirectory() as d:
            before = ov.encode("utf-8")
            out = self._run_fix(Path(d), main, ov)
        self.assertEqual(out, before)   # byte-identical, never rewritten

    def test_cli_crlf_sort_only_preserved(self):
        import tempfile
        main = "[1] one\n\n[2] two\n"
        ov = "[2] two\r\n\r\n[1] one\r\n"   # CRLF, out of order, no drift
        with tempfile.TemporaryDirectory() as d:
            out = self._run_fix(Path(d), main, ov)
        self.assertEqual(_scan_overflow_ids(out.decode("utf-8"))[0], [1, 2])
        self.assertIn(b"\r\n", out)          # CRLF preserved
        self.assertNotIn(b"[1] one\n\n[2]", out)  # not normalized to LF


if __name__ == "__main__":
    unittest.main(verbosity=2)
