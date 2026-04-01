"""Unit tests for MemoryStore."""

import tempfile
import time
import unittest
from pathlib import Path

from memory_store import MemoryRecord, MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def _make_store(self):
        self.tmp = tempfile.TemporaryDirectory()
        return MemoryStore(Path(self.tmp.name) / "mem.jsonl")

    def tearDown(self):
        if hasattr(self, "tmp"):
            self.tmp.cleanup()

    def test_record_and_load(self):
        ms = self._make_store()
        ms.record_fix_attempt(objective="fix bug", path="app/core.py", line=12, outcome="failure", hint="err")
        recs = ms.load_all()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].path, "app/core.py")
        self.assertEqual(recs[0].outcome, "failure")

    def test_record_rollback(self):
        ms = self._make_store()
        ms.record_rollback(objective="fix bug", path="app/core.py", hint="narrow fail", trigger="narrow_test_fail")
        recs = ms.load_all()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].kind, "rollback")

    def test_recall_failures_for_path(self):
        ms = self._make_store()
        ms.record_fix_attempt(objective="o", path="a.py", line=1, outcome="failure")
        ms.record_fix_attempt(objective="o", path="a.py", line=2, outcome="success")
        ms.record_fix_attempt(objective="o", path="b.py", line=1, outcome="failure")
        failures = ms.recall_failures_for_path("a.py")
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].line, 1)

    def test_success_rate(self):
        ms = self._make_store()
        ms.record_fix_attempt(objective="o", path="a.py", line=1, outcome="failure")
        ms.record_fix_attempt(objective="o", path="a.py", line=1, outcome="success")
        rate = ms.success_rate_for_path("a.py")
        self.assertAlmostEqual(rate, 0.5)

    def test_recall_recent_ordering(self):
        ms = self._make_store()
        for i in range(5):
            r = MemoryRecord(kind="fix_attempt", objective="o", outcome="success", ts=float(i))
            ms.record(r)
        recent = ms.recall_recent(limit=3)
        self.assertEqual(len(recent), 3)
        self.assertEqual(recent[-1].ts, 4.0)

    def test_empty_store(self):
        ms = self._make_store()
        self.assertEqual(ms.load_all(), [])
        self.assertEqual(ms.success_rate_for_path("x.py"), 0.0)

    def test_semantic_search_returns_relevant_records(self):
        ms = self._make_store()
        ms.record_fix_attempt(
            objective="fix flaky unittest timeout",
            path="tests/test_api.py",
            line=10,
            outcome="failure",
            hint="timeout error in api test",
        )
        ms.record_fix_attempt(
            objective="refactor css",
            path="public/dashboard.html",
            line=99,
            outcome="success",
            hint="ui spacing",
        )

        results = ms.semantic_search(query="api timeout test", limit=5)
        self.assertGreaterEqual(len(results), 1)
        top = results[0]["record"]
        self.assertEqual(top.path, "tests/test_api.py")

    def test_semantic_search_path_filter(self):
        ms = self._make_store()
        ms.record_fix_attempt(objective="python syntax", path="a.py", line=1, outcome="failure", hint="indent")
        ms.record_fix_attempt(objective="python syntax", path="b.py", line=1, outcome="failure", hint="indent")

        results = ms.semantic_search(query="python indent", limit=10, path="b.py")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["record"].path, "b.py")


if __name__ == "__main__":
    unittest.main()
