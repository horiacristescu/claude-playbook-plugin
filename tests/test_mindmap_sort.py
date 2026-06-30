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
from tasks.cli import (  # noqa: E402
    sort_overflow_by_id, _scan_overflow_ids, _node_starts, _parse_nodes,
    _unnumbered_tail, _unnumbered_tail_notice,
)


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

    def _run_fix_rc(self, tmp, main, overflow):
        """Run `mindmap-sync --fix`; return (returncode, overflow_bytes). Does NOT
        assert success — lets fail-closed (non-zero exit) cases be tested."""
        import os
        import subprocess
        (tmp / ".agent" / "tasks").mkdir(parents=True, exist_ok=True)
        (tmp / "MIND_MAP.md").write_text(main, encoding="utf-8")
        # write overflow byte-exact (preserve any CRLF) via binary
        (tmp / "MIND_MAP_OVERFLOW.md").write_bytes(overflow.encode("utf-8"))
        env = dict(os.environ)
        env["PYTHONPATH"] = str(_HERE.parent / "plugins/playbook")
        r = subprocess.run(
            [sys.executable, "-m", "tasks.cli", "mindmap-sync", "--fix"],
            cwd=str(tmp), env=env, capture_output=True, text=True,
        )
        return r.returncode, (tmp / "MIND_MAP_OVERFLOW.md").read_bytes()

    def _run_fix(self, tmp, main, overflow):
        rc, out = self._run_fix_rc(tmp, main, overflow)
        self.assertEqual(rc, 0, f"--fix exited {rc} unexpectedly")
        return out

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

    # --- task 007: P1 raw-span drift/append (byte/CRLF/fail-closed) ---

    def test_cli_drift_substring_node_untouched(self):
        # Node [1]'s body ("abc") is a SUBSTRING of node [2]'s body ("abcdef").
        # A stripped-body global replace() would corrupt [2]; the raw-span edit
        # must touch only [1].
        import tempfile
        main = "[1] abc-SYNCED\n\n[2] abcdef\n"
        ov = "[1] abc\n\n[2] abcdef\n"
        with tempfile.TemporaryDirectory() as d:
            out = self._run_fix(Path(d), main, ov).decode("utf-8")
        self.assertIn("[2] abcdef", out)        # node 2 intact
        self.assertIn("abc-SYNCED", out)        # node 1 synced
        self.assertEqual(_scan_overflow_ids(out)[0], [1, 2])

    def test_cli_crlf_drift_preserved(self):
        # Drift of an EXISTING node on a CRLF file must keep \r\n (the direct
        # data-loss scenario; read_text would have normalized to LF).
        import tempfile
        main = "[1] one-SYNCED\n\n[2] two\n"
        ov = "[1] one\r\n\r\n[2] two\r\n"
        with tempfile.TemporaryDirectory() as d:
            out = self._run_fix(Path(d), main, ov)
        self.assertIn(b"\r\n", out)
        self.assertNotIn(b"\r\r\n", out)        # no doubled CR
        self.assertIn(b"one-SYNCED", out)

    def test_cli_crlf_append_preserved(self):
        # Appending a main_only node to a CRLF overflow keeps CRLF on the new node.
        import tempfile
        main = "[1] one\n\n[2] two\n\n[3] three\n"
        ov = "[1] one\r\n\r\n[2] two\r\n"      # missing [3]
        with tempfile.TemporaryDirectory() as d:
            out = self._run_fix(Path(d), main, ov)
        self.assertIn(b"\r\n", out)
        self.assertNotIn(b"\r\r\n", out)
        self.assertIn(b"[3] three", out)
        self.assertEqual(_scan_overflow_ids(out.decode("utf-8"))[0], [1, 2, 3])

    def test_cli_append_no_final_newline(self):
        # Overflow with NO trailing newline + a main_only node → append must not
        # glue the new node onto the last one.
        import tempfile
        main = "[1] one\n\n[2] two\n"
        ov = "[1] one"                          # no final newline, missing [2]
        with tempfile.TemporaryDirectory() as d:
            out = self._run_fix(Path(d), main, ov).decode("utf-8")
        self.assertEqual(_scan_overflow_ids(out)[0], [1, 2])
        self.assertNotIn("one[2]", out)         # not concatenated
        self.assertIn("[1] one\n\n[2] two", out)

    def test_cli_drift_idempotent(self):
        # Running --fix twice: the second run is a byte no-op.
        import tempfile
        main = "[1] one-SYNCED\n\n[2] two\n"
        ov = "[1] one\n\n[2] two\n"
        with tempfile.TemporaryDirectory() as d:
            first = self._run_fix(Path(d), main, ov)
            # feed the first result back in as the overflow
            second = self._run_fix(Path(d), main, first.decode("utf-8"))
        self.assertEqual(first, second)         # idempotent

    def test_cli_fail_closed_unmatched_fence(self):
        # An unmatched code fence makes _partition_overflow return None. With drift
        # present, --fix must FAIL CLOSED: non-zero exit, file byte-unchanged.
        import tempfile
        main = "[1] one-SYNCED\n\n[2] two\n"
        ov = "[1] one\n```\nunclosed fence never terminated\n\n[2] two\n"
        with tempfile.TemporaryDirectory() as d:
            rc, out = self._run_fix_rc(Path(d), main, ov)
        self.assertNotEqual(rc, 0)              # failed closed
        self.assertEqual(out, ov.encode("utf-8"))  # file untouched

    def test_cli_fail_closed_unmatched_fence_in_main(self):
        # W7: an unmatched fence in MIND_MAP.md mis-parses the sync source, so
        # --fix must fail closed (non-zero, overflow untouched) even though the
        # overflow itself is well-formed.
        import tempfile
        main = "[1] one-SYNCED\n```\nunclosed fence in MAIN\n\n[2] two\n"
        ov = "[1] one\n\n[2] two\n"
        with tempfile.TemporaryDirectory() as d:
            rc, out = self._run_fix_rc(Path(d), main, ov)
        self.assertNotEqual(rc, 0)
        self.assertEqual(out, ov.encode("utf-8"))

    def test_cli_sort_only_blocked_by_main_fence(self):
        # W11a: an unmatched fence in MIND_MAP.md must block even a SORT-ONLY --fix
        # (no drift / no main_only), because the early guard fires before any write.
        import tempfile
        main = "[1] one\n```\nunclosed fence in MAIN\n\n[2] two\n"
        ov = "[2] two\n\n[1] one\n"        # out of order → would otherwise sort
        with tempfile.TemporaryDirectory() as d:
            rc, out = self._run_fix_rc(Path(d), main, ov)
        self.assertNotEqual(rc, 0)
        self.assertEqual(out, ov.encode("utf-8"))   # not sorted, not written

    def test_cli_mixed_newline_drift_no_spurious_abort(self):
        # W11b: a mostly-LF overflow with one stray CRLF elsewhere must still sync
        # an LF drifted span (per-span newline detection), not fail closed.
        import tempfile
        main = "[1] one-SYNCED\n\n[2] two\n"
        ov = "[1] one\n\n[2] two\r\n"      # node [1] is LF; a stray CRLF on [2]
        with tempfile.TemporaryDirectory() as d:
            rc, out = self._run_fix_rc(Path(d), main, ov)
        self.assertEqual(rc, 0)                       # no spurious fail-closed
        self.assertIn(b"one-SYNCED", out)

    def test_cli_untouched_node_separators_preserved(self):
        # W6: syncing one node must NOT canonicalize the blank-line separators of
        # other, untouched nodes (a triple-blank stays a triple-blank).
        import tempfile
        main = "[1] one-SYNCED\n\n[2] two\n\n[3] three\n"
        ov = "[1] one\n\n[2] two\n\n\n[3] three\n"   # triple blank between 2 and 3
        with tempfile.TemporaryDirectory() as d:
            out = self._run_fix(Path(d), main, ov)
        self.assertIn(b"two\n\n\n[3]", out)          # triple blank preserved
        self.assertIn(b"one-SYNCED", out)

    def test_cli_drifted_node_post_heading_tail_preserved(self):
        # W6: a drifted node whose overflow body has a glued `## heading` + content
        # (which _extract_nodes truncates) must keep that tail — only the body
        # before the heading is synced.
        import tempfile
        main = "[1] alpha-SYNCED\n## sub\nkept tail\n\n[2] two\n"
        ov = "[1] alpha\n## sub\nkept tail\n\n[2] two\n"
        with tempfile.TemporaryDirectory() as d:
            out = self._run_fix(Path(d), main, ov).decode("utf-8")
        self.assertIn("kept tail", out)
        self.assertIn("alpha-SYNCED", out)

    def test_node_start_id_agreement_across_parsers(self):
        # Panel I3: the cli `_node_starts` and ref-integrity `_node_ids` must agree
        # on node-START id sets (NOT body extent) — including ignoring a fenced [N].
        import importlib.util
        ri_path = _HERE.parent / "plugins/playbook/skills/merge/ref-integrity.py"
        spec = importlib.util.spec_from_file_location("ref_integrity_cmp", ri_path)
        ri = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ri)
        fixture = ("[1] **a** — body\n"
                   "```\n[99] fenced not a node\n```\n"
                   "[2] **b** — body\n[3] **c** — body\n")
        cli_starts, _ = _node_starts(fixture.splitlines(keepends=True))
        cli_ids = [nid for _, nid in cli_starts]
        self.assertEqual(cli_ids, ri._node_ids(fixture))
        self.assertEqual(cli_ids, [1, 2, 3])    # fenced [99] excluded by both


