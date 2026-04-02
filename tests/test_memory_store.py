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

    def test_semantic_search_embedding_mode_returns_relevant_records(self):
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

        results = ms.semantic_search(query="api timeout", limit=5, mode="embedding")
        self.assertGreaterEqual(len(results), 1)
        top = results[0]["record"]
        self.assertEqual(top.path, "tests/test_api.py")

    def test_semantic_search_embedding_mode_path_filter(self):
        ms = self._make_store()
        ms.record_fix_attempt(objective="python syntax", path="a.py", line=1, outcome="failure", hint="indent")
        ms.record_fix_attempt(objective="python syntax", path="b.py", line=1, outcome="failure", hint="indent")

        results = ms.semantic_search(query="python indent", limit=10, path="b.py", mode="embedding")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["record"].path, "b.py")

    def test_semantic_search_auto_mode_returns_results(self):
        ms = self._make_store()
        ms.record_fix_attempt(
            objective="api timeout retry bug",
            path="services/api.py",
            line=22,
            outcome="failure",
            hint="timeout in integration test",
        )
        results = ms.semantic_search(query="api timeout", limit=5, mode="auto")
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["record"].path, "services/api.py")

    def test_semantic_search_invalid_mode_falls_back_overlap(self):
        ms = self._make_store()
        ms.record_fix_attempt(
            objective="database connection timeout",
            path="db/client.py",
            line=8,
            outcome="failure",
            hint="retry missing",
        )
        results = ms.semantic_search(query="connection timeout", limit=3, mode="unknown")
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["record"].path, "db/client.py")

    def test_semantic_search_time_weight_prefers_recent_records(self):
        ms = self._make_store()

        # eski ama daha alakalı kayıt
        ms.record(
            MemoryRecord(
                kind="fix_attempt",
                objective="api timeout retry",
                outcome="failure",
                path="old.py",
                hint="timeout retry",
                ts=1000.0,
            )
        )
        # daha yeni ama biraz daha az alakalı kayıt
        ms.record(
            MemoryRecord(
                kind="fix_attempt",
                objective="api timeout",
                outcome="failure",
                path="new.py",
                hint="timeout",
                ts=time.time(),
            )
        )

        no_time = ms.semantic_search(
            query="api timeout retry",
            limit=2,
            mode="overlap",
            time_weight=0.0,
        )
        strong_time = ms.semantic_search(
            query="api timeout retry",
            limit=2,
            mode="overlap",
            time_weight=0.95,
            half_life_days=1,
        )

        self.assertEqual(no_time[0]["record"].path, "old.py")
        self.assertEqual(strong_time[0]["record"].path, "new.py")

    def test_semantic_search_result_contains_relevance_and_freshness(self):
        ms = self._make_store()
        ms.record_fix_attempt(
            objective="cache timeout",
            path="cache.py",
            line=1,
            outcome="failure",
            hint="timeout",
        )
        results = ms.semantic_search(query="cache timeout", limit=3, mode="auto")
        self.assertGreaterEqual(len(results), 1)
        self.assertIn("relevance", results[0])
        self.assertIn("freshness", results[0])

    def test_hash_index_created_and_synced(self):
        ms = self._make_store()
        ms.record_fix_attempt(objective="api timeout", path="a.py", line=1, outcome="failure", hint="timeout")
        ms.record_fix_attempt(objective="retry fix", path="b.py", line=2, outcome="success", hint="retry")

        self.assertTrue(ms.index_path.exists())
        self.assertEqual(ms._line_count(ms.path), ms._line_count(ms.index_path))

    def test_hash_index_rebuild_after_restart(self):
        ms = self._make_store()
        ms.record_fix_attempt(objective="api timeout", path="services/api.py", line=10, outcome="failure", hint="timeout")
        ms.record_fix_attempt(objective="cache timeout", path="services/cache.py", line=5, outcome="failure", hint="cache")

        # Yeni instance ile index üzerinden arama yapılabildiğini doğrula.
        ms2 = MemoryStore(ms.path)
        ms2.ensure_hash_index()
        results = ms2.semantic_search(query="api timeout", mode="embedding", limit=5)
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["record"].path, "services/api.py")

    def test_recommended_mode_empty_store(self):
        ms = self._make_store()
        self.assertEqual(ms.recommended_mode(), "overlap")

    def test_benchmark_search_returns_mode_stats(self):
        ms = self._make_store()
        ms.record_fix_attempt(objective="api timeout retry", path="svc/api.py", line=1, outcome="failure", hint="timeout")
        ms.record_fix_attempt(objective="cache timeout", path="svc/cache.py", line=2, outcome="success", hint="cache")

        bench = ms.benchmark_search(query="timeout", repeats=2, limit=5)
        self.assertIn("recommended_mode", bench)
        self.assertIn("modes", bench)
        self.assertIn("overlap", bench["modes"])
        self.assertIn("embedding", bench["modes"])
        self.assertIn("auto", bench["modes"])
        self.assertGreaterEqual(bench["modes"]["overlap"]["avg_ms"], 0.0)

    def test_semantic_search_ann_mode_returns_relevant_records(self):
        ms = self._make_store()
        ms.record_fix_attempt(
            objective="api timeout retry",
            path="services/api.py",
            line=10,
            outcome="failure",
            hint="retry timeout",
        )
        ms.record_fix_attempt(
            objective="ui spacing",
            path="public/dashboard.html",
            line=2,
            outcome="success",
            hint="css",
        )

        results = ms.semantic_search(query="api timeout", mode="ann", limit=5)
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["record"].path, "services/api.py")

    def test_recommended_mode_large_store_prefers_ann(self):
        ms = self._make_store()
        for i in range(320):
            ms.record_fix_attempt(
                objective=f"obj-{i} timeout",
                path=f"svc/file_{i}.py",
                line=i,
                outcome="failure" if i % 2 else "success",
                hint="timeout",
            )
        self.assertEqual(ms.recommended_mode(), "ann")


