#!/usr/bin/env python3
"""Point tests for the `merge` skill's bundled ref-integrity.py verifier.

Pure stdlib unittest (no hypothesis — honors the T135 stdlib-only invariant).
One test per failure class plus the differential (--remap / --base) checks.

Run: python3 tests/test_ref_integrity.py    (or: python3 -m unittest ...)
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path

# Load ref-integrity.py (hyphenated filename → import by path).
_HERE = Path(__file__).resolve().parent
_CANDIDATES = [
    _HERE.parent / "plugins/playbook/skills/merge/ref-integrity.py",            # post-rename (E1)
    _HERE.parent / "plugins/playbook/skills/merge-with-mindmap/ref-integrity.py",  # pre-rename
]
_RI_PATH = next((p for p in _CANDIDATES if p.exists()), None)
assert _RI_PATH, f"ref-integrity.py not found in {[str(c) for c in _CANDIDATES]}"
_spec = importlib.util.spec_from_file_location("ref_integrity", _RI_PATH)
ri = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ri)


class RefIntegrityTest(unittest.TestCase):
    def _check(self, main_text, overflow_text=None, remap=None, base=None):
        """Write temp files, run check(), return (findings, warnings, notes)."""
        d = Path(self._tmp.name)
        main = d / "MIND_MAP.md"
        main.write_text(main_text, encoding="utf-8")
        overflow = d / "MIND_MAP_OVERFLOW.md"
        if overflow_text is not None:
            overflow.write_text(overflow_text, encoding="utf-8")
        rmap = ri._parse_remap(remap) if remap else None
        return ri.check(main, overflow, rmap, base)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _with_base(self, base_main, base_overflow=""):
        """Monkeypatch _git_show to return per-path base content for --base."""
        orig = ri._git_show
        ri._git_show = lambda ref, path: base_overflow if "OVERFLOW" in path else base_main
        self.addCleanup(lambda: setattr(ri, "_git_show", orig))

    # --- non-differential checks ------------------------------------------
    def test_clean_pair(self):
        main = ("# MIND_MAP\n\n"
                "[1] **root** — see [2] and [3]\n"
                "[2] **mid** — links [1]\n"
                "[3] **deep** ↗ — full text in overflow\n")
        overflow = "[3] **deep** — full body\ncontinues line two, links [1]\n"
        findings, _, _ = self._check(main, overflow)
        self.assertEqual(findings, [], f"expected clean, got {findings}")

    def test_noncontiguous_ids(self):
        main = "[1] **a** — x\n[3] **c** — y\n"  # missing [2]
        findings, _, _ = self._check(main)
        self.assertTrue(any("contiguous" in f for f in findings), findings)

    def test_duplicate_id(self):
        main = "[1] **a** — x\n[1] **dup** — y\n[2] **b** — z\n"
        findings, _, _ = self._check(main)
        self.assertTrue(any("duplicate" in f for f in findings), findings)

    def test_overflow_orphan(self):
        main = "[1] **a** — x\n[2] **b** ↗ — y\n"
        overflow = "[2] **b** — body\n[5] **ghost** — not in main\n"
        findings, _, _ = self._check(main, overflow)
        self.assertTrue(any("orphan" in f and "5" in f for f in findings), findings)

    def test_sparse_overflow_is_not_a_failure(self):
        # MIND_MAP 1..4; [2] and [4] are ↗ summaries; overflow holds ONLY those
        # two (sparse, non-contiguous) — must be clean.
        main = ("[1] **a** — x\n[2] **b** ↗ — s\n[3] **c** — z\n[4] **d** ↗ — s\n")
        overflow = "[2] **b** — body two\n[4] **d** — body four\n"
        findings, _, _ = self._check(main, overflow)
        self.assertEqual(findings, [], f"sparse overflow should be clean, got {findings}")

    def test_arrow_without_overflow_entry(self):
        main = "[1] **a** — x\n[2] **b** ↗ — needs overflow\n"
        overflow = "[1] **a** — body\n"  # [2] ↗ has no overflow entry
        findings, _, _ = self._check(main, overflow)
        self.assertTrue(any("↗-contract" in f for f in findings), findings)

    def test_dangling_ref(self):
        main = "[1] **a** — refers to [9]\n[2] **b** — y\n"
        findings, _, _ = self._check(main, "")
        self.assertTrue(any("resolve to no node" in f and "9" in f for f in findings), findings)

    def test_code_fence_and_zero_excluded(self):
        # [99] inside a fence, arr[0] inline, ${BASH_SOURCE[0]} — none are refs.
        main = ("[1] **a** — normal, see [2]\n"
                "[2] **b** — has code\n"
                "```\nresult = data[99]\n```\n"
                "inline `arr[0]` and ${BASH_SOURCE[0]} here\n")
        findings, _, _ = self._check(main, "")
        self.assertEqual(findings, [], f"code/[0] must not register as refs, got {findings}")

    def test_mirror_divergence_advisory_without_base(self):
        # Without --base, a non-↗ mirror mismatch is ADVISORY (warning), never a
        # gating finding — so it can't be a permanent-red gate (task-004 fix).
        main = "[1] **a** — canonical body, see [2]\n[2] **b** ↗ — summary\n"
        overflow = "[1] **a** — DIVERGED body\n[2] **b** — full text of b\n"
        findings, warnings, _ = self._check(main, overflow)
        self.assertEqual([f for f in findings if "mirror" in f], [], findings)
        self.assertTrue(any("mirror" in w for w in warnings), warnings)

    def test_non_arrow_mirror_match_is_clean(self):
        main = "[1] **a** — identical body, see [2]\n[2] **b** ↗ — summary\n"
        overflow = "[1] **a** — identical body, see [2]\n[2] **b** — full text\n"
        findings, _, _ = self._check(main, overflow)
        self.assertEqual(findings, [], findings)

    # --- task 004: differential mirror / archive (vs --base) --------------
    def test_mirror_new_divergence_flagged_with_base(self):
        # Base: [1] matched its overflow. HEAD: [1] diverged → NEW → hard finding.
        self._with_base("[1] **a** — body v1\n", "[1] **a** — body v1\n")
        findings, _, _ = self._check("[1] **a** — body v2 DIVERGED\n",
                                     "[1] **a** — body v1\n", base="B")
        self.assertTrue(any("newly diverged" in f and "1" in f for f in findings), findings)

    def test_mirror_preexisting_divergence_suppressed_with_base(self):
        # Base [1] ALREADY mismatched; HEAD [1] still mismatched → suppressed.
        self._with_base("[1] **a** — body A\n", "[1] **a** — body B already diverged\n")
        findings, _, _ = self._check("[1] **a** — body A\n",
                                     "[1] **a** — body C still diverged\n", base="B")
        self.assertEqual([f for f in findings if "mirror" in f], [], findings)

    def test_archive_new_unmirrored_full_node_flagged_with_base(self):
        # Complete-archive base ([1] full mirrored). HEAD adds full [2] not in
        # overflow → flagged.
        self._with_base("[1] **a** — body\n", "[1] **a** — body\n")
        findings, _, _ = self._check("[1] **a** — body\n[2] **b** — new full node\n",
                                     "[1] **a** — body\n", base="B")
        self.assertTrue(any("archive" in f and "2" in f for f in findings), findings)

    def test_archive_sparse_base_silent(self):
        # Base overflow holds only a ↗-summary's full text (no full-node mirror)
        # → sparse convention → adding an unmirrored full node is NOT flagged.
        self._with_base("[1] **a** ↗ — summary\n", "[1] **a** — full text of summary\n")
        findings, _, _ = self._check("[1] **a** ↗ — summary\n[2] **b** — new full node\n",
                                     "[1] **a** — full text of summary\n", base="B")
        self.assertEqual([f for f in findings if "archive" in f], [], findings)

    def test_archive_renumbered_mirrored_node_not_flagged(self):
        # pF4: base [2] full node mirrored. HEAD renumbers it to [3] (remap 2:3)
        # and DOES mirror [3]; a NEW trunk full node [2] is NOT mirrored. Assert
        # the renamed-but-mirrored [3] is NOT flagged and the new [2] IS.
        self._with_base("[1] **root** — base\n[2] **x** — body\n",
                        "[1] **root** — base\n[2] **x** — body\n")
        main = "[1] **root** — base\n[2] **trunk** — new trunk node\n[3] **x** — body\n"
        overflow = "[1] **root** — base\n[3] **x** — body\n"
        findings, _, _ = self._check(main, overflow, base="B", remap="2:3")
        arch = [f for f in findings if "archive" in f]
        self.assertTrue(arch, "expected an archive finding for the new unmirrored node")
        self.assertTrue(any("2" in f for f in arch), arch)
        self.assertFalse(any("[3]" in f or "3]" in f for f in arch),
                         f"renamed-but-mirrored [3] must NOT be flagged: {arch}")

    def test_base_unreadable_fails_closed(self):
        orig = ri._git_show
        ri._git_show = lambda ref, path: None      # base ref unreadable
        self.addCleanup(lambda: setattr(ri, "_git_show", orig))
        findings, _, _ = self._check("[1] **a** — x\n", "[1] **a** — x\n", base="BADREF")
        self.assertTrue(any("failing closed" in f for f in findings), findings)

    # --- differential checks ----------------------------------------------
    def test_remap_flags_stale_but_resolving_selfref(self):
        # [4] is a renumbered node (remap 2->4) but still refers to old [2].
        # [2] is a valid node, so the ref RESOLVES — only the remap check catches it.
        main = "[1] **a** — x\n[2] **b** — y\n[3] **c** — z\n[4] **d** — see [2]\n"
        findings, _, _ = self._check(main, "", remap="2:4")
        self.assertTrue(any("stale ref" in f for f in findings), findings)
        # ...and WITHOUT the remap it passes (the gap the differential check fills):
        findings_no_remap, _, _ = self._check(main, "")
        self.assertEqual(findings_no_remap, [], findings_no_remap)

    def test_remap_flags_cross_node_stale_ref(self):
        # [4] (new from 2:4) still points at [3], which is OLD (3:5) — a sibling,
        # not [4]'s own paired old. The per-pair check missed this; the
        # region-wide scan must catch it.
        main = "[1] **a** — x\n[2] **b** — y\n[3] **c** — z\n[4] **d** — see [3]\n[5] **e** — w\n"
        findings, _, _ = self._check(main, "", remap="2:4,3:5")
        self.assertTrue(any("stale ref" in f and "[4]" in f and "[3]" in f for f in findings), findings)

    def test_remap_clean_when_no_stale(self):
        main = "[1] **a** — x\n[2] **b** — y\n[3] **c** — z\n[4] **d** — see [5]\n[5] **e** — w\n"
        findings, _, _ = self._check(main, "", remap="2:4,3:5")
        self.assertEqual(findings, [], findings)

    def test_dangling_slug_warns_without_base(self):
        main = "[1] **alpha** — links [[beta-node]] which is undefined\n"
        findings, warnings, _ = self._check(main, "")
        self.assertEqual(findings, [], findings)               # not a hard failure
        self.assertTrue(any("dangling" in w for w in warnings), warnings)

    def test_new_dangling_slug_fails_with_base(self):
        # Monkeypatch _git_show so --base logic runs without a real git repo.
        # Base defines alpha and already dangles [[old-ghost]]; HEAD adds a NEW
        # dangling [[new-ghost]] → only the NEW one is a hard finding.
        base_text = "[1] **alpha** — links [[old-ghost]]\n"
        main = "[1] **alpha** — links [[old-ghost]] and [[new-ghost]]\n"
        orig = ri._git_show
        ri._git_show = lambda ref, path: base_text
        try:
            findings, warnings, _ = self._check(main, "", base="BASEREF")
        finally:
            ri._git_show = orig
        self.assertTrue(any("NEW dangling" in f and "new-ghost" in f for f in findings), findings)
        self.assertTrue(any("inherited dangling" in w and "old-ghost" in w for w in warnings), warnings)

    # --- impl-panel-review hardening --------------------------------------
    def test_remap_stale_ref_survives_code_fence(self):
        # The stale [2] ref sits AFTER a fenced `# comment`. The heading-trim
        # must not truncate the body at the in-fence `#`, or the ref is missed.
        main = ("[1] **a** — x\n[2] **b** — y\n[3] **c** — z\n"
                "[4] **d** — renumbered\n```\n# shell comment\n```\nstill see [2] here\n")
        findings, _, _ = self._check(main, "", remap="2:4")
        self.assertTrue(any("stale ref" in f and "[2]" in f for f in findings), findings)

    def test_arrow_in_prose_is_not_a_summary(self):
        # [1] mentions ↗ in prose (not on its definition line) and has no overflow
        # entry → must NOT trigger the ↗-contract.
        main = "[1] **a** — explains the ↗ contract in prose\n[2] **b** — y\n"
        findings, _, _ = self._check(main, "")
        self.assertEqual(findings, [], findings)

    def test_remap_absent_new_id_fails(self):
        main = "[1] **a** — x\n[2] **b** — y\n"
        findings, _, _ = self._check(main, "", remap="2:99")
        self.assertTrue(any("NEW id" in f and "99" in f for f in findings), findings)

    def test_node_id_below_one_rejected(self):
        main = "[0] **zero** — invalid\n[1] **a** — x\n"
        findings, _, _ = self._check(main)
        self.assertTrue(any("< 1" in f for f in findings), findings)

    def test_base_inherited_dangling_slug_in_overflow_not_new(self):
        # An inherited dangling [[slug]] lives only in base OVERFLOW. It must be
        # treated as inherited (warning), not NEW (finding). [1] is ↗ so the
        # mirror check doesn't interfere.
        base_main = "[1] **alpha** ↗ — summary\n"
        base_overflow = "[1] **alpha** — links [[old-ghost]]\n"
        main = "[1] **alpha** ↗ — summary\n"
        overflow = "[1] **alpha** — links [[old-ghost]]\n"
        orig = ri._git_show
        ri._git_show = lambda ref, path: base_overflow if "OVERFLOW" in path else base_main
        try:
            findings, warnings, _ = self._check(main, overflow, base="BASEREF")
        finally:
            ri._git_show = orig
        self.assertEqual(findings, [], findings)
        self.assertTrue(any("inherited" in w and "old-ghost" in w for w in warnings), warnings)


    # --- task 004: verifier-trust hardening -------------------------------
    def test_trailing_arrow_detected_as_summary(self):
        # ↗ at the END of the definition line is the marker in some repos
        # (ai_ring_vet convention). Must be detected → ↗-contract fires when
        # there's no overflow entry.
        main = "[1] **a** — full detail in overflow ↗\n[2] **b** — x\n"
        findings, _, _ = self._check(main, "")
        self.assertTrue(any("↗-contract" in f for f in findings), findings)


    def test_mirror_remap_translation_flags_renamed_node(self):
        # pF1/F2: base [2] matched its overflow. HEAD renames 2→3 (remap 2:3)
        # and [3]'s overflow copy diverges → the differential mirror must flag
        # [3] (base-id translation works), not the old [2].
        self._with_base("[1] **root** — base\n[2] **x** — body v1\n",
                        "[1] **root** — base\n[2] **x** — body v1\n")
        main = "[1] **root** — base\n[2] **trunk** — new\n[3] **x** — body v1\n"
        overflow = "[1] **root** — base\n[2] **trunk** — new\n[3] **x** — body v2 DIVERGED\n"
        findings, _, _ = self._check(main, overflow, base="B", remap="2:3")
        diverged = [f for f in findings if "diverged" in f]
        self.assertTrue(any("3" in f for f in diverged), findings)
        self.assertFalse(any("[2]" in f for f in diverged), diverged)

    def test_clean_real_repo_style_merge_exits_clean(self):
        # Trailing-↗ summary + complete archive + a clean renumber where the
        # renamed node IS re-mirrored and a new trunk full node IS mirrored →
        # no findings (the "good merge" baseline ref-integrity must pass).
        self._with_base("[1] **root** — base\n[2] **x** — body\n",
                        "[1] **root** — base\n[2] **x** — body\n")
        main = ("[1] **root** — base\n[2] **t** — trunk full\n"
                "[3] **x** — body\n[4] **s** ↗\n")
        overflow = ("[1] **root** — base\n[2] **t** — trunk full\n"
                    "[3] **x** — body\n[4] **s** — full text of s\n")
        findings, _, _ = self._check(main, overflow, base="B", remap="2:3")
        self.assertEqual(findings, [], findings)

    # --- task 007: fence-aware node + slug parsing (P2) ---

    def test_fenced_bracket_not_a_node_id(self):
        # A `[99]` line INSIDE a ``` fence must not be counted as a node
        # definition (no ghost node → no false duplicate/contiguity failure).
        text = ("[1] **a** — body\n"
                "```\n[99] not a real node\n```\n"
                "more body\n[2] **b** — body\n")
        self.assertEqual(ri._node_ids(text), [1, 2])
        self.assertNotIn(99, ri._node_bodies(text))
        # the fenced line stays inside node [1]'s body (not amputated to a node)
        self.assertIn("[99] not a real node", ri._node_bodies(text)[1])

    def test_fenced_bracket_no_false_contiguity_failure(self):
        # End to end: a fenced [99] in an otherwise-contiguous 1..2 map is clean.
        main = ("[1] **a** — see [2]\n"
                "```\n[99] example node line\n```\n"
                "[2] **b** — end\n")
        findings, _, _ = self._check(main, "")
        self.assertEqual(findings, [], findings)

    def test_fenced_title_does_not_define_slug(self):
        # A fenced `[99] **Fake Title**` must NOT define `fake-title` — otherwise
        # it would mask a genuinely dangling [[fake-title]] link as resolved.
        text = ("[1] **Real** — body\n"
                "```\n[99] **Fake Title** — fenced example\n```\n"
                "[2] **Two** — body\n")
        slugs = ri._defined_slugs(text)
        self.assertIn("real", slugs)
        self.assertIn("two", slugs)
        self.assertNotIn("fake-title", slugs)
        # and the dangling-slug check now flags a real [[fake-title]] reference
        self.assertIn("fake-title",
                      ri._dangling_slugs("see [[fake-title]]", slugs))

    def test_fenced_title_dangling_slug_flagged_with_base(self):
        # The whole point: a fenced fake title no longer hides a NEW dangling slug.
        self._with_base("[1] **root** — base\n", "[1] **root** — base\n")
        main = ("[1] **root** — base, see [[fake-title]]\n"
                "```\n[2] **Fake Title** — fenced, not a real node\n```\n")
        findings, _, _ = self._check(main, "", base="B")
        self.assertTrue(any("fake-title" in f for f in findings), findings)

    def test_fenced_slug_reference_not_dangling(self):
        # W8: a [[slug]] reference INSIDE a code fence is illustrative, not a real
        # link — even though the fenced [N] **Title** no longer defines the slug,
        # the fenced reference must be stripped (mirrors _refs), so NO dangling
        # finding. A genuine unfenced dangling [[slug]] is still flagged.
        self._with_base("[1] **root** — base\n", "[1] **root** — base\n")
        main = ("[1] **root** — base\n"
                "```\n[2] **Demo** — example, see [[demo]]\n```\n")
        findings, _, _ = self._check(main, "", base="B")
        self.assertFalse(any("demo" in f for f in findings),
                         f"fenced [[demo]] must not be a dangling finding: {findings}")
        # control: an unfenced dangling slug IS reported
        main2 = "[1] **root** — base, see [[ghost]]\n"
        findings2, _, _ = self._check(main2, "", base="B")
        self.assertTrue(any("ghost" in f for f in findings2), findings2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
