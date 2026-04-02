"""Unit tests for QualityEvaluator."""

import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from memory_store import MemoryRecord, MemoryStore
from quality_evaluator import QualityEvaluator


class QualityEvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.mem_path = self.base / "memory.jsonl"
        self.log_dir = self.base / "log"
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_trace_events(self, events: list[dict]):
        trace_file = self.log_dir / f"agent_trace_{datetime.now().strftime('%Y%m%d')}.jsonl"
        with trace_file.open("w", encoding="utf-8") as fh:
            for e in events:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")

    def test_summary_empty_data(self):
        ev = QualityEvaluator(memory_path=self.mem_path, trace_dir=self.log_dir)
        out = ev.summary(lines=100, days=7)
        self.assertEqual(out["status"], "critical")
        self.assertEqual(out["trace"]["events"], 0)
        self.assertEqual(out["memory"]["recent_records"], 0)

    def test_summary_healthy_with_good_signals(self):
        ms = MemoryStore(self.mem_path)
        now = time.time()
        ms.record(MemoryRecord(kind="fix_attempt", objective="o1", outcome="success", ts=now))
        ms.record(MemoryRecord(kind="fix_attempt", objective="o2", outcome="success", ts=now))
        self._write_trace_events(
            [
                {"event": "tool_result", "ok": True},
                {"event": "tool_result", "ok": True},
                {"event": "tool_result", "ok": True},
            ]
        )

        ev = QualityEvaluator(memory_path=self.mem_path, trace_dir=self.log_dir)
        out = ev.summary(lines=100, days=7)
        self.assertEqual(out["status"], "healthy")
        self.assertTrue(out["promotion"]["ready"])

    def test_summary_penalized_by_rollbacks(self):
        ms = MemoryStore(self.mem_path)
        now = time.time()
        ms.record(MemoryRecord(kind="fix_attempt", objective="o1", outcome="success", ts=now))
        ms.record(MemoryRecord(kind="rollback", objective="o1", outcome="rollback", ts=now))
        ms.record(MemoryRecord(kind="rollback", objective="o2", outcome="rollback", ts=now))
        self._write_trace_events(
            [
                {"event": "tool_result", "ok": True},
                {"event": "auto_rollback_applied"},
                {"event": "auto_rollback_applied_broad"},
            ]
        )

        ev = QualityEvaluator(memory_path=self.mem_path, trace_dir=self.log_dir)
        out = ev.summary(lines=100, days=7)
        self.assertFalse(out["promotion"]["ready"])
        self.assertLess(out["score"], 100.0)

    def test_canary_decision_promote(self):
        ms = MemoryStore(self.mem_path)
        now = time.time()
        ms.record(MemoryRecord(kind="fix_attempt", objective="o1", outcome="success", ts=now))
        ms.record(MemoryRecord(kind="fix_attempt", objective="o2", outcome="success", ts=now))
        self._write_trace_events(
            [
                {"event": "tool_result", "ok": True},
                {"event": "tool_result", "ok": True},
                {"event": "tool_result", "ok": True},
                {"event": "tool_result", "ok": True},
            ]
        )

        ev = QualityEvaluator(memory_path=self.mem_path, trace_dir=self.log_dir)
        summary = ev.summary(lines=100, days=7)
        decision = ev.canary_decision(summary)
        self.assertEqual(decision["decision"], "promote")

    def test_canary_decision_rollback_on_low_score(self):
        ms = MemoryStore(self.mem_path)
        now = time.time()
        ms.record(MemoryRecord(kind="fix_attempt", objective="o1", outcome="failure", ts=now))
        ms.record(MemoryRecord(kind="rollback", objective="o1", outcome="rollback", ts=now))
        self._write_trace_events(
            [
                {"event": "tool_result", "ok": False},
                {"event": "tool_result", "ok": False},
                {"event": "tool_denied"},
                {"event": "tool_denied"},
                {"event": "tool_denied"},
                {"event": "tool_denied"},
                {"event": "tool_denied"},
                {"event": "tool_denied"},
            ]
        )

        ev = QualityEvaluator(memory_path=self.mem_path, trace_dir=self.log_dir)
        summary = ev.summary(lines=100, days=7)
        decision = ev.canary_decision(summary)
        self.assertEqual(decision["decision"], "rollback")

    def test_adaptive_thresholds_increase_strictness_on_high_volatility(self):
        ms = MemoryStore(self.mem_path)
        now = time.time()
        ms.record(MemoryRecord(kind="fix_attempt", objective="o1", outcome="failure", ts=now))
        ms.record(MemoryRecord(kind="rollback", objective="o1", outcome="rollback", ts=now))
        self._write_trace_events(
            [
                {"event": "tool_result", "ok": False},
                {"event": "tool_result", "ok": False},
                {"event": "tool_denied"},
                {"event": "tool_denied"},
                {"event": "auto_rollback_applied"},
            ]
        )

        ev = QualityEvaluator(memory_path=self.mem_path, trace_dir=self.log_dir)
        summary = ev.summary(lines=100, days=7)
        thresholds = ev.adaptive_thresholds(summary)

        self.assertGreaterEqual(thresholds["min_promote_score"], 80.0)
        self.assertLessEqual(thresholds["rollback_score"], 45.0)
        self.assertIn("signals", thresholds)
        self.assertIn("volatility", thresholds["signals"])

    def test_canary_decision_adaptive_sets_flag(self):
        ms = MemoryStore(self.mem_path)
        now = time.time()
        ms.record(MemoryRecord(kind="fix_attempt", objective="o1", outcome="success", ts=now))
        self._write_trace_events(
            [
                {"event": "tool_result", "ok": True},
                {"event": "tool_result", "ok": True},
            ]
        )

        ev = QualityEvaluator(memory_path=self.mem_path, trace_dir=self.log_dir)
        summary = ev.summary(lines=100, days=7)
        decision = ev.canary_decision(summary, adaptive=True)
        self.assertTrue(decision["adaptive"])
        self.assertIn("thresholds", decision)

    def test_trend_window_summary_contains_1h_and_24h(self):
        now = datetime.now()
        self._write_trace_events(
            [
                {"ts": (now - timedelta(minutes=20)).isoformat(), "event": "tool_result", "ok": True},
                {"ts": (now - timedelta(hours=3)).isoformat(), "event": "tool_result", "ok": True},
            ]
        )
        ev = QualityEvaluator(memory_path=self.mem_path, trace_dir=self.log_dir)
        out = ev.trend_window_summary(lines=100)
        self.assertIn("1h", out["windows"])
        self.assertIn("24h", out["windows"])

    def test_trend_canary_decision_detects_short_window_drop(self):
        now = datetime.now()
        events = []
        # son 24h icinde iyi birikim
        for _ in range(20):
            events.append({"ts": (now - timedelta(hours=8)).isoformat(), "event": "tool_result", "ok": True})
        # son 1h icinde bozulma
        for _ in range(8):
            events.append({"ts": (now - timedelta(minutes=10)).isoformat(), "event": "tool_result", "ok": False})
        for _ in range(5):
            events.append({"ts": (now - timedelta(minutes=5)).isoformat(), "event": "tool_denied"})

        self._write_trace_events(events)
        ev = QualityEvaluator(memory_path=self.mem_path, trace_dir=self.log_dir)
        out = ev.trend_canary_decision(lines=1000)
        self.assertIn(out["decision"], {"hold", "rollback"})
        self.assertIn("trend", out)

    def test_trend_canary_decision_ewma_metadata_and_alpha_clamp(self):
        now = datetime.now()
        self._write_trace_events(
            [
                {"ts": (now - timedelta(minutes=5)).isoformat(), "event": "tool_result", "ok": True},
                {"ts": (now - timedelta(hours=6)).isoformat(), "event": "tool_result", "ok": True},
            ]
        )
        ev = QualityEvaluator(memory_path=self.mem_path, trace_dir=self.log_dir)
        out = ev.trend_canary_decision(lines=200, use_ewma=True, ewma_alpha=2.0)
        self.assertIn("ewma", out)
        self.assertTrue(out["ewma"]["enabled"])
        self.assertLessEqual(out["ewma"]["alpha"], 0.95)

    def test_trend_canary_decision_includes_sync_risk_payload(self):
        now = datetime.now()
        self._write_trace_events(
            [
                {"ts": (now - timedelta(minutes=10)).isoformat(), "event": "tool_result", "ok": True},
                {"ts": (now - timedelta(hours=2)).isoformat(), "event": "tool_result", "ok": True},
            ]
        )
        ev = QualityEvaluator(memory_path=self.mem_path, trace_dir=self.log_dir)
        with patch.object(
            QualityEvaluator,
            "get_calendar_sync_status",
            return_value={
                "trend_summary": {
                    "risk_confidence": {
                        "score": 0.62,
                        "level": "medium",
                        "components": {"latency": 0.4},
                    }
                }
            },
        ):
            out = ev.trend_canary_decision(lines=200)
        self.assertIn("sync_risk", out)
        self.assertEqual(out["sync_risk"].get("status"), "ok")
        self.assertEqual(out["sync_risk"].get("level"), "medium")

    def test_trend_canary_decision_sync_risk_high_forces_rollback(self):
        now = datetime.now()
        self._write_trace_events(
            [
                {"ts": (now - timedelta(minutes=8)).isoformat(), "event": "tool_result", "ok": True},
                {"ts": (now - timedelta(hours=3)).isoformat(), "event": "tool_result", "ok": True},
            ]
        )
        ev = QualityEvaluator(memory_path=self.mem_path, trace_dir=self.log_dir)
        prev_en = os.environ.get("AGENT_CANARY_TREND_SYNC_RISK")
        prev_hi = os.environ.get("AGENT_CANARY_TREND_SYNC_RISK_HIGH")
        os.environ["AGENT_CANARY_TREND_SYNC_RISK"] = "1"
        os.environ["AGENT_CANARY_TREND_SYNC_RISK_HIGH"] = "0.7"
        try:
            with patch.object(
                QualityEvaluator,
                "get_calendar_sync_status",
                return_value={
                    "trend_summary": {
                        "risk_confidence": {
                            "score": 0.9,
                            "level": "high",
                            "components": {"latency": 0.8},
                        }
                    }
                },
            ):
                out = ev.trend_canary_decision(lines=200)
            self.assertEqual(out.get("decision"), "rollback")
            self.assertEqual(out.get("reason", [""])[0], "sync_risk_high")
        finally:
            if prev_en is None:
                os.environ.pop("AGENT_CANARY_TREND_SYNC_RISK", None)
            else:
                os.environ["AGENT_CANARY_TREND_SYNC_RISK"] = prev_en
            if prev_hi is None:
                os.environ.pop("AGENT_CANARY_TREND_SYNC_RISK_HIGH", None)
            else:
                os.environ["AGENT_CANARY_TREND_SYNC_RISK_HIGH"] = prev_hi

    def test_trend_canary_decision_persists_explainability_snapshot(self):
        now = datetime.now()
        self._write_trace_events(
            [
                {"ts": (now - timedelta(minutes=6)).isoformat(), "event": "tool_result", "ok": True},
                {"ts": (now - timedelta(hours=2)).isoformat(), "event": "tool_result", "ok": True},
            ]
        )
        ev = QualityEvaluator(memory_path=self.mem_path, trace_dir=self.log_dir)
        snap_file = self.base / "trend_snapshots.jsonl"

        prev_file = os.environ.get("AGENT_CANARY_TREND_EXPLAINABILITY_SNAPSHOT_FILE")
        prev_on = os.environ.get("AGENT_CANARY_TREND_EXPLAINABILITY_SNAPSHOT")
        os.environ["AGENT_CANARY_TREND_EXPLAINABILITY_SNAPSHOT_FILE"] = str(snap_file)
        os.environ["AGENT_CANARY_TREND_EXPLAINABILITY_SNAPSHOT"] = "1"
        try:
            out = ev.trend_canary_decision(lines=300)
            self.assertIn("decision", out)
            self.assertTrue(snap_file.exists())
            loaded = QualityEvaluator.get_trend_explainability_snapshots(limit=5)
            self.assertGreaterEqual(len(loaded.get("items", [])), 1)
            self.assertIn("explainability_tree", loaded["items"][-1])
        finally:
            for key, prev in [
                ("AGENT_CANARY_TREND_EXPLAINABILITY_SNAPSHOT_FILE", prev_file),
                ("AGENT_CANARY_TREND_EXPLAINABILITY_SNAPSHOT", prev_on),
            ]:
                if prev is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = prev

    def test_trend_canary_decision_seasonality_metadata(self):
        now = datetime.now()
        events = []
        current_hour = now.hour
        other_hour = (current_hour + 1) % 24

        # Simule: current hour historically noisy, other hour stable
        for _ in range(8):
            events.append(
                {
                    "ts": (now - timedelta(days=1)).replace(hour=current_hour, minute=10, second=0, microsecond=0).isoformat(),
                    "event": "tool_result",
                    "ok": False,
                }
            )
        for _ in range(10):
            events.append(
                {
                    "ts": (now - timedelta(days=1)).replace(hour=other_hour, minute=10, second=0, microsecond=0).isoformat(),
                    "event": "tool_result",
                    "ok": True,
                }
            )

        self._write_trace_events(events)
        ev = QualityEvaluator(memory_path=self.mem_path, trace_dir=self.log_dir)
        out = ev.trend_canary_decision(lines=1000, use_seasonality=True, seasonality_lookback_days=7)
        self.assertIn("seasonality", out)
        self.assertTrue(out["seasonality"]["enabled"])
        self.assertEqual(out["seasonality"]["lookback_days"], 7)
        self.assertIn("adjustment", out["seasonality"])
        self.assertIn("hour_baseline", out["seasonality"])
        self.assertIn("dow_hour_baseline", out["seasonality"])
        self.assertIn("dow_observed", out["seasonality"])
        self.assertIn("used_baseline", out["seasonality"])
        self.assertIn("cluster_blended_baseline", out["seasonality"])
        self.assertIn("weekday_cluster", out["seasonality"])
        self.assertIn("cluster_baseline", out["seasonality"])
        self.assertIn("neighbor_baseline", out["seasonality"])
        self.assertIn("cluster_similarity", out["seasonality"])
        self.assertIn("cluster_adaptive_coef", out["seasonality"])
        self.assertIn("cluster_coverage", out["seasonality"])
        self.assertIn("neighbor_coverage", out["seasonality"])
        self.assertIn("hour_neighbor_baseline", out["seasonality"])
        self.assertIn("hour_neighbor_similarity", out["seasonality"])
        self.assertIn("hour_neighbor_adaptive_coef", out["seasonality"])
        self.assertIn("hour_neighbor_coverage", out["seasonality"])
        self.assertIn("matrix_blended_baseline", out["seasonality"])
        self.assertIn("calendar_override_active", out["seasonality"])
        self.assertIn("calendar_label", out["seasonality"])
        self.assertIn("calendar_baseline", out["seasonality"])
        self.assertIn("calendar_adaptive_coef", out["seasonality"])
        self.assertIn("calendar_coverage", out["seasonality"])
        self.assertIn("calendar_severity", out["seasonality"])
        self.assertIn("calendar_severity_weight", out["seasonality"])
        self.assertIn("dow_hour_samples", out["seasonality"])
        self.assertIn("hour_samples", out["seasonality"])
        self.assertIn("sample_confidence", out["seasonality"])
        self.assertIn("variance_confidence", out["seasonality"])
        self.assertIn("volatility_confidence", out["seasonality"])
        self.assertIn("drift_confidence", out["seasonality"])
        self.assertIn("variance", out["seasonality"])
        self.assertIn("volatility", out["seasonality"])
        self.assertIn("trend_drift", out["seasonality"])
        self.assertIn("confidence", out["seasonality"])
        self.assertGreaterEqual(out["seasonality"]["confidence"], 0.0)
        self.assertLessEqual(out["seasonality"]["confidence"], 1.0)

    def test_trend_canary_decision_seasonality_dow_hour_fallback(self):
        now = datetime.now()
        events = []

        # Exact current dow-hour cell may be absent; hour-level history still exists.
        for d in range(1, 7):
            ts = (now - timedelta(days=d)).replace(hour=now.hour, minute=0, second=0, microsecond=0)
            events.append({"ts": ts.isoformat(), "event": "tool_result", "ok": (d % 2 == 0)})

        self._write_trace_events(events)
        ev = QualityEvaluator(memory_path=self.mem_path, trace_dir=self.log_dir)
        out = ev.trend_canary_decision(lines=1000, use_seasonality=True, seasonality_lookback_days=30)

        self.assertIn("seasonality", out)
        seas = out["seasonality"]
        self.assertIn("hour_baseline", seas)
        self.assertIn("dow_hour_baseline", seas)
        self.assertIn("dow_observed", seas)
        self.assertIn("used_baseline", seas)
        self.assertIn("confidence", seas)
        self.assertIn("cluster_adaptive_coef", seas)
        self.assertIn("hour_neighbor_adaptive_coef", seas)
        self.assertIn("calendar_override_active", seas)
        self.assertEqual(seas.get("dow_hour_samples", -1), 0)
        self.assertEqual(seas.get("sample_confidence", 1.0), 0.0)
        self.assertEqual(seas.get("drift_confidence", 1.0), 1.0)
        self.assertEqual(seas.get("confidence", 1.0), 0.0)
        self.assertFalse(seas.get("calendar_override_active", True))
        self.assertEqual(seas.get("calendar_severity"), "medium")
        self.assertEqual(seas.get("calendar_severity_weight"), 1.0)
        self.assertGreaterEqual(seas.get("cluster_adaptive_coef", -1.0), 0.0)
        self.assertLessEqual(seas.get("cluster_adaptive_coef", 2.0), 1.0)
        self.assertGreaterEqual(seas.get("hour_neighbor_adaptive_coef", -1.0), 0.0)
        self.assertLessEqual(seas.get("hour_neighbor_adaptive_coef", 2.0), 1.0)
        self.assertIsInstance(seas.get("adjustment", 0.0), float)

    def test_trend_canary_decision_seasonality_variance_reduces_confidence(self):
        now = datetime.now()
        events = []

        # Build enough samples in current dow-hour but with high variance.
        for i in range(14):
            ts = (now - timedelta(days=7 * (i + 1))).replace(
                hour=now.hour,
                minute=5,
                second=0,
                microsecond=0,
            )
            events.append({"ts": ts.isoformat(), "event": "tool_result", "ok": (i % 2 == 0)})

        self._write_trace_events(events)
        ev = QualityEvaluator(memory_path=self.mem_path, trace_dir=self.log_dir)
        out = ev.trend_canary_decision(lines=5000, use_seasonality=True, seasonality_lookback_days=120)
        seas = out.get("seasonality", {})

        self.assertGreaterEqual(seas.get("sample_confidence", 0.0), 0.9)
        self.assertLess(seas.get("variance_confidence", 1.0), 0.2)
        self.assertLess(seas.get("confidence", 1.0), seas.get("sample_confidence", 0.0))

    def test_seasonality_adjustment_drift_confidence_reduces_trust(self):
        now = datetime.now()
        events = []

        # Stable historical slot with many samples.
        for i in range(15):
            ts = (now - timedelta(days=7 * (i + 1))).replace(
                hour=now.hour,
                minute=15,
                second=0,
                microsecond=0,
            )
            events.append({"ts": ts.isoformat(), "event": "tool_result", "ok": True})

        self._write_trace_events(events)
        ev = QualityEvaluator(memory_path=self.mem_path, trace_dir=self.log_dir)

        low_drift = ev._seasonality_adjustment(lookback_days=120, lines=5000, trend_drift=2.0)
        high_drift = ev._seasonality_adjustment(lookback_days=120, lines=5000, trend_drift=28.0)

        self.assertGreater(low_drift.get("drift_confidence", 0.0), high_drift.get("drift_confidence", 1.0))
        self.assertGreater(low_drift.get("confidence", 0.0), high_drift.get("confidence", 1.0))

    def test_seasonality_adjustment_weekday_cluster_similarity_affects_coef(self):
        now = datetime.now()

        def _build_events(dissimilar_neighbor: bool) -> list[dict]:
            events: list[dict] = []
            for d in range(1, 50):
                dt = now - timedelta(days=d)
                dt = dt.replace(hour=now.hour, minute=20, second=0, microsecond=0)
                wd = dt.weekday()

                if now.weekday() < 5:
                    in_cluster = wd < 5
                else:
                    in_cluster = wd >= 5

                prev_day = (now.weekday() - 1) % 7
                next_day = (now.weekday() + 1) % 7
                is_neighbor = wd in {prev_day, next_day}

                ok = in_cluster
                if dissimilar_neighbor and is_neighbor:
                    ok = False

                events.append({"ts": dt.isoformat(), "event": "tool_result", "ok": ok})
            return events

        with_sim = _build_events(dissimilar_neighbor=False)
        with_diff = _build_events(dissimilar_neighbor=True)

        self._write_trace_events(with_sim)
        ev = QualityEvaluator(memory_path=self.mem_path, trace_dir=self.log_dir)
        similar = ev._seasonality_adjustment(lookback_days=90, lines=10000, trend_drift=3.0)

        self._write_trace_events(with_diff)
        ev2 = QualityEvaluator(memory_path=self.mem_path, trace_dir=self.log_dir)
        dissimilar = ev2._seasonality_adjustment(lookback_days=90, lines=10000, trend_drift=3.0)

        self.assertGreater(similar.get("cluster_similarity", 0.0), dissimilar.get("cluster_similarity", 1.0))
        self.assertGreater(similar.get("cluster_adaptive_coef", 0.0), dissimilar.get("cluster_adaptive_coef", 1.0))

    def test_seasonality_adjustment_hour_neighbor_similarity_affects_coef(self):
        now = datetime.now()

        def _build_events(dissimilar_hours: bool) -> list[dict]:
            events: list[dict] = []
            prev_hour = (now.hour - 1) % 24
            next_hour = (now.hour + 1) % 24

            for d in range(1, 50):
                dt = now - timedelta(days=d)
                base_dt = dt.replace(minute=25, second=0, microsecond=0)

                events.append(
                    {
                        "ts": base_dt.replace(hour=now.hour).isoformat(),
                        "event": "tool_result",
                        "ok": True,
                    }
                )

                neighbor_ok = not dissimilar_hours
                events.append(
                    {
                        "ts": base_dt.replace(hour=prev_hour).isoformat(),
                        "event": "tool_result",
                        "ok": neighbor_ok,
                    }
                )
                events.append(
                    {
                        "ts": base_dt.replace(hour=next_hour).isoformat(),
                        "event": "tool_result",
                        "ok": neighbor_ok,
                    }
                )
            return events

        self._write_trace_events(_build_events(dissimilar_hours=False))
        ev = QualityEvaluator(memory_path=self.mem_path, trace_dir=self.log_dir)
        similar = ev._seasonality_adjustment(lookback_days=90, lines=20000, trend_drift=3.0)

        self._write_trace_events(_build_events(dissimilar_hours=True))
        ev2 = QualityEvaluator(memory_path=self.mem_path, trace_dir=self.log_dir)
        dissimilar = ev2._seasonality_adjustment(lookback_days=90, lines=20000, trend_drift=3.0)

        self.assertGreater(
            similar.get("hour_neighbor_similarity", 0.0),
            dissimilar.get("hour_neighbor_similarity", 1.0),
        )
        self.assertGreater(
            similar.get("hour_neighbor_adaptive_coef", 0.0),
            dissimilar.get("hour_neighbor_adaptive_coef", 1.0),
        )

    def test_seasonality_adjustment_calendar_override_applies_special_day_baseline(self):
        ref = datetime(2026, 4, 23, 10, 0, 0)
        events = []
        for year in [2024, 2025]:
            for _ in range(4):
                events.append(
                    {
                        "ts": datetime(year, 4, 23, 10, 15, 0).isoformat(),
                        "event": "tool_result",
                        "ok": False,
                    }
                )
            for _ in range(6):
                events.append(
                    {
                        "ts": datetime(year, 4, 16, 10, 15, 0).isoformat(),
                        "event": "tool_result",
                        "ok": True,
                    }
                )

        self._write_trace_events(events)
        ev = QualityEvaluator(memory_path=self.mem_path, trace_dir=self.log_dir)
        out = ev._seasonality_adjustment(
            lookback_days=800,
            lines=5000,
            trend_drift=2.0,
            reference_now=ref,
            calendar_overrides=[{"label": "cocuk_bayrami", "month_day": "04-23"}],
        )

        self.assertTrue(out.get("calendar_override_active", False))
        self.assertEqual(out.get("calendar_label"), "cocuk_bayrami")
        self.assertIsNotNone(out.get("calendar_baseline"))
        self.assertGreater(out.get("calendar_coverage", 0), 0)
        self.assertGreater(out.get("calendar_adaptive_coef", 0.0), 0.0)

    def test_seasonality_adjustment_calendar_range_match_supports_moving_holidays(self):
        ref = datetime(2026, 3, 21, 11, 0, 0)
        events = []
        for y, day in [(2025, 30), (2025, 31), (2026, 20)]:
            events.append(
                {
                    "ts": datetime(y, 3, day, 11, 5, 0).isoformat(),
                    "event": "tool_result",
                    "ok": False,
                }
            )

        self._write_trace_events(events)
        ev = QualityEvaluator(memory_path=self.mem_path, trace_dir=self.log_dir)
        out = ev._seasonality_adjustment(
            lookback_days=800,
            lines=5000,
            trend_drift=2.0,
            reference_now=ref,
            calendar_overrides=[
                {
                    "label": "ramazan_bayrami_donemi",
                    "start_date": "2026-03-20",
                    "end_date": "2026-03-22",
                    "severity": "high",
                }
            ],
        )

        self.assertTrue(out.get("calendar_override_active", False))
        self.assertEqual(out.get("calendar_label"), "ramazan_bayrami_donemi")
        self.assertEqual(out.get("calendar_severity"), "high")
        self.assertGreater(out.get("calendar_severity_weight", 0.0), 1.0)

    def test_seasonality_adjustment_calendar_severity_increases_adaptive_coef(self):
        ref = datetime(2026, 5, 27, 9, 0, 0)
        events = []
        for year in [2025, 2026]:
            for _ in range(4):
                events.append(
                    {
                        "ts": datetime(year, 5, 27, 9, 10, 0).isoformat(),
                        "event": "tool_result",
                        "ok": False,
                    }
                )

        self._write_trace_events(events)
        ev = QualityEvaluator(memory_path=self.mem_path, trace_dir=self.log_dir)
        low = ev._seasonality_adjustment(
            lookback_days=800,
            lines=5000,
            trend_drift=2.0,
            reference_now=ref,
            calendar_overrides=[{"label": "ozel_gun", "date": "2026-05-27", "severity": "low"}],
        )
        high = ev._seasonality_adjustment(
            lookback_days=800,
            lines=5000,
            trend_drift=2.0,
            reference_now=ref,
            calendar_overrides=[{"label": "ozel_gun", "date": "2026-05-27", "severity": "critical"}],
        )

        self.assertGreater(high.get("calendar_adaptive_coef", 0.0), low.get("calendar_adaptive_coef", 1.0))
        self.assertGreater(high.get("calendar_severity_weight", 0.0), low.get("calendar_severity_weight", 1.0))

    def test_calendar_sync_merges_external_source_file(self):
        target = self.base / "calendar.json"
        source = self.base / "calendar_source.json"
        target.write_text(
            json.dumps([
                {"label": "yeni_yil", "month_day": "01-01", "severity": "medium"},
            ], ensure_ascii=False),
            encoding="utf-8",
        )
        source.write_text(
            json.dumps(
                [
                    {"label": "ramazan_donemi", "start_date": "2026-03-20", "end_date": "2026-03-22", "severity": "high"},
                    {"label": "yeni_yil", "month_day": "01-01", "severity": "medium"},
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        prev = os.environ.get("AGENT_CANARY_CALENDAR_FILE")
        os.environ["AGENT_CANARY_CALENDAR_FILE"] = str(target)
        try:
            out = QualityEvaluator.sync_calendar_overrides(source=str(source), merge=True)
            self.assertTrue(out.get("ok"))
            self.assertEqual(out.get("imported"), 2)
            rows = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 2)
            labels = {row["label"] for row in rows}
            self.assertIn("yeni_yil", labels)
            self.assertIn("ramazan_donemi", labels)
        finally:
            if prev is None:
                os.environ.pop("AGENT_CANARY_CALENDAR_FILE", None)
            else:
                os.environ["AGENT_CANARY_CALENDAR_FILE"] = prev

    def test_calendar_sync_replace_mode_discards_existing_rows(self):
        target = self.base / "calendar.json"
        source = self.base / "calendar_source.json"
        target.write_text(
            json.dumps([
                {"label": "old", "month_day": "01-01", "severity": "medium"},
            ], ensure_ascii=False),
            encoding="utf-8",
        )
        source.write_text(
            json.dumps([
                {"label": "new", "date": "2026-11-10", "severity": "low"},
            ], ensure_ascii=False),
            encoding="utf-8",
        )

        prev = os.environ.get("AGENT_CANARY_CALENDAR_FILE")
        os.environ["AGENT_CANARY_CALENDAR_FILE"] = str(target)
        try:
            out = QualityEvaluator.sync_calendar_overrides(source=str(source), merge=False)
            self.assertTrue(out.get("ok"))
            rows = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["label"], "new")
        finally:
            if prev is None:
                os.environ.pop("AGENT_CANARY_CALENDAR_FILE", None)
            else:
                os.environ["AGENT_CANARY_CALENDAR_FILE"] = prev

    def test_calendar_sync_imports_ics_events(self):
        target = self.base / "calendar.json"
        source = self.base / "calendar.ics"
        source.write_text(
            "\n".join(
                [
                    "BEGIN:VCALENDAR",
                    "BEGIN:VEVENT",
                    "SUMMARY:Cumhuriyet Bayrami",
                    "DTSTART;VALUE=DATE:20261029",
                    "CATEGORIES:high",
                    "END:VEVENT",
                    "BEGIN:VEVENT",
                    "SUMMARY:Kurban Bayrami Donemi",
                    "DTSTART;VALUE=DATE:20260527",
                    "DTEND;VALUE=DATE:20260531",
                    "X-SEVERITY:critical",
                    "END:VEVENT",
                    "END:VCALENDAR",
                ]
            ),
            encoding="utf-8",
        )

        prev = os.environ.get("AGENT_CANARY_CALENDAR_FILE")
        os.environ["AGENT_CANARY_CALENDAR_FILE"] = str(target)
        try:
            out = QualityEvaluator.sync_calendar_overrides(source=str(source), merge=False)
            self.assertTrue(out.get("ok"))
            rows = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 2)

            tek_gun = next(row for row in rows if row["label"] == "cumhuriyet_bayrami")
            aralik = next(row for row in rows if row["label"] == "kurban_bayrami_donemi")

            self.assertEqual(tek_gun.get("date"), "2026-10-29")
            self.assertEqual(tek_gun.get("severity"), "high")
            self.assertEqual(aralik.get("start_date"), "2026-05-27")
            self.assertEqual(aralik.get("end_date"), "2026-05-30")
            self.assertEqual(aralik.get("severity"), "critical")
        finally:
            if prev is None:
                os.environ.pop("AGENT_CANARY_CALENDAR_FILE", None)
            else:
                os.environ["AGENT_CANARY_CALENDAR_FILE"] = prev

    def test_calendar_sync_writes_status_telemetry_file(self):
        target = self.base / "calendar.json"
        source = self.base / "calendar_source.json"
        status = self.base / "calendar_status.json"
        source.write_text(
            json.dumps([
                {"label": "new", "date": "2026-11-10", "severity": "low"},
            ], ensure_ascii=False),
            encoding="utf-8",
        )

        prev_file = os.environ.get("AGENT_CANARY_CALENDAR_FILE")
        prev_status = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE")
        os.environ["AGENT_CANARY_CALENDAR_FILE"] = str(target)
        os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = str(status)
        try:
            out = QualityEvaluator.sync_calendar_overrides(source=str(source), merge=False)
            self.assertTrue(out.get("ok"))
            meta = json.loads(status.read_text(encoding="utf-8"))
            self.assertEqual(meta.get("status"), "ok")
            self.assertEqual(meta.get("written"), 1)
            self.assertEqual(meta.get("source"), str(source))
            self.assertEqual(meta.get("source_type"), "json")
            self.assertEqual(meta.get("outcome"), "ok")
            self.assertGreater(float(meta.get("latency_ms", 0) or 0), 0)
            self.assertIn("finished_at", meta)
        finally:
            if prev_file is None:
                os.environ.pop("AGENT_CANARY_CALENDAR_FILE", None)
            else:
                os.environ["AGENT_CANARY_CALENDAR_FILE"] = prev_file
            if prev_status is None:
                os.environ.pop("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE", None)
            else:
                os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = prev_status

    def test_calendar_auto_sync_refreshes_when_status_is_stale(self):
        target = self.base / "calendar.json"
        source = self.base / "calendar_source.json"
        status = self.base / "calendar_status.json"
        source.write_text(
            json.dumps([
                {"label": "otomatik", "date": "2026-12-01", "severity": "medium"},
            ], ensure_ascii=False),
            encoding="utf-8",
        )
        status.write_text(
            json.dumps(
                {
                    "ok": True,
                    "status": "ok",
                    "finished_at": "2020-01-01T00:00:00",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        prev_file = os.environ.get("AGENT_CANARY_CALENDAR_FILE")
        prev_status = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE")
        prev_source = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_SOURCE")
        prev_auto = os.environ.get("AGENT_CANARY_CALENDAR_AUTO_SYNC")
        prev_interval = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_INTERVAL_SEC")
        prev_merge = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_MERGE")
        os.environ["AGENT_CANARY_CALENDAR_FILE"] = str(target)
        os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = str(status)
        os.environ["AGENT_CANARY_CALENDAR_SYNC_SOURCE"] = str(source)
        os.environ["AGENT_CANARY_CALENDAR_AUTO_SYNC"] = "1"
        os.environ["AGENT_CANARY_CALENDAR_SYNC_INTERVAL_SEC"] = "60"
        os.environ["AGENT_CANARY_CALENDAR_SYNC_MERGE"] = "0"
        try:
            out = QualityEvaluator.maybe_auto_sync_calendar_overrides(force=False)
            self.assertTrue(out.get("ok"))
            rows = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["label"], "otomatik")
        finally:
            for key, prev in [
                ("AGENT_CANARY_CALENDAR_FILE", prev_file),
                ("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE", prev_status),
                ("AGENT_CANARY_CALENDAR_SYNC_SOURCE", prev_source),
                ("AGENT_CANARY_CALENDAR_AUTO_SYNC", prev_auto),
                ("AGENT_CANARY_CALENDAR_SYNC_INTERVAL_SEC", prev_interval),
                ("AGENT_CANARY_CALENDAR_SYNC_MERGE", prev_merge),
            ]:
                if prev is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = prev

    def test_calendar_sync_noop_when_merged_content_unchanged(self):
        target = self.base / "calendar.json"
        source = self.base / "calendar_source.json"
        status = self.base / "calendar_status.json"
        payload = [{"label": "same", "date": "2026-10-10", "severity": "medium"}]
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        prev_file = os.environ.get("AGENT_CANARY_CALENDAR_FILE")
        prev_status = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE")
        os.environ["AGENT_CANARY_CALENDAR_FILE"] = str(target)
        os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = str(status)
        try:
            out = QualityEvaluator.sync_calendar_overrides(source=str(source), merge=True)
            self.assertTrue(out.get("ok"))
            self.assertEqual(out.get("status"), "noop")
            self.assertFalse(out.get("changed", True))
            meta = json.loads(status.read_text(encoding="utf-8"))
            self.assertEqual(meta.get("status"), "noop")
        finally:
            if prev_file is None:
                os.environ.pop("AGENT_CANARY_CALENDAR_FILE", None)
            else:
                os.environ["AGENT_CANARY_CALENDAR_FILE"] = prev_file
            if prev_status is None:
                os.environ.pop("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE", None)
            else:
                os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = prev_status

    def test_calendar_auto_sync_respects_error_cooldown(self):
        status = self.base / "calendar_status.json"
        recent_error = datetime.now().isoformat()
        status.write_text(
            json.dumps(
                {
                    "ok": False,
                    "status": "error",
                    "finished_at": recent_error,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        prev_status = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE")
        prev_source = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_SOURCE")
        prev_auto = os.environ.get("AGENT_CANARY_CALENDAR_AUTO_SYNC")
        prev_cooldown = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_ERROR_COOLDOWN_SEC")
        os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = str(status)
        os.environ["AGENT_CANARY_CALENDAR_SYNC_SOURCE"] = str(self.base / "missing.json")
        os.environ["AGENT_CANARY_CALENDAR_AUTO_SYNC"] = "1"
        os.environ["AGENT_CANARY_CALENDAR_SYNC_ERROR_COOLDOWN_SEC"] = "3600"
        try:
            out = QualityEvaluator.maybe_auto_sync_calendar_overrides(force=False)
            self.assertFalse(out.get("ok", True))
            self.assertEqual(out.get("status"), "cooldown")
            self.assertIn("retry_after", out)
        finally:
            for key, prev in [
                ("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE", prev_status),
                ("AGENT_CANARY_CALENDAR_SYNC_SOURCE", prev_source),
                ("AGENT_CANARY_CALENDAR_AUTO_SYNC", prev_auto),
                ("AGENT_CANARY_CALENDAR_SYNC_ERROR_COOLDOWN_SEC", prev_cooldown),
            ]:
                if prev is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = prev

    def test_calendar_sync_remote_304_uses_conditional_headers_and_noop(self):
        target = self.base / "calendar.json"
        status = self.base / "calendar_status.json"
        target.write_text(
            json.dumps([
                {"label": "onceki", "date": "2026-12-01", "severity": "medium"},
            ], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        status.write_text(
            json.dumps(
                {
                    "ok": True,
                    "status": "ok",
                    "source": "https://example.com/calendar.json",
                    "http_etag": "W/\"v1\"",
                    "http_last_modified": "Wed, 21 Oct 2015 07:28:00 GMT",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        captured_headers: list[dict[str, str]] = []

        def _fake_urlopen(req, timeout=10):  # noqa: ARG001
            captured_headers.append(dict(req.header_items()))
            raise HTTPError(req.full_url, 304, "Not Modified", hdrs=None, fp=None)

        prev_file = os.environ.get("AGENT_CANARY_CALENDAR_FILE")
        prev_status = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE")
        os.environ["AGENT_CANARY_CALENDAR_FILE"] = str(target)
        os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = str(status)
        try:
            with patch("quality_evaluator.urlopen", side_effect=_fake_urlopen):
                out = QualityEvaluator.sync_calendar_overrides(
                    source="https://example.com/calendar.json",
                    merge=False,
                )
            self.assertTrue(out.get("ok"))
            self.assertEqual(out.get("status"), "noop_remote_not_modified")
            self.assertEqual(out.get("http_status"), 304)
            self.assertEqual(out.get("source_type"), "http_json")
            self.assertEqual(out.get("outcome"), "noop_remote_not_modified")
            self.assertGreaterEqual(len(captured_headers), 1)
            headers = {k.lower(): v for k, v in captured_headers[0].items()}
            self.assertEqual(headers.get("if-none-match"), "W/\"v1\"")
            self.assertEqual(headers.get("if-modified-since"), "Wed, 21 Oct 2015 07:28:00 GMT")

            rows = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["label"], "onceki")
        finally:
            if prev_file is None:
                os.environ.pop("AGENT_CANARY_CALENDAR_FILE", None)
            else:
                os.environ["AGENT_CANARY_CALENDAR_FILE"] = prev_file
            if prev_status is None:
                os.environ.pop("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE", None)
            else:
                os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = prev_status

    def test_calendar_sync_error_sets_exponential_backoff(self):
        status = self.base / "calendar_status.json"
        prev_status = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE")
        prev_source = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_SOURCE")
        prev_auto = os.environ.get("AGENT_CANARY_CALENDAR_AUTO_SYNC")
        prev_backoff = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_BACKOFF_BASE_SEC")
        prev_jitter = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_BACKOFF_JITTER")
        os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = str(status)
        os.environ["AGENT_CANARY_CALENDAR_SYNC_SOURCE"] = "https://example.com/missing.json"
        os.environ["AGENT_CANARY_CALENDAR_AUTO_SYNC"] = "1"
        os.environ["AGENT_CANARY_CALENDAR_SYNC_BACKOFF_BASE_SEC"] = "120"
        os.environ["AGENT_CANARY_CALENDAR_SYNC_BACKOFF_JITTER"] = "0"
        try:
            with patch("quality_evaluator.urlopen", side_effect=RuntimeError("network_down")):
                with self.assertRaises(RuntimeError):
                    QualityEvaluator.sync_calendar_overrides(source="https://example.com/missing.json", merge=True)

            meta = json.loads(status.read_text(encoding="utf-8"))
            self.assertEqual(meta.get("status"), "error")
            self.assertEqual(meta.get("consecutive_failures"), 1)
            self.assertEqual(meta.get("backoff_sec"), 120)
            self.assertEqual(meta.get("backoff_jitter"), 0.0)
            self.assertIn("next_retry_at", meta)

            out = QualityEvaluator.maybe_auto_sync_calendar_overrides(force=False)
            self.assertEqual(out.get("status"), "cooldown")
            self.assertEqual(out.get("reason"), "backoff_active")
        finally:
            for key, prev in [
                ("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE", prev_status),
                ("AGENT_CANARY_CALENDAR_SYNC_SOURCE", prev_source),
                ("AGENT_CANARY_CALENDAR_AUTO_SYNC", prev_auto),
                ("AGENT_CANARY_CALENDAR_SYNC_BACKOFF_BASE_SEC", prev_backoff),
                ("AGENT_CANARY_CALENDAR_SYNC_BACKOFF_JITTER", prev_jitter),
            ]:
                if prev is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = prev

    def test_calendar_sync_status_keeps_bounded_history(self):
        target = self.base / "calendar.json"
        source = self.base / "calendar_source.json"
        status = self.base / "calendar_status.json"
        source.write_text(
            json.dumps([
                {"label": "x", "date": "2026-12-01", "severity": "medium"},
            ], ensure_ascii=False),
            encoding="utf-8",
        )

        prev_file = os.environ.get("AGENT_CANARY_CALENDAR_FILE")
        prev_status = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE")
        prev_hist = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_HISTORY_LIMIT")
        os.environ["AGENT_CANARY_CALENDAR_FILE"] = str(target)
        os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = str(status)
        os.environ["AGENT_CANARY_CALENDAR_SYNC_HISTORY_LIMIT"] = "2"
        try:
            QualityEvaluator.sync_calendar_overrides(source=str(source), merge=False)
            QualityEvaluator.sync_calendar_overrides(source=str(source), merge=True)
            QualityEvaluator.sync_calendar_overrides(source=str(source), merge=True)
            meta = json.loads(status.read_text(encoding="utf-8"))
            self.assertIn("history", meta)
            self.assertEqual(len(meta.get("history", [])), 2)
            derived = QualityEvaluator.get_calendar_sync_status()
            self.assertIn("history_summary", derived)
            self.assertEqual(derived["history_summary"].get("total"), 2)
            self.assertIn("status_counts", derived["history_summary"])
            self.assertIn("source_type_summary", derived["history_summary"])
            self.assertIn("p95_latency_ms", derived["history_summary"])
            self.assertIn("window_summary", derived)
            self.assertIn("trend_summary", derived)
            self.assertIn("1h", derived["window_summary"])
            self.assertIn("24h", derived["window_summary"])
            self.assertEqual(derived["window_summary"]["1h"].get("attempts"), 2)
        finally:
            for key, prev in [
                ("AGENT_CANARY_CALENDAR_FILE", prev_file),
                ("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE", prev_status),
                ("AGENT_CANARY_CALENDAR_SYNC_HISTORY_LIMIT", prev_hist),
            ]:
                if prev is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = prev

    def test_calendar_sync_status_window_error_type_distribution(self):
        status = self.base / "calendar_status.json"
        now = datetime.now()
        status.write_text(
            json.dumps(
                {
                    "ok": False,
                    "status": "error",
                    "history": [
                        {
                            "finished_at": (now - timedelta(minutes=10)).isoformat(),
                            "status": "error",
                            "source_type": "http_json",
                            "latency_ms": 180,
                            "error": "RuntimeError: timeout",
                        },
                        {
                            "finished_at": (now - timedelta(minutes=5)).isoformat(),
                            "status": "ok",
                            "source_type": "http_json",
                            "latency_ms": 120,
                            "error": None,
                        },
                        {
                            "finished_at": (now - timedelta(hours=3)).isoformat(),
                            "status": "error",
                            "source_type": "ics",
                            "latency_ms": 310,
                            "error": "ValueError: bad payload",
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        prev_status = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE")
        os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = str(status)
        try:
            out = QualityEvaluator.get_calendar_sync_status()
            w1 = out["window_summary"]["1h"]
            w24 = out["window_summary"]["24h"]

            self.assertEqual(w1.get("attempts"), 2)
            self.assertEqual(w1.get("errors"), 1)
            self.assertEqual(w1.get("error_types", {}).get("RuntimeError"), 1)
            self.assertEqual(w1.get("source_type_summary", {}).get("http_json", {}).get("attempts"), 2)
            self.assertEqual(w1.get("source_type_summary", {}).get("http_json", {}).get("success"), 1)
            self.assertEqual(w1.get("source_type_summary", {}).get("http_json", {}).get("success_rate"), 0.5)
            self.assertEqual(w1.get("p95_latency_ms"), 180.0)
            self.assertEqual(w1.get("p99_latency_ms"), 180.0)
            self.assertEqual(w24.get("attempts"), 3)
            self.assertEqual(w24.get("errors"), 2)
            self.assertEqual(w24.get("error_types", {}).get("RuntimeError"), 1)
            self.assertEqual(w24.get("error_types", {}).get("ValueError"), 1)
            self.assertEqual(w24.get("source_type_summary", {}).get("ics", {}).get("attempts"), 1)
            self.assertEqual(w24.get("p99_latency_ms"), 310.0)
            self.assertEqual(out["history_summary"].get("source_type_summary", {}).get("http_json", {}).get("attempts"), 2)
            self.assertIn("p99_latency_ms", out["history_summary"])
            trend = out.get("trend_summary", {})
            self.assertIn("score_1h", trend)
            self.assertIn("score_24h", trend)
            self.assertIn("volatility_24h", trend)
            self.assertIn("anomaly_threshold", trend)
            self.assertIn("ewma", trend)
            self.assertIn("anomaly", trend)
            self.assertIn("latency_regime", trend)
        finally:
            if prev_status is None:
                os.environ.pop("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE", None)
            else:
                os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = prev_status

    def test_calendar_sync_status_trend_anomaly_flags_short_window_drop(self):
        status = self.base / "calendar_status.json"
        now = datetime.now()
        history = []

        for m in [10, 20, 30]:
            history.append(
                {
                    "finished_at": (now - timedelta(minutes=m)).isoformat(),
                    "status": "error",
                    "source_type": "http_json",
                    "latency_ms": 210,
                    "error": "RuntimeError: timeout",
                }
            )

        for h in [2, 4, 6, 8, 10, 12]:
            history.append(
                {
                    "finished_at": (now - timedelta(hours=h)).isoformat(),
                    "status": "ok",
                    "source_type": "http_json",
                    "latency_ms": 110,
                    "error": None,
                }
            )

        status.write_text(
            json.dumps(
                {
                    "ok": False,
                    "status": "error",
                    "history": history,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        prev_status = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE")
        os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = str(status)
        try:
            out = QualityEvaluator.get_calendar_sync_status()
            trend = out.get("trend_summary", {})
            self.assertTrue(trend.get("anomaly"))
            self.assertEqual(trend.get("reason"), "short_window_degradation")
            self.assertLess(float(trend.get("delta_1h_vs_24h", 0.0) or 0.0), 0.0)
            self.assertIsNotNone(trend.get("anomaly_threshold"))
            self.assertIsNotNone(trend.get("volatility_24h"))
        finally:
            if prev_status is None:
                os.environ.pop("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE", None)
            else:
                os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = prev_status

    def test_calendar_sync_trend_threshold_adapts_to_volatility(self):
        status = self.base / "calendar_status.json"
        now = datetime.now()

        low_vol_history = [
            {
                "finished_at": (now - timedelta(minutes=10 + i)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
                "latency_ms": 120,
                "error": None,
            }
            for i in range(8)
        ]
        low_vol_history += [
            {
                "finished_at": (now - timedelta(minutes=5)).isoformat(),
                "status": "error",
                "source_type": "http_json",
                "latency_ms": 200,
                "error": "RuntimeError: timeout",
            }
        ]

        high_vol_history = [
            {
                "finished_at": (now - timedelta(minutes=10 + i)).isoformat(),
                "status": "ok" if i % 2 == 0 else "error",
                "source_type": "http_json",
                "latency_ms": 120 + (i * 10),
                "error": None if i % 2 == 0 else "RuntimeError: timeout",
            }
            for i in range(8)
        ]
        high_vol_history += [
            {
                "finished_at": (now - timedelta(minutes=5)).isoformat(),
                "status": "error",
                "source_type": "http_json",
                "latency_ms": 210,
                "error": "RuntimeError: timeout",
            }
        ]

        prev_status = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE")
        os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = str(status)
        try:
            status.write_text(json.dumps({"history": low_vol_history}, ensure_ascii=False), encoding="utf-8")
            low = QualityEvaluator.get_calendar_sync_status().get("trend_summary", {})

            status.write_text(json.dumps({"history": high_vol_history}, ensure_ascii=False), encoding="utf-8")
            high = QualityEvaluator.get_calendar_sync_status().get("trend_summary", {})

            self.assertLessEqual(float(high.get("volatility_24h", 0.0) or 0.0), 1.0)
            self.assertGreaterEqual(float(high.get("volatility_24h", 0.0) or 0.0), float(low.get("volatility_24h", 0.0) or 0.0))
            # Higher volatility should produce a more negative threshold.
            self.assertLess(
                float(high.get("anomaly_threshold", 0.0) or 0.0),
                float(low.get("anomaly_threshold", 0.0) or 0.0),
            )
        finally:
            if prev_status is None:
                os.environ.pop("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE", None)
            else:
                os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = prev_status

    def test_calendar_sync_trend_ewma_toggle_changes_score(self):
        status = self.base / "calendar_status.json"
        now = datetime.now()
        history = [
            {
                "finished_at": (now - timedelta(minutes=50)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
                "latency_ms": 100,
            },
            {
                "finished_at": (now - timedelta(minutes=40)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
                "latency_ms": 110,
            },
            {
                "finished_at": (now - timedelta(minutes=30)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
                "latency_ms": 120,
            },
            {
                "finished_at": (now - timedelta(minutes=5)).isoformat(),
                "status": "error",
                "source_type": "http_json",
                "latency_ms": 220,
                "error": "RuntimeError: timeout",
            },
            {
                "finished_at": (now - timedelta(hours=2)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
                "latency_ms": 105,
            },
            {
                "finished_at": (now - timedelta(hours=3)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
                "latency_ms": 106,
            },
        ]
        status.write_text(json.dumps({"history": history}, ensure_ascii=False), encoding="utf-8")

        prev_status = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE")
        prev_ewma = os.environ.get("AGENT_CANARY_SYNC_TREND_EWMA")
        prev_alpha = os.environ.get("AGENT_CANARY_SYNC_TREND_EWMA_ALPHA")
        os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = str(status)
        try:
            os.environ["AGENT_CANARY_SYNC_TREND_EWMA"] = "0"
            plain = QualityEvaluator.get_calendar_sync_status().get("trend_summary", {})

            os.environ["AGENT_CANARY_SYNC_TREND_EWMA"] = "1"
            os.environ["AGENT_CANARY_SYNC_TREND_EWMA_ALPHA"] = "0.8"
            ewma = QualityEvaluator.get_calendar_sync_status().get("trend_summary", {})

            self.assertFalse(plain.get("ewma", {}).get("enabled", True))
            self.assertTrue(ewma.get("ewma", {}).get("enabled", False))
            self.assertNotEqual(plain.get("score_1h"), ewma.get("score_1h"))
        finally:
            for key, prev in [
                ("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE", prev_status),
                ("AGENT_CANARY_SYNC_TREND_EWMA", prev_ewma),
                ("AGENT_CANARY_SYNC_TREND_EWMA_ALPHA", prev_alpha),
            ]:
                if prev is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = prev

    def test_calendar_sync_trend_threshold_uses_env_coefficients(self):
        status = self.base / "calendar_status.json"
        now = datetime.now()
        history = [
            {
                "finished_at": (now - timedelta(minutes=10)).isoformat(),
                "status": "error",
                "source_type": "http_json",
            },
            {
                "finished_at": (now - timedelta(minutes=20)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
            },
            {
                "finished_at": (now - timedelta(hours=2)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
            },
            {
                "finished_at": (now - timedelta(hours=3)).isoformat(),
                "status": "error",
                "source_type": "http_json",
                "error": "RuntimeError: timeout",
            },
            {
                "finished_at": (now - timedelta(hours=4)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
            },
            {
                "finished_at": (now - timedelta(hours=5)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
            },
        ]
        status.write_text(json.dumps({"history": history}, ensure_ascii=False), encoding="utf-8")

        prev_status = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE")
        prev_base = os.environ.get("AGENT_CANARY_SYNC_ANOMALY_BASE_DELTA")
        prev_coef = os.environ.get("AGENT_CANARY_SYNC_ANOMALY_VOL_COEF")
        os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = str(status)
        try:
            os.environ["AGENT_CANARY_SYNC_ANOMALY_BASE_DELTA"] = "0.25"
            os.environ["AGENT_CANARY_SYNC_ANOMALY_VOL_COEF"] = "0.35"
            dflt = QualityEvaluator.get_calendar_sync_status().get("trend_summary", {})

            os.environ["AGENT_CANARY_SYNC_ANOMALY_BASE_DELTA"] = "0.6"
            os.environ["AGENT_CANARY_SYNC_ANOMALY_VOL_COEF"] = "0.8"
            custom = QualityEvaluator.get_calendar_sync_status().get("trend_summary", {})

            self.assertLess(
                float(custom.get("anomaly_threshold", 0.0) or 0.0),
                float(dflt.get("anomaly_threshold", 0.0) or 0.0),
            )
        finally:
            for key, prev in [
                ("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE", prev_status),
                ("AGENT_CANARY_SYNC_ANOMALY_BASE_DELTA", prev_base),
                ("AGENT_CANARY_SYNC_ANOMALY_VOL_COEF", prev_coef),
            ]:
                if prev is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = prev

    def test_calendar_sync_trend_source_type_weighting_uses_env(self):
        status = self.base / "calendar_status.json"
        now = datetime.now()
        history = [
            {
                "finished_at": (now - timedelta(minutes=40)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
                "latency_ms": 100,
            },
            {
                "finished_at": (now - timedelta(minutes=10)).isoformat(),
                "status": "error",
                "source_type": "file",
                "latency_ms": 220,
                "error": "RuntimeError: timeout",
            },
            {
                "finished_at": (now - timedelta(hours=3)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
                "latency_ms": 110,
            },
        ]
        status.write_text(json.dumps({"history": history}, ensure_ascii=False), encoding="utf-8")

        prev_status = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE")
        prev_default = os.environ.get("AGENT_CANARY_SYNC_SOURCE_WEIGHT_DEFAULT")
        prev_http_json = os.environ.get("AGENT_CANARY_SYNC_SOURCE_WEIGHT_HTTP_JSON")
        prev_file = os.environ.get("AGENT_CANARY_SYNC_SOURCE_WEIGHT_FILE")
        os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = str(status)
        try:
            os.environ["AGENT_CANARY_SYNC_SOURCE_WEIGHT_DEFAULT"] = "1.0"
            os.environ["AGENT_CANARY_SYNC_SOURCE_WEIGHT_HTTP_JSON"] = "1.0"
            os.environ["AGENT_CANARY_SYNC_SOURCE_WEIGHT_FILE"] = "1.0"
            neutral = QualityEvaluator.get_calendar_sync_status().get("trend_summary", {})

            os.environ["AGENT_CANARY_SYNC_SOURCE_WEIGHT_HTTP_JSON"] = "2.0"
            os.environ["AGENT_CANARY_SYNC_SOURCE_WEIGHT_FILE"] = "0.5"
            weighted = QualityEvaluator.get_calendar_sync_status().get("trend_summary", {})

            self.assertNotEqual(neutral.get("score_1h"), weighted.get("score_1h"))
            self.assertTrue(weighted.get("source_weighting", {}).get("enabled"))
        finally:
            for key, prev in [
                ("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE", prev_status),
                ("AGENT_CANARY_SYNC_SOURCE_WEIGHT_DEFAULT", prev_default),
                ("AGENT_CANARY_SYNC_SOURCE_WEIGHT_HTTP_JSON", prev_http_json),
                ("AGENT_CANARY_SYNC_SOURCE_WEIGHT_FILE", prev_file),
            ]:
                if prev is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = prev

    def test_calendar_sync_trend_includes_intra_window_drift_metrics(self):
        status = self.base / "calendar_status.json"
        now = datetime.now()
        history = [
            {
                "finished_at": (now - timedelta(minutes=55)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
            },
            {
                "finished_at": (now - timedelta(minutes=45)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
            },
            {
                "finished_at": (now - timedelta(minutes=15)).isoformat(),
                "status": "error",
                "source_type": "http_json",
            },
            {
                "finished_at": (now - timedelta(minutes=5)).isoformat(),
                "status": "error",
                "source_type": "http_json",
            },
            {
                "finished_at": (now - timedelta(hours=2)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
            },
        ]
        status.write_text(json.dumps({"history": history}, ensure_ascii=False), encoding="utf-8")

        prev_status = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE")
        os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = str(status)
        try:
            out = QualityEvaluator.get_calendar_sync_status().get("trend_summary", {})
            d1 = out.get("drift_1h", {})
            d24 = out.get("drift_24h", {})
            self.assertIn("first_half_score", d1)
            self.assertIn("last_half_score", d1)
            self.assertIn("intra_window_delta", d1)
            self.assertIn("instability", d1)
            self.assertLess(float(d1.get("intra_window_delta", 0.0) or 0.0), 0.0)
            self.assertIn("intra_window_delta", d24)
            self.assertIn("drift_gap", out)
        finally:
            if prev_status is None:
                os.environ.pop("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE", None)
            else:
                os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = prev_status

    def test_calendar_sync_trend_source_penalty_makes_threshold_stricter(self):
        status = self.base / "calendar_status.json"
        now = datetime.now()
        history = [
            {
                "finished_at": (now - timedelta(minutes=5)).isoformat(),
                "status": "error",
                "source_type": "http_json",
            },
            {
                "finished_at": (now - timedelta(minutes=15)).isoformat(),
                "status": "error",
                "source_type": "http_json",
            },
            {
                "finished_at": (now - timedelta(hours=2)).isoformat(),
                "status": "error",
                "source_type": "http_json",
                "error": "RuntimeError: timeout",
            },
            {
                "finished_at": (now - timedelta(hours=3)).isoformat(),
                "status": "ok",
                "source_type": "file",
            },
            {
                "finished_at": (now - timedelta(hours=4)).isoformat(),
                "status": "ok",
                "source_type": "file",
            },
        ]
        status.write_text(json.dumps({"history": history}, ensure_ascii=False), encoding="utf-8")

        prev_status = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE")
        prev_source_coef = os.environ.get("AGENT_CANARY_SYNC_ANOMALY_SOURCE_COEF")
        prev_http_w = os.environ.get("AGENT_CANARY_SYNC_SOURCE_WEIGHT_HTTP_JSON")
        os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = str(status)
        try:
            os.environ["AGENT_CANARY_SYNC_SOURCE_WEIGHT_HTTP_JSON"] = "1.8"
            os.environ["AGENT_CANARY_SYNC_ANOMALY_SOURCE_COEF"] = "0.0"
            low_pen = QualityEvaluator.get_calendar_sync_status().get("trend_summary", {})

            os.environ["AGENT_CANARY_SYNC_ANOMALY_SOURCE_COEF"] = "1.0"
            high_pen = QualityEvaluator.get_calendar_sync_status().get("trend_summary", {})

            self.assertGreaterEqual(float(high_pen.get("source_adaptive_penalty", 0.0) or 0.0), 0.0)
            self.assertLess(
                float(high_pen.get("anomaly_threshold", 0.0) or 0.0),
                float(low_pen.get("anomaly_threshold", 0.0) or 0.0),
            )
        finally:
            for key, prev in [
                ("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE", prev_status),
                ("AGENT_CANARY_SYNC_ANOMALY_SOURCE_COEF", prev_source_coef),
                ("AGENT_CANARY_SYNC_SOURCE_WEIGHT_HTTP_JSON", prev_http_w),
            ]:
                if prev is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = prev

    def test_calendar_sync_trend_drift_time_series_score_present(self):
        status = self.base / "calendar_status.json"
        now = datetime.now()
        history = []
        for i in range(8):
            history.append(
                {
                    "finished_at": (now - timedelta(hours=8 - i)).isoformat(),
                    "status": "ok" if i < 4 else "error",
                    "source_type": "http_json",
                }
            )
        status.write_text(json.dumps({"history": history}, ensure_ascii=False), encoding="utf-8")

        prev_status = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE")
        prev_span = os.environ.get("AGENT_CANARY_SYNC_DRIFT_TREND_SPAN")
        os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = str(status)
        try:
            os.environ["AGENT_CANARY_SYNC_DRIFT_TREND_SPAN"] = "3"
            out = QualityEvaluator.get_calendar_sync_status().get("trend_summary", {})
            dts = out.get("drift_time_series", {})
            self.assertIn("rolling_span", dts)
            self.assertIn("series", dts)
            self.assertIn("slope", dts)
            self.assertIn("score", dts)
            self.assertGreaterEqual(int(dts.get("points", 0) or 0), 2)
            self.assertIsNotNone(dts.get("score"))
        finally:
            for key, prev in [
                ("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE", prev_status),
                ("AGENT_CANARY_SYNC_DRIFT_TREND_SPAN", prev_span),
            ]:
                if prev is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = prev

    def test_calendar_sync_trend_latency_regime_makes_threshold_stricter(self):
        status = self.base / "calendar_status.json"
        now = datetime.now()
        history = [
            {
                "finished_at": (now - timedelta(minutes=8)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
                "latency_ms": 420,
            },
            {
                "finished_at": (now - timedelta(minutes=5)).isoformat(),
                "status": "error",
                "source_type": "http_json",
                "latency_ms": 510,
                "error": "RuntimeError: timeout",
            },
            {
                "finished_at": (now - timedelta(hours=2)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
                "latency_ms": 90,
            },
            {
                "finished_at": (now - timedelta(hours=3)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
                "latency_ms": 95,
            },
            {
                "finished_at": (now - timedelta(hours=4)).isoformat(),
                "status": "error",
                "source_type": "http_json",
                "latency_ms": 110,
                "error": "RuntimeError: timeout",
            },
            {
                "finished_at": (now - timedelta(hours=5)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
                "latency_ms": 100,
            },
        ]
        status.write_text(json.dumps({"history": history}, ensure_ascii=False), encoding="utf-8")

        prev_status = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE")
        prev_latency_coef = os.environ.get("AGENT_CANARY_SYNC_ANOMALY_LATENCY_COEF")
        os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = str(status)
        try:
            os.environ["AGENT_CANARY_SYNC_ANOMALY_LATENCY_COEF"] = "0.0"
            low = QualityEvaluator.get_calendar_sync_status().get("trend_summary", {})

            os.environ["AGENT_CANARY_SYNC_ANOMALY_LATENCY_COEF"] = "1.2"
            high = QualityEvaluator.get_calendar_sync_status().get("trend_summary", {})

            self.assertGreater(float(high.get("latency_regime", {}).get("risk", 0.0) or 0.0), 0.0)
            self.assertLess(
                float(high.get("anomaly_threshold", 0.0) or 0.0),
                float(low.get("anomaly_threshold", 0.0) or 0.0),
            )
            self.assertIn("p99_ratio", high.get("latency_regime", {}))
        finally:
            for key, prev in [
                ("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE", prev_status),
                ("AGENT_CANARY_SYNC_ANOMALY_LATENCY_COEF", prev_latency_coef),
            ]:
                if prev is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = prev

    def test_calendar_sync_trend_source_autotune_changes_weighted_score(self):
        status = self.base / "calendar_status.json"
        now = datetime.now()
        history = [
            {
                "finished_at": (now - timedelta(minutes=8)).isoformat(),
                "status": "error",
                "source_type": "http_json",
                "latency_ms": 200,
            },
            {
                "finished_at": (now - timedelta(minutes=5)).isoformat(),
                "status": "error",
                "source_type": "http_json",
                "latency_ms": 210,
            },
            {
                "finished_at": (now - timedelta(hours=2)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
                "latency_ms": 100,
            },
            {
                "finished_at": (now - timedelta(hours=3)).isoformat(),
                "status": "ok",
                "source_type": "file",
                "latency_ms": 90,
            },
            {
                "finished_at": (now - timedelta(hours=4)).isoformat(),
                "status": "ok",
                "source_type": "file",
                "latency_ms": 95,
            },
            {
                "finished_at": (now - timedelta(hours=5)).isoformat(),
                "status": "ok",
                "source_type": "file",
                "latency_ms": 98,
            },
        ]
        status.write_text(json.dumps({"history": history}, ensure_ascii=False), encoding="utf-8")

        prev_status = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE")
        prev_autotune = os.environ.get("AGENT_CANARY_SYNC_SOURCE_AUTOTUNE")
        prev_strength = os.environ.get("AGENT_CANARY_SYNC_SOURCE_AUTOTUNE_STRENGTH")
        os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = str(status)
        try:
            os.environ["AGENT_CANARY_SYNC_SOURCE_AUTOTUNE"] = "0"
            plain = QualityEvaluator.get_calendar_sync_status().get("trend_summary", {})

            os.environ["AGENT_CANARY_SYNC_SOURCE_AUTOTUNE"] = "1"
            os.environ["AGENT_CANARY_SYNC_SOURCE_AUTOTUNE_STRENGTH"] = "1.0"
            tuned = QualityEvaluator.get_calendar_sync_status().get("trend_summary", {})

            self.assertNotEqual(plain.get("score_1h"), tuned.get("score_1h"))
            self.assertTrue(tuned.get("source_weighting", {}).get("autotune_enabled"))
            self.assertIn("http_json", tuned.get("source_weighting", {}).get("learned", {}))
        finally:
            for key, prev in [
                ("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE", prev_status),
                ("AGENT_CANARY_SYNC_SOURCE_AUTOTUNE", prev_autotune),
                ("AGENT_CANARY_SYNC_SOURCE_AUTOTUNE_STRENGTH", prev_strength),
            ]:
                if prev is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = prev

    def test_calendar_sync_trend_risk_confidence_fields_present(self):
        status = self.base / "calendar_status.json"
        now = datetime.now()
        history = [
            {
                "finished_at": (now - timedelta(minutes=9)).isoformat(),
                "status": "error",
                "source_type": "http_json",
                "latency_ms": 300,
            },
            {
                "finished_at": (now - timedelta(minutes=5)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
                "latency_ms": 280,
            },
            {
                "finished_at": (now - timedelta(hours=2)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
                "latency_ms": 100,
            },
            {
                "finished_at": (now - timedelta(hours=3)).isoformat(),
                "status": "error",
                "source_type": "http_json",
                "latency_ms": 120,
                "error": "RuntimeError: timeout",
            },
            {
                "finished_at": (now - timedelta(hours=4)).isoformat(),
                "status": "ok",
                "source_type": "file",
                "latency_ms": 95,
            },
            {
                "finished_at": (now - timedelta(hours=5)).isoformat(),
                "status": "ok",
                "source_type": "file",
                "latency_ms": 98,
            },
        ]
        status.write_text(json.dumps({"history": history}, ensure_ascii=False), encoding="utf-8")

        prev_status = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE")
        os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = str(status)
        try:
            trend = QualityEvaluator.get_calendar_sync_status().get("trend_summary", {})
            rc = trend.get("risk_confidence", {})
            self.assertIn("score", rc)
            self.assertIn("level", rc)
            self.assertIn("components", rc)
            self.assertIn(rc.get("level"), {"low", "medium", "high"})
            self.assertGreaterEqual(float(rc.get("score", 0.0) or 0.0), 0.0)
            self.assertLessEqual(float(rc.get("score", 1.0) or 1.0), 1.0)
        finally:
            if prev_status is None:
                os.environ.pop("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE", None)
            else:
                os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = prev_status

    def test_calendar_sync_trend_source_autotune_respects_recency(self):
        status = self.base / "calendar_status.json"
        now = datetime.now()
        history = [
            {
                "finished_at": (now - timedelta(minutes=10)).isoformat(),
                "status": "error",
                "source_type": "http_json",
                "latency_ms": 220,
            },
            {
                "finished_at": (now - timedelta(minutes=20)).isoformat(),
                "status": "error",
                "source_type": "http_json",
                "latency_ms": 210,
            },
            {
                "finished_at": (now - timedelta(hours=20)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
                "latency_ms": 90,
            },
            {
                "finished_at": (now - timedelta(hours=22)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
                "latency_ms": 85,
            },
            {
                "finished_at": (now - timedelta(hours=3)).isoformat(),
                "status": "ok",
                "source_type": "file",
                "latency_ms": 95,
            },
            {
                "finished_at": (now - timedelta(hours=4)).isoformat(),
                "status": "ok",
                "source_type": "file",
                "latency_ms": 100,
            },
        ]
        status.write_text(json.dumps({"history": history}, ensure_ascii=False), encoding="utf-8")

        prev_status = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE")
        prev_autotune = os.environ.get("AGENT_CANARY_SYNC_SOURCE_AUTOTUNE")
        prev_strength = os.environ.get("AGENT_CANARY_SYNC_SOURCE_AUTOTUNE_STRENGTH")
        prev_half = os.environ.get("AGENT_CANARY_SYNC_SOURCE_AUTOTUNE_HALF_LIFE_HOURS")
        os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = str(status)
        try:
            os.environ["AGENT_CANARY_SYNC_SOURCE_AUTOTUNE"] = "1"
            os.environ["AGENT_CANARY_SYNC_SOURCE_AUTOTUNE_STRENGTH"] = "1.0"
            os.environ["AGENT_CANARY_SYNC_SOURCE_AUTOTUNE_HALF_LIFE_HOURS"] = "2"
            out = QualityEvaluator.get_calendar_sync_status().get("trend_summary", {})
            learned = out.get("source_weighting", {}).get("learned", {})
            self.assertIn("http_json", learned)
            self.assertGreater(float(learned.get("http_json", 1.0) or 1.0), 1.0)
        finally:
            for key, prev in [
                ("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE", prev_status),
                ("AGENT_CANARY_SYNC_SOURCE_AUTOTUNE", prev_autotune),
                ("AGENT_CANARY_SYNC_SOURCE_AUTOTUNE_STRENGTH", prev_strength),
                ("AGENT_CANARY_SYNC_SOURCE_AUTOTUNE_HALF_LIFE_HOURS", prev_half),
            ]:
                if prev is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = prev

    def test_calendar_risk_components_timeseries_returns_points(self):
        status = self.base / "calendar_status.json"
        now = datetime.now()
        history = [
            {
                "finished_at": (now - timedelta(minutes=50)).isoformat(),
                "status": "ok",
                "source_type": "http_json",
                "latency_ms": 110,
            },
            {
                "finished_at": (now - timedelta(minutes=20)).isoformat(),
                "status": "error",
                "source_type": "http_json",
                "latency_ms": 260,
            },
            {
                "finished_at": (now - timedelta(hours=2)).isoformat(),
                "status": "ok",
                "source_type": "file",
                "latency_ms": 90,
            },
        ]
        status.write_text(json.dumps({"history": history}, ensure_ascii=False), encoding="utf-8")
        prev_status = os.environ.get("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE")
        os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = str(status)
        try:
            out = QualityEvaluator.get_calendar_risk_components_timeseries(
                lookback_hours=24,
                bucket_minutes=60,
                max_points=12,
            )
            self.assertIn("points", out)
            self.assertGreaterEqual(len(out.get("points", [])), 1)
            first = out["points"][0]
            self.assertIn("risk_score", first)
            self.assertIn("components", first)
            self.assertIn("latency", first.get("components", {}))
        finally:
            if prev_status is None:
                os.environ.pop("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE", None)
            else:
                os.environ["AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE"] = prev_status

    def test_build_trend_explainability_tree_contains_expected_nodes(self):
        decision_payload = {
            "decision": "hold",
            "reason": ["sync_risk_medium", "risk=0.6"],
            "sync_risk": {"status": "ok", "score": 0.6, "level": "medium"},
        }
        sync_status = {
            "trend_summary": {
                "risk_confidence": {
                    "score": 0.58,
                    "level": "medium",
                    "components": {
                        "volatility": 0.4,
                        "drift": 0.5,
                        "latency": 0.7,
                        "source": 0.2,
                    },
                }
            }
        }

        tree = QualityEvaluator.build_trend_explainability_tree(decision_payload, sync_status)
        self.assertEqual(tree.get("root"), "decision")
        self.assertGreaterEqual(len(tree.get("nodes", [])), 3)
        self.assertGreaterEqual(len(tree.get("leaves", [])), 4)


if __name__ == "__main__":
    unittest.main()
