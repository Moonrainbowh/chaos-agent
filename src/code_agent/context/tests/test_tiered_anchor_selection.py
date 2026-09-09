from __future__ import annotations

import unittest

from code_agent.context.models import FileSignature, RepoEntry, Symbol
from code_agent.context.repo_index import RepoIndexSnapshot
from code_agent.context.repo_tiered_context import select_tiered_context


class TieredAnchorSelectionTests(unittest.TestCase):
    def entry(self, path, specs):
        symbols = tuple(Symbol(path, name, "function", start, end, signature, doc)
                        for name, start, end, signature, doc in specs)
        return RepoEntry(path, symbols, (), 1000, FileSignature(1000, 1))

    def select(self, entries, query, ranked=None, touched=()):
        return select_tiered_context(
            RepoIndexSnapshot(7, tuple(entries)),
            tuple(entries if ranked is None else ranked), query, touched, 4000,
        )

    def test_two_explicit_files_each_receive_independent_anchor(self):
        a = self.entry("a.py", [("first", 50, 60, "def first():", "")])
        b = self.entry("b.py", [("second", 50, 60, "def second():", "")])
        selection = self.select((a, b), "inspect b.py and a.py", ranked=(a,))
        self.assertEqual([n.path for n in selection.l0], ["b.py", "a.py"])
        self.assertTrue(all(n.kind == "line" and not n.symbol for n in selection.l0))

    def test_reranking_selects_relevant_docstring_instead_of_first_symbol(self):
        entry = self.entry("worker.py", [
            ("first", 1, 5, "def first():", "format dates"),
            ("process", 10, 20, "def process(data):", "validate payload checksum"),
        ])
        selection = self.select((entry,), "repair payload checksum")
        self.assertEqual([n.symbol for n in selection.l0], ["process"])

    def test_name_components_and_signature_are_reranking_signals(self):
        entry = self.entry("worker.py", [
            ("first", 1, 5, "def first():", ""),
            ("loadPayload", 10, 20, "def loadPayload(checksum):", ""),
        ])
        selection = self.select((entry,), "payload checksum")
        self.assertEqual([n.symbol for n in selection.l0], ["loadPayload"])

    def test_low_confidence_uses_bounded_module_slice(self):
        entry = self.entry("worker.py", [("first", 100, 150, "def first():", "payload")])
        for query in ("unrelated issue", "payload", "inspect worker.py"):
            with self.subTest(query=query):
                node = self.select((entry,), query).l0[0]
                self.assertEqual((node.kind, node.symbol, node.start_line, node.end_line),
                                 ("line", "", 1, 40))
                self.assertEqual(node.signature, entry.signature)

    def test_two_explicit_symbols_in_same_file_are_supported(self):
        entry = self.entry("worker.py", [
            ("first", 1, 5, "", ""), ("second", 10, 15, "", ""),
        ])
        selection = self.select((entry,), "compare first and second")
        self.assertEqual([n.symbol for n in selection.l0], ["first", "second"])

    def test_two_path_lines_precede_symbol_and_select_innermost_scope(self):
        a = self.entry("src/含 空格.py", [
            ("outer", 1, 100, "", ""), ("inner", 10, 15, "", ""),
        ])
        b = self.entry("b.py", [("other", 5, 10, "", "")])
        selection = self.select((a, b), 'outer "src/含 空格.py:12" and b.py:8')
        self.assertEqual([n.symbol for n in selection.l0], ["inner", "other"])

    def test_explicit_symbol_is_found_outside_ranked_files(self):
        a = self.entry("a.py", [("irrelevant", 1, 5, "", "")])
        b = self.entry("b.py", [("wanted", 10, 15, "", "")])
        selection = self.select((a, b), "repair wanted", ranked=(a,))
        self.assertEqual([n.symbol for n in selection.l0], ["wanted"])

    def test_partial_identifier_is_not_an_exact_symbol_match(self):
        entry = self.entry("worker.py", [("run", 50, 55, "", "")])
        node = self.select((entry,), "runtime problem").l0[0]
        self.assertEqual(node.kind, "line")

    def test_overlapping_and_repeated_anchors_are_deduplicated(self):
        entry = self.entry("worker.py", [
            ("outer", 1, 100, "", ""), ("inner", 10, 15, "", ""),
        ])
        selection = self.select((entry,), "worker.py:12 worker.py:13 outer inner")
        self.assertEqual([n.symbol for n in selection.l0], ["inner"])

    def test_reranking_can_select_two_and_is_deterministic(self):
        entry = self.entry("worker.py", [
            ("decodePayload", 1, 5, "", ""),
            ("encodePayload", 10, 15, "", ""),
            ("savePayload", 20, 25, "", ""),
        ])
        first = self.select((entry,), "payload")
        self.assertEqual([n.symbol for n in first.l0], ["decodePayload", "encodePayload"])
        self.assertEqual(first, self.select((entry,), "payload"))

    def test_touched_breaks_equal_scores_but_does_not_create_confidence(self):
        a = self.entry("a.py", [("process", 1, 5, "", "payload checksum")])
        b = self.entry("b.py", [("handle", 1, 5, "", "payload checksum")])
        selection = self.select((a, b), "payload checksum", touched=("b.py",))
        self.assertEqual([n.path for n in selection.l0], ["b.py", "a.py"])
        fallback = self.select((a, b), "unrelated", touched=("b.py",)).l0[0]
        self.assertEqual((fallback.path, fallback.kind), ("b.py", "line"))

    def test_absolute_path_and_symbol_free_file_have_bounded_anchors(self):
        entry = self.entry("src/config.py", [])
        selection = self.select((entry,), "F:/project/src/config.py:7")
        self.assertEqual((selection.l0[0].start_line, selection.l0[0].end_line), (1, 27))