class TestParseNodes(unittest.TestCase):
    """task 007 W9 — the hoisted merge-collision parser `_parse_nodes`."""

    def test_fenced_bracket_stays_in_body(self):
        # A fenced `[99] ` line must NOT start a ghost node — it stays inside the
        # enclosing node's raw body (which feeds the md5 collision check).
        text = ("[1] **a** body\n"
                "```\n[99] fenced not a node\n```\n"
                "tail of one\n[2] **b** body\n")
        nodes = _parse_nodes(text)
        self.assertEqual(sorted(nodes), [1, 2])          # no ghost [99]
        self.assertIn("[99] fenced not a node", nodes[1])  # fenced line kept in body
        self.assertIn("tail of one", nodes[1])

    def test_raw_body_accumulation_no_heading_trim(self):
        # Unlike _extract_nodes, _parse_nodes does NOT truncate at a heading.
        text = "[1] **a** body\n## sub\nmore\n[2] **b** body\n"
        nodes = _parse_nodes(text)
        self.assertIn("## sub", nodes[1])
        self.assertIn("more", nodes[1])

    def test_trailing_space_required(self):
        # The regex requires `[N] ` (trailing space) — a bare `[5]` line is not a
        # node definition here (intended, pre-existing behavior).
        self.assertEqual(_parse_nodes("[5]no-space\n"), {})
        self.assertEqual(sorted(_parse_nodes("[5] real node\n")), [5])