class DreamConsolidatorTests(unittest.TestCase):
    def _make_store(self):
        self.tmp = tempfile.TemporaryDirectory()
        return MemoryStore(Path(self.tmp.name) / "mem.jsonl")

    def tearDown(self):
        if hasattr(self, "tmp"):
            self.tmp.cleanup()

    def test_dream_merges_duplicate_records(self):
        """Duplicate records (same path+kind, similar objective) should be merged."""
        from dream_consolidator import DreamConsolidator

        ms = self._make_store()
        # Add 3 duplicates with the same path and very similar objectives
        for i in range(3):
            ms.record_fix_attempt(
                objective="fix authentication bug in login module",
                path="auth.py",
                line=10,
                outcome="failure",
                hint=f"hint-{i}",
            )

        dc = DreamConsolidator(ms)
        result = dc.consolidate(similarity_threshold=0.5)

        self.assertEqual(result["total_before"], 3)
        self.assertEqual(result["merged_count"], 1)
        self.assertEqual(result["removed_count"], 2)
        self.assertEqual(result["kept_count"], 1)

        # After merge, only 1 record should remain
        recs = ms.load_all()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].meta.get("hit_count"), 3)
        self.assertTrue(recs[0].meta.get("dream_merged"))

    def test_dream_preserves_unique_records(self):
        """Records with different paths should not be merged."""
        from dream_consolidator import DreamConsolidator

        ms = self._make_store()
        ms.record_fix_attempt(objective="fix login", path="auth.py", line=1, outcome="failure")
        ms.record_fix_attempt(objective="fix logging", path="logger.py", line=5, outcome="failure")
        ms.record_fix_attempt(objective="fix config", path="config.py", line=20, outcome="success")

        dc = DreamConsolidator(ms)
        result = dc.consolidate(similarity_threshold=0.6)

        # All 3 are on different paths, none should merge
        self.assertEqual(result["merged_count"], 0)
        self.assertEqual(result["removed_count"], 0)
        self.assertEqual(result["kept_count"], 3)

    def test_dream_importance_score_calculated(self):
        """Merged records must have importance_score > 0."""
        from dream_consolidator import DreamConsolidator

        ms = self._make_store()
        for _ in range(4):
            ms.record_fix_attempt(
                objective="fix database connection timeout error",
                path="db.py",
                line=50,
                outcome="failure",
                hint="check pool size",
            )

        dc = DreamConsolidator(ms)
        dc.consolidate(similarity_threshold=0.5)

        recs = ms.load_all()
        self.assertEqual(len(recs), 1)
        score = recs[0].meta.get("importance_score", 0)
        self.assertGreater(score, 0)

    def test_dream_session_tracking(self):
        """record_session increments sessions_since_dream counter."""
        from dream_consolidator import DreamConsolidator

        ms = self._make_store()
        dc = DreamConsolidator(ms)

        for sid in ["sess-1", "sess-2", "sess-3"]:
            dc.record_session(sid)

        st = dc.get_status()
        self.assertEqual(st["sessions_since_dream"], 3)
        self.assertIn("sess-1", st["session_ids"])

    def test_dream_should_not_trigger_if_too_few_sessions(self):
        """should_dream returns False if sessions_since_dream < min_sessions."""
        from dream_consolidator import DreamConsolidator

        ms = self._make_store()
        dc = DreamConsolidator(ms)
        dc.record_session("s1")
        dc.record_session("s2")

        ok, reason = dc.should_dream(min_sessions=5, min_hours=0.0)
        self.assertFalse(ok)
        self.assertIn("sessions_since_dream", reason)

    def test_dream_should_trigger_when_conditions_met(self):
        """should_dream returns True when session count and time conditions are met."""
        from dream_consolidator import DreamConsolidator

        ms = self._make_store()
        dc = DreamConsolidator(ms)

        for i in range(5):
            dc.record_session(f"sess-{i}")

        ok, reason = dc.should_dream(min_sessions=5, min_hours=0.0)
        self.assertTrue(ok)

    def test_maybe_dream_runs_when_forced(self):
        """maybe_dream(force=True) runs consolidation ignoring trigger conditions."""
        from dream_consolidator import DreamConsolidator

        ms = self._make_store()
        for _ in range(2):
            ms.record_fix_attempt(
                objective="fix the same bug again",
                path="app.py",
                line=1,
                outcome="failure",
                hint="check imports",
            )

        dc = DreamConsolidator(ms)
        result = dc.maybe_dream(force=True, similarity_threshold=0.4)

        self.assertTrue(result["ran"])
        self.assertEqual(result["reason"], "consolidated")
        self.assertIsNotNone(result.get("result"))

    def test_maybe_dream_updates_status_after_run(self):
        """After a successful dream, status resets session count and records timestamp."""
        from dream_consolidator import DreamConsolidator

        ms = self._make_store()
        dc = DreamConsolidator(ms)

        for i in range(5):
            dc.record_session(f"s-{i}")

        dc.maybe_dream(force=True)

        st = dc.get_status()
        self.assertEqual(st["sessions_since_dream"], 0)
        self.assertIsNotNone(st["last_dream_ts"])
        self.assertEqual(st["total_dreams"], 1)

    def test_dream_lock_prevents_concurrent_runs(self):
        """If lock file exists and is fresh, maybe_dream returns lock_held."""
        from dream_consolidator import DreamConsolidator

        ms = self._make_store()
        dc = DreamConsolidator(ms)

        # Write a fresh lock file
        dc.lock_path.parent.mkdir(parents=True, exist_ok=True)
        dc.lock_path.write_text("99999", encoding="utf-8")

        result = dc.maybe_dream(force=True)
        self.assertFalse(result["ran"])
        self.assertEqual(result["reason"], "lock_held")

        # Cleanup
        dc.lock_path.unlink()

    def test_dream_merged_hints_combined(self):
        """Merged record should combine hints from duplicate records."""
        from dream_consolidator import DreamConsolidator

        ms = self._make_store()
        hints = ["check config", "verify env", "restart service"]
        for h in hints:
            ms.record_fix_attempt(
                objective="service startup failure fix",
                path="service.py",
                line=1,
                outcome="failure",
                hint=h,
            )

        dc = DreamConsolidator(ms)
        dc.consolidate(similarity_threshold=0.5)

        recs = ms.load_all()
        self.assertEqual(len(recs), 1)
        # At least some hints combined
        self.assertTrue(len(recs[0].hint) > 0)


if __name__ == "__main__":
    unittest.main()