class TestUnnumberedTailNotice(unittest.TestCase):
    """task 008 — surface a stale heading-led unnumbered tail (round-3 regression).
    NOTICE only; never auto-delete. Detector mirrors _extract_nodes' heading-trim."""

    LEG = "## Legacy notes\n**X** — old.\n**Y** — old.\n"

    # --- pure detector (_unnumbered_tail) ---
    def test_detect_blank_preceded_heading(self):
        c = "[1] a\n\n[2] b body\n\n" + self.LEG
        self.assertTrue(_unnumbered_tail(c).startswith("## Legacy"))

    def test_detect_glued_heading(self):
        # heading glued to last node prose (NO blank line) — _partition_overflow.tail
        # would MISS this; the detector must catch it (panel Critical).
        c = "[1] a\n\n[2] b body\n## Legacy notes\nold\n"
        self.assertTrue(_unnumbered_tail(c).startswith("## Legacy"))

    def test_headingless_prose_not_detected(self):
        # documented limitation: trailing prose with no heading is indistinguishable
        # from the last node's body.
        c = "[1] a\n\n[2] b body\nmore prose, no heading\n"
        self.assertEqual(_unnumbered_tail(c), "")

    def test_no_nodes_not_detected(self):
        self.assertEqual(_unnumbered_tail("just preamble\nno nodes\n"), "")

    def test_fenced_hash_not_detected(self):
        # a `##` inside a code fence in the last node is not a section heading
        c = "[1] a\n\n[2] b\n```\n## not a heading\n```\nend\n"
        self.assertEqual(_unnumbered_tail(c), "")

    # --- notice gating (_unnumbered_tail_notice) ---
    def test_notice_fires_without_date(self):
        c = "[1] a\n\n[2] b body\n\n" + self.LEG
        self.assertIn("unnumbered line", _unnumbered_tail_notice(c))

    def test_notice_silent_with_dated_keepnote(self):
        c = "[1] a\n\n[2] b body\n\n## Legacy (kept 2026-06-30)\nold\n"
        self.assertEqual(_unnumbered_tail_notice(c), "")

    def test_incidental_body_date_does_not_suppress(self):
        # W7 regression: the REAL round-3 block has "Created 2026-05-14" in its body
        # and "kept for history" on the heading — neither line has BOTH keep/kept AND
        # a date, so the notice MUST still fire (an incidental date is not a keep-note).
        c = ("[40] x\n\n[41] y\n\n"
             "## Legacy / unnumbered notes (superseded, kept for history)\n\n"
             "**Project Initialize** — Created 2026-05-14: CLAUDE.md, MIND_MAP.md.\n")
        self.assertIn("unnumbered line", _unnumbered_tail_notice(c))

    def test_keepnote_own_line_suppresses(self):
        # a deliberate keep-note as its own line under the heading also silences it.
        c = ("[1] a\n\n[2] b body\n\n## Legacy notes\n"
             "kept 2026-06-30 (intentionally retained per gotcha #7)\nold\n")
        self.assertEqual(_unnumbered_tail_notice(c), "")

    def test_substring_keepword_plus_date_does_not_suppress(self):
        # impl-panel Critical: "keep" as a SUBSTRING of another word (bookkeeping,
        # timekeeping, housekeeping, beekeeper) + a date on the same line must NOT
        # false-suppress — only a whole-word keep/kept counts as an acknowledgement.
        for stale in ("**Timekeeping log** — Created 2026-01-15: setup.",
                      "**bookkeeping entry** 2024-03-03 reconciled.",
                      "housekeeping notes 2025-01-01."):
            c = f"[1] a\n\n[2] b body\n\n## Legacy notes\n{stale}\n"
            self.assertIn("unnumbered line", _unnumbered_tail_notice(c),
                          f"substring keep-word wrongly suppressed: {stale!r}")

    def test_cli_crlf_tail_notice_and_preserves(self):
        # impl-panel probe 6: CRLF overflow with a tail-only, --fix mode → notice
        # fires, line endings + the Legacy block preserved byte-identical.
        import tempfile
        main = "[1] **a** body\n\n[2] **b** body\n"
        ov = ("[1] **a** body\n\n[2] **b** body\n\n" + self.LEG)
        ov_crlf = ov.replace("\n", "\r\n")
        with tempfile.TemporaryDirectory() as d:
            rc, out, after = self._run(Path(d), main, ov_crlf, fix=True)
        self.assertEqual(rc, 0)
        self.assertIn("unnumbered line", out)
        self.assertEqual(after, ov_crlf.encode("utf-8"))     # byte-identical, CRLF kept
        self.assertNotIn(b"\r\r\n", after)

    def test_notice_silent_without_tail(self):
        self.assertEqual(_unnumbered_tail_notice("[1] a\n\n[2] b body\n"), "")

    # --- end-to-end via the CLI, both modes ---
    def _run(self, tmp, main, overflow, fix):
        import os, subprocess
        (tmp / ".agent" / "tasks").mkdir(parents=True, exist_ok=True)
        (tmp / "MIND_MAP.md").write_text(main, encoding="utf-8")
        (tmp / "MIND_MAP_OVERFLOW.md").write_bytes(overflow.encode("utf-8"))
        env = dict(os.environ)
        env["PYTHONPATH"] = str(_HERE.parent / "plugins/playbook")
        args = [sys.executable, "-m", "tasks.cli", "mindmap-sync"] + (["--fix"] if fix else [])
        r = subprocess.run(args, cwd=str(tmp), env=env, capture_output=True, text=True)
        return r.returncode, r.stdout, (tmp / "MIND_MAP_OVERFLOW.md").read_bytes()

    def test_cli_readonly_notice_with_tail(self):
        import tempfile
        main = "[1] **a** body\n\n[2] **b** body\n"
        ov = "[1] **a** body\n\n[2] **b** body\n\n" + self.LEG
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = self._run(Path(d), main, ov, fix=False)
        self.assertEqual(rc, 0)
        self.assertIn("unnumbered line", out)

    def test_cli_readonly_silent_without_tail(self):
        import tempfile
        main = "[1] **a** body\n\n[2] **b** body\n"
        ov = "[1] **a** body\n\n[2] **b** body\n"
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = self._run(Path(d), main, ov, fix=False)
        self.assertEqual(rc, 0)
        self.assertNotIn("unnumbered line", out)

    def test_cli_fix_cleanfile_tailonly_notices_and_preserves(self):
        # --fix on a fully-synced, sorted overflow whose ONLY extra is a tail:
        # notice fires AND the file (incl. the Legacy block) is byte-identical.
        import tempfile
        main = "[1] **a** body\n\n[2] **b** body\n"
        ov = "[1] **a** body\n\n[2] **b** body\n\n" + self.LEG
        with tempfile.TemporaryDirectory() as d:
            rc, out, after = self._run(Path(d), main, ov, fix=True)
        self.assertEqual(rc, 0)
        self.assertIn("unnumbered line", out)
        self.assertEqual(after, ov.encode("utf-8"))   # byte-identical: never deleted

    def test_cli_fix_silent_with_dated_keepnote(self):
        import tempfile
        main = "[1] **a** body\n\n[2] **b** body\n"
        ov = "[1] **a** body\n\n[2] **b** body\n\n## Legacy (kept 2026-06-30)\nold\n"
        with tempfile.TemporaryDirectory() as d:
            rc, out, after = self._run(Path(d), main, ov, fix=True)
        self.assertEqual(rc, 0)
        self.assertNotIn("unnumbered line", out)
        self.assertEqual(after, ov.encode("utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
