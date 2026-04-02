"""Quality evaluator for autonomous loop health.

Combines trace and memory signals into a compact summary
that can be used for canary/promotion decisions.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from memory_store import MemoryStore


class QualityEvaluator:
    def __init__(self, memory_path: Path | str | None = None, trace_dir: Path | str = "log") -> None:
        self.memory = MemoryStore(memory_path)
        self.trace_dir = Path(trace_dir)

    def _trace_file(self) -> Path:
        return self.trace_dir / f"agent_trace_{datetime.now().strftime('%Y%m%d')}.jsonl"

    def _load_trace_events(self, lines: int = 1000) -> list[dict[str, Any]]:
        path = self._trace_file()
        if not path.exists():
            return []

        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rows.append(json.loads(raw))
                except Exception:
                    continue

        if lines > 0:
            return rows[-lines:]
        return rows

    @staticmethod
    def _safe_iso_to_ts(value: Any) -> float | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value)).timestamp()
        except Exception:
            return None

    @staticmethod
    def _safe_iso_to_dt(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except Exception:
            return None

    @staticmethod
    def _calendar_file_path() -> Path:
        return Path(os.getenv("AGENT_CANARY_CALENDAR_FILE", "calendar_overrides.json"))

    @staticmethod
    def _calendar_sync_status_path() -> Path:
        return Path(os.getenv("AGENT_CANARY_CALENDAR_SYNC_STATUS_FILE", "calendar_sync_status.json"))

    @staticmethod
    def _trend_explainability_snapshot_path() -> Path:
        return Path(
            os.getenv(
                "AGENT_CANARY_TREND_EXPLAINABILITY_SNAPSHOT_FILE",
                "trend_explainability_snapshots.jsonl",
            )
        )

    @staticmethod
    def _infer_calendar_source_type(source: str) -> str:
        src = str(source or "").strip().lower()
        if not src:
            return "unknown"
        if src.startswith("http://") or src.startswith("https://"):
            if src.endswith(".ics"):
                return "http_ics"
            if src.endswith(".json"):
                return "http_json"
            return "http"
        if src.endswith(".ics"):
            return "ics"
        if src.endswith(".json"):
            return "json"
        return "file"

    @staticmethod
    def _is_success_sync_status(status: str) -> bool:
        s = str(status or "").strip().lower()
        return s in {"ok", "noop", "noop_remote_not_modified", "fresh"}

    @staticmethod
    def _error_type_from_message(message: Any) -> str:
        text = str(message or "").strip()
        if not text:
            return "unknown"
        if ":" in text:
            head = text.split(":", 1)[0].strip()
            return head or "unknown"
        return "unknown"

    @staticmethod
    def _outcome_weight(status: str) -> float:
        s = str(status or "").strip().lower()
        mapping = {
            "ok": 1.0,
            "noop": 0.6,
            "noop_remote_not_modified": 0.7,
            "fresh": 0.2,
            "cooldown": -0.4,
            "error": -1.0,
            "skipped": 0.0,
        }
        return float(mapping.get(s, 0.0))

    @staticmethod
    def _source_type_weight(source_type: str) -> float:
        env_key = f"AGENT_CANARY_SYNC_SOURCE_WEIGHT_{str(source_type or '').strip().upper()}"
        raw = os.getenv(env_key)
        if raw is None:
            raw = os.getenv("AGENT_CANARY_SYNC_SOURCE_WEIGHT_DEFAULT", "1.0")
        try:
            w = float(raw)
        except Exception:
            w = 1.0
        return max(0.1, min(2.0, w))

    @staticmethod
    def _percentile(values: list[float], p: float) -> float | None:
        if not values:
            return None
        if len(values) == 1:
            return round(float(values[0]), 2)
        p = max(0.0, min(100.0, float(p)))
        ordered = sorted(float(v) for v in values)
        idx = int(round((p / 100.0) * (len(ordered) - 1)))
        return round(float(ordered[idx]), 2)

    @classmethod
    def _load_calendar_overrides(cls) -> list[dict[str, Any]]:
        cls.maybe_auto_sync_calendar_overrides()
        return cls._read_calendar_overrides_file()

    @classmethod
    def _read_calendar_overrides_file(cls) -> list[dict[str, Any]]:
        path = cls._calendar_file_path()
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        return []

    @classmethod
    def get_calendar_sync_status(cls) -> dict[str, Any]:
        path = cls._calendar_sync_status_path()
        if not path.exists():
            return {
                "ok": False,
                "status": "never_synced",
                "path": str(path),
            }
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {
                "ok": False,
                "status": "invalid_status_file",
                "path": str(path),
                "error": f"{exc.__class__.__name__}: {exc}",
            }
        if not isinstance(data, dict):
            return {
                "ok": False,
                "status": "invalid_status_payload",
                "path": str(path),
            }
        history = data.get("history", []) if isinstance(data.get("history"), list) else []
        if history:
            counts: dict[str, int] = {}
            latencies: list[float] = []
            source_type_map: dict[str, dict[str, Any]] = {}
            for h in history:
                if not isinstance(h, dict):
                    continue
                st = str(h.get("status", "unknown")).strip() or "unknown"
                counts[st] = counts.get(st, 0) + 1
                try:
                    lat = float(h.get("latency_ms", 0) or 0)
                    if lat > 0:
                        latencies.append(lat)
                except Exception:
                    pass

                source_type = str(h.get("source_type", "unknown")).strip() or "unknown"
                rec = source_type_map.get(source_type) or {
                    "attempts": 0,
                    "success": 0,
                    "latencies": [],
                }
                rec["attempts"] += 1
                if cls._is_success_sync_status(h.get("status", "")):
                    rec["success"] += 1
                try:
                    lat = float(h.get("latency_ms", 0) or 0)
                    if lat > 0:
                        rec["latencies"].append(lat)
                except Exception:
                    pass
                source_type_map[source_type] = rec

            source_type_summary: dict[str, Any] = {}
            for s_type, rec in source_type_map.items():
                attempts = int(rec.get("attempts", 0) or 0)
                success = int(rec.get("success", 0) or 0)
                lats = rec.get("latencies", []) if isinstance(rec.get("latencies"), list) else []
                source_type_summary[s_type] = {
                    "attempts": attempts,
                    "success": success,
                    "success_rate": round(success / attempts, 4) if attempts > 0 else None,
                    "p95_latency_ms": cls._percentile(lats, 95.0),
                    "p99_latency_ms": cls._percentile(lats, 99.0),
                    "avg_latency_ms": round(sum(lats) / len(lats), 2) if lats else None,
                }

            data["history_summary"] = {
                "total": len(history),
                "status_counts": counts,
                "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
                "max_latency_ms": round(max(latencies), 2) if latencies else None,
                "min_latency_ms": round(min(latencies), 2) if latencies else None,
                "p95_latency_ms": cls._percentile(latencies, 95.0),
                "p99_latency_ms": cls._percentile(latencies, 99.0),
                "source_type_summary": source_type_summary,
            }

            now = datetime.now()
            windows: dict[str, Any] = {}
            for hours, key in [(1, "1h"), (24, "24h")]:
                cutoff = now - timedelta(hours=hours)
                w_entries = []
                for h in history:
                    if not isinstance(h, dict):
                        continue
                    ts = cls._safe_iso_to_dt(h.get("finished_at"))
                    if ts is None:
                        continue
                    if ts >= cutoff:
                        w_entries.append(h)

                attempts = len(w_entries)
                success = sum(1 for h in w_entries if cls._is_success_sync_status(h.get("status", "")))
                errors = [h for h in w_entries if str(h.get("status", "")).strip().lower() == "error"]
                error_types: dict[str, int] = {}
                source_type_map_w: dict[str, dict[str, Any]] = {}
                window_latencies: list[float] = []
                for h in errors:
                    et = cls._error_type_from_message(h.get("error"))
                    error_types[et] = error_types.get(et, 0) + 1

                for h in w_entries:
                    s_type = str(h.get("source_type", "unknown")).strip() or "unknown"
                    rec = source_type_map_w.get(s_type) or {"attempts": 0, "success": 0, "latencies": []}
                    rec["attempts"] += 1
                    if cls._is_success_sync_status(h.get("status", "")):
                        rec["success"] += 1
                    try:
                        lat = float(h.get("latency_ms", 0) or 0)
                        if lat > 0:
                            rec["latencies"].append(lat)
                            window_latencies.append(lat)
                    except Exception:
                        pass
                    source_type_map_w[s_type] = rec

                source_type_summary_w: dict[str, Any] = {}
                for s_type, rec in source_type_map_w.items():
                    att = int(rec.get("attempts", 0) or 0)
                    suc = int(rec.get("success", 0) or 0)
                    lats = rec.get("latencies", []) if isinstance(rec.get("latencies"), list) else []
                    source_type_summary_w[s_type] = {
                        "attempts": att,
                        "success": suc,
                        "success_rate": round(suc / att, 4) if att > 0 else None,
                        "p95_latency_ms": cls._percentile(lats, 95.0),
                        "p99_latency_ms": cls._percentile(lats, 99.0),
                    }

                windows[key] = {
                    "attempts": attempts,
                    "success": success,
                    "success_rate": round(success / attempts, 4) if attempts > 0 else None,
                    "errors": len(errors),
                    "error_types": error_types,
                    "p95_latency_ms": cls._percentile(window_latencies, 95.0),
                    "p99_latency_ms": cls._percentile(window_latencies, 99.0),
                    "source_type_summary": source_type_summary_w,
                }

            data["window_summary"] = windows
            hist_source_summary = data.get("history_summary", {}).get("source_type_summary", {})

            autotune_enabled = os.getenv("AGENT_CANARY_SYNC_SOURCE_AUTOTUNE", "1").strip() == "1"
            try:
                autotune_strength = float(os.getenv("AGENT_CANARY_SYNC_SOURCE_AUTOTUNE_STRENGTH", "0.4"))
            except Exception:
                autotune_strength = 0.4
            autotune_strength = max(0.0, min(1.0, autotune_strength))

            learned_source_weights: dict[str, float] = {}
            if autotune_enabled and isinstance(hist_source_summary, dict):
                try:
                    recency_half_life_h = float(os.getenv("AGENT_CANARY_SYNC_SOURCE_AUTOTUNE_HALF_LIFE_HOURS", "12"))
                except Exception:
                    recency_half_life_h = 12.0
                recency_half_life_h = max(1.0, min(168.0, recency_half_life_h))
                now_ts = now.timestamp()

                recency_stats: dict[str, dict[str, float]] = {}
                for h in history:
                    if not isinstance(h, dict):
                        continue
                    s_type = str(h.get("source_type", "unknown")).strip() or "unknown"
                    ts = cls._safe_iso_to_ts(h.get("finished_at"))
                    if ts is None:
                        age_h = recency_half_life_h
                    else:
                        age_h = max(0.0, (now_ts - ts) / 3600.0)
                    recency_w = 0.5 ** (age_h / recency_half_life_h)
                    success = 1.0 if cls._is_success_sync_status(h.get("status", "")) else 0.0
                    rec = recency_stats.get(s_type) or {"w": 0.0, "ws": 0.0}
                    rec["w"] += recency_w
                    rec["ws"] += recency_w * success
                    recency_stats[s_type] = rec

                for s_type, rec in hist_source_summary.items():
                    if not isinstance(rec, dict):
                        continue
                    attempts = int(rec.get("attempts", 0) or 0)
                    sr_raw = rec.get("success_rate")
                    try:
                        sr = float(sr_raw) if sr_raw is not None else 0.5
                    except Exception:
                        sr = 0.5
                    sr = max(0.0, min(1.0, sr))

                    rec_stat = recency_stats.get(str(s_type), {})
                    rec_w = float(rec_stat.get("w", 0.0) or 0.0)
                    if rec_w > 0:
                        rec_sr = max(0.0, min(1.0, float(rec_stat.get("ws", 0.0) / rec_w)))
                        rec_conf = max(0.0, min(1.0, rec_w / 6.0))
                        sr = ((1.0 - rec_conf) * sr) + (rec_conf * rec_sr)

                    confidence = max(0.0, min(1.0, attempts / 8.0))
                    # Low success_rate increases weight (more risk-sensitive), high success_rate decreases it.
                    risk_bias = (0.6 - sr) * 2.0
                    multiplier = 1.0 + (autotune_strength * confidence * risk_bias)
                    learned_source_weights[str(s_type)] = round(max(0.5, min(1.8, multiplier)), 4)

            def _effective_source_weight(source_type: str) -> float:
                base = cls._source_type_weight(source_type)
                tuned = learned_source_weights.get(str(source_type), 1.0)
                return max(0.1, min(2.0, base * tuned))

            short = windows.get("1h", {})
            longw = windows.get("24h", {})
            short_entries = [
                h
                for h in history
                if isinstance(h, dict)
                and (cls._safe_iso_to_dt(h.get("finished_at")) or datetime.min) >= (now - timedelta(hours=1))
            ]
            long_entries = [
                h
                for h in history
                if isinstance(h, dict)
                and (cls._safe_iso_to_dt(h.get("finished_at")) or datetime.min) >= (now - timedelta(hours=24))
            ]

            ewma_enabled = os.getenv("AGENT_CANARY_SYNC_TREND_EWMA", "0").strip() == "1"
            try:
                ewma_alpha = float(os.getenv("AGENT_CANARY_SYNC_TREND_EWMA_ALPHA", "0.35"))
            except Exception:
                ewma_alpha = 0.35
            ewma_alpha = max(0.05, min(0.95, ewma_alpha))

            def _score(entries: list[dict[str, Any]], use_ewma: bool, alpha: float) -> float | None:
                if not entries:
                    return None
                ordered = sorted(
                    entries,
                    key=lambda e: cls._safe_iso_to_dt(e.get("finished_at")) or datetime.min,
                )
                values = [
                    cls._outcome_weight(str(e.get("status", "")))
                    * _effective_source_weight(str(e.get("source_type", "unknown")) )
                    for e in ordered
                ]
                if not use_ewma:
                    return round(sum(values) / len(values), 4)

                ewma = values[0]
                for v in values[1:]:
                    ewma = (alpha * v) + ((1.0 - alpha) * ewma)
                return round(float(ewma), 4)

            def _volatility(entries: list[dict[str, Any]]) -> float | None:
                if not entries:
                    return None
                weights = [
                    cls._outcome_weight(str(e.get("status", "")))
                    * _effective_source_weight(str(e.get("source_type", "unknown")) )
                    for e in entries
                ]
                if len(weights) <= 1:
                    return 0.0
                mean = sum(weights) / len(weights)
                var = sum((w - mean) ** 2 for w in weights) / len(weights)
                return round(max(0.0, min(1.0, var ** 0.5)), 4)

            def _window_drift(entries: list[dict[str, Any]]) -> dict[str, Any]:
                if not entries:
                    return {
                        "attempts": 0,
                        "first_half_score": None,
                        "last_half_score": None,
                        "intra_window_delta": None,
                        "instability": None,
                    }
                ordered = sorted(
                    entries,
                    key=lambda e: cls._safe_iso_to_dt(e.get("finished_at")) or datetime.min,
                )
                vals = [
                    cls._outcome_weight(str(e.get("status", "")))
                    * _effective_source_weight(str(e.get("source_type", "unknown")) )
                    for e in ordered
                ]
                n = len(vals)
                if n == 1:
                    return {
                        "attempts": 1,
                        "first_half_score": round(float(vals[0]), 4),
                        "last_half_score": round(float(vals[0]), 4),
                        "intra_window_delta": 0.0,
                        "instability": 0.0,
                    }
                split = max(1, n // 2)
                first_vals = vals[:split]
                last_vals = vals[split:]
                if not last_vals:
                    last_vals = vals[-1:]
                first_avg = sum(first_vals) / len(first_vals)
                last_avg = sum(last_vals) / len(last_vals)
                mean = sum(vals) / len(vals)
                var = sum((v - mean) ** 2 for v in vals) / len(vals)
                return {
                    "attempts": n,
                    "first_half_score": round(float(first_avg), 4),
                    "last_half_score": round(float(last_avg), 4),
                    "intra_window_delta": round(float(last_avg - first_avg), 4),
                    "instability": round(float(var ** 0.5), 4),
                }

            def _source_adaptive_penalty(
                short_window: dict[str, Any],
                hist_source_summary: dict[str, Any],
            ) -> float:
                try:
                    source_coef = float(os.getenv("AGENT_CANARY_SYNC_ANOMALY_SOURCE_COEF", "0.35"))
                except Exception:
                    source_coef = 0.35
                source_coef = max(0.0, min(2.0, source_coef))
                st_map = short_window.get("source_type_summary", {}) if isinstance(short_window, dict) else {}
                if not isinstance(st_map, dict) or not st_map:
                    return 0.0
                total_attempts = sum(int((v or {}).get("attempts", 0) or 0) for v in st_map.values())
                if total_attempts <= 0:
                    return 0.0

                raw_penalty = 0.0
                for s_type, rec in st_map.items():
                    attempts = int((rec or {}).get("attempts", 0) or 0)
                    if attempts <= 0:
                        continue
                    share = attempts / total_attempts
                    hist_rec = hist_source_summary.get(s_type, {}) if isinstance(hist_source_summary, dict) else {}
                    try:
                        hist_sr = float(hist_rec.get("success_rate")) if hist_rec.get("success_rate") is not None else 0.5
                    except Exception:
                        hist_sr = 0.5
                    hist_sr = max(0.0, min(1.0, hist_sr))
                    risk = max(0.0, 0.7 - hist_sr)
                    src_w = cls._source_type_weight(str(s_type))
                    src_w = _effective_source_weight(str(s_type))
                    raw_penalty += share * risk * src_w

                return round(max(0.0, min(1.0, source_coef * raw_penalty)), 4)

            def _drift_time_series(entries: list[dict[str, Any]]) -> dict[str, Any]:
                if not entries:
                    return {
                        "points": 0,
                        "rolling_span": None,
                        "series": [],
                        "slope": None,
                        "score": None,
                    }
                ordered = sorted(
                    entries,
                    key=lambda e: cls._safe_iso_to_dt(e.get("finished_at")) or datetime.min,
                )
                vals = [
                    cls._outcome_weight(str(e.get("status", "")))
                    * _effective_source_weight(str(e.get("source_type", "unknown")) )
                    for e in ordered
                ]
                try:
                    span = int(os.getenv("AGENT_CANARY_SYNC_DRIFT_TREND_SPAN", "4"))
                except Exception:
                    span = 4
                span = max(2, min(12, span))
                if len(vals) < span:
                    span = max(2, len(vals))

                series: list[float] = []
                if len(vals) < 2:
                    series = [round(float(vals[0]), 4)]
                else:
                    for i in range(span, len(vals) + 1):
                        chunk = vals[i - span : i]
                        series.append(round(sum(chunk) / len(chunk), 4))
                    if not series:
                        series = [round(sum(vals) / len(vals), 4)]

                slope = None
                score = None
                if len(series) >= 2:
                    slope = (series[-1] - series[0]) / float(len(series) - 1)
                    # Positive score means degradation trend (falling weighted outcomes).
                    score = max(-1.0, min(1.0, -float(slope)))

                return {
                    "points": len(series),
                    "rolling_span": span,
                    "series": series[-8:],
                    "slope": round(float(slope), 4) if slope is not None else None,
                    "score": round(float(score), 4) if score is not None else None,
                }

            def _latency_regime_risk(short_e: list[dict[str, Any]], long_e: list[dict[str, Any]]) -> dict[str, Any]:
                def _to_float(value: Any) -> float | None:
                    try:
                        if value is None:
                            return None
                        v = float(value)
                        return v if v > 0 else None
                    except Exception:
                        return None

                short_lats: list[float] = []
                baseline_lats: list[float] = []
                short_cutoff = now - timedelta(hours=1)

                for e in short_e:
                    try:
                        lat = float(e.get("latency_ms", 0) or 0)
                        if lat > 0:
                            short_lats.append(lat)
                    except Exception:
                        pass

                for e in long_e:
                    ts = cls._safe_iso_to_dt(e.get("finished_at"))
                    if ts is None or ts >= short_cutoff:
                        continue
                    try:
                        lat = float(e.get("latency_ms", 0) or 0)
                        if lat > 0:
                            baseline_lats.append(lat)
                    except Exception:
                        pass

                if not baseline_lats:
                    baseline_lats = short_lats[:]

                s95 = _to_float(cls._percentile(short_lats, 95.0))
                l95 = _to_float(cls._percentile(baseline_lats, 95.0))
                s99 = _to_float(cls._percentile(short_lats, 99.0))
                l99 = _to_float(cls._percentile(baseline_lats, 99.0))

                try:
                    p95_ratio_trigger = float(os.getenv("AGENT_CANARY_SYNC_LATENCY_P95_RATIO_TRIGGER", "1.25"))
                except Exception:
                    p95_ratio_trigger = 1.25
                try:
                    p99_ratio_trigger = float(os.getenv("AGENT_CANARY_SYNC_LATENCY_P99_RATIO_TRIGGER", "1.35"))
                except Exception:
                    p99_ratio_trigger = 1.35
                try:
                    abs_trigger_ms = float(os.getenv("AGENT_CANARY_SYNC_LATENCY_ABS_TRIGGER_MS", "60"))
                except Exception:
                    abs_trigger_ms = 60.0

                p95_ratio_trigger = max(1.0, min(5.0, p95_ratio_trigger))
                p99_ratio_trigger = max(1.0, min(5.0, p99_ratio_trigger))
                abs_trigger_ms = max(10.0, min(2000.0, abs_trigger_ms))

                p95_ratio = round(s95 / l95, 4) if s95 is not None and l95 is not None and l95 > 0 else None
                p99_ratio = round(s99 / l99, 4) if s99 is not None and l99 is not None and l99 > 0 else None
                p95_abs_delta = round(s95 - l95, 2) if s95 is not None and l95 is not None else None
                p99_abs_delta = round(s99 - l99, 2) if s99 is not None and l99 is not None else None

                components: list[float] = []
                if p95_ratio is not None:
                    components.append(max(0.0, min(1.0, (p95_ratio - p95_ratio_trigger) / p95_ratio_trigger)))
                if p99_ratio is not None:
                    components.append(max(0.0, min(1.0, (p99_ratio - p99_ratio_trigger) / p99_ratio_trigger)))
                if p95_abs_delta is not None:
                    components.append(max(0.0, min(1.0, p95_abs_delta / abs_trigger_ms)))
                if p99_abs_delta is not None:
                    components.append(max(0.0, min(1.0, p99_abs_delta / abs_trigger_ms)))

                risk = round(sum(components) / len(components), 4) if components else 0.0
                return {
                    "p95_short": s95,
                    "p95_long": l95,
                    "p95_ratio": p95_ratio,
                    "p99_short": s99,
                    "p99_long": l99,
                    "p99_ratio": p99_ratio,
                    "p95_abs_delta_ms": p95_abs_delta,
                    "p99_abs_delta_ms": p99_abs_delta,
                    "risk": risk,
                }

            short_score = _score(short_entries, ewma_enabled, ewma_alpha)
            long_score = _score(long_entries, ewma_enabled, ewma_alpha)
            long_volatility = _volatility(long_entries)
            short_drift = _window_drift(short_entries)
            long_drift = _window_drift(long_entries)
            source_adaptive_penalty = _source_adaptive_penalty(short, hist_source_summary)
            drift_ts = _drift_time_series(long_entries)
            latency_regime = _latency_regime_risk(short_entries, long_entries)
            short_attempts = int(short.get("attempts", 0) or 0)
            long_attempts = int(longw.get("attempts", 0) or 0)
            delta = None
            anomaly = False
            reason = "insufficient_data"
            anomaly_threshold = None
            if short_score is not None and long_score is not None:
                delta = round(short_score - long_score, 4)
                try:
                    min_short_attempts = int(os.getenv("AGENT_CANARY_SYNC_ANOMALY_MIN_SHORT_ATTEMPTS", "2"))
                except Exception:
                    min_short_attempts = 2
                try:
                    min_long_attempts = int(os.getenv("AGENT_CANARY_SYNC_ANOMALY_MIN_LONG_ATTEMPTS", "4"))
                except Exception:
                    min_long_attempts = 4
                try:
                    base_delta = float(os.getenv("AGENT_CANARY_SYNC_ANOMALY_BASE_DELTA", "0.25"))
                except Exception:
                    base_delta = 0.25
                try:
                    vol_coef = float(os.getenv("AGENT_CANARY_SYNC_ANOMALY_VOL_COEF", "0.35"))
                except Exception:
                    vol_coef = 0.35
                try:
                    drift_coef = float(os.getenv("AGENT_CANARY_SYNC_ANOMALY_DRIFT_COEF", "0.25"))
                except Exception:
                    drift_coef = 0.25
                try:
                    latency_coef = float(os.getenv("AGENT_CANARY_SYNC_ANOMALY_LATENCY_COEF", "0.3"))
                except Exception:
                    latency_coef = 0.3
                min_short_attempts = max(1, min_short_attempts)
                min_long_attempts = max(1, min_long_attempts)
                base_delta = max(0.05, min(2.0, base_delta))
                vol_coef = max(0.0, min(2.0, vol_coef))
                drift_coef = max(0.0, min(2.0, drift_coef))
                latency_coef = max(0.0, min(2.0, latency_coef))
                vol = float(long_volatility or 0.0)
                drift_risk = max(0.0, float(drift_ts.get("score", 0.0) or 0.0))
                latency_risk = max(0.0, min(1.0, float(latency_regime.get("risk", 0.0) or 0.0)))
                # Dynamic threshold: in volatile regimes require larger drop to flag anomaly.
                anomaly_threshold = round(
                    -(
                        base_delta
                        + (vol_coef * max(0.0, min(1.0, vol)))
                        + source_adaptive_penalty
                        + (drift_coef * drift_risk)
                        + (latency_coef * latency_risk)
                    ),
                    4,
                )
                # Anomaly when short window materially degrades versus long baseline.
                anomaly = bool(
                    short_attempts >= min_short_attempts and long_attempts >= min_long_attempts and delta <= anomaly_threshold
                )
                reason = "short_window_degradation" if anomaly else "stable_or_improving"

            vol_n = max(0.0, min(1.0, float(long_volatility or 0.0)))
            drift_risk = max(0.0, min(1.0, float(drift_ts.get("score", 0.0) or 0.0)))
            latency_risk = max(0.0, min(1.0, float(latency_regime.get("risk", 0.0) or 0.0)))
            source_risk = max(0.0, min(1.0, float(source_adaptive_penalty or 0.0)))
            signal_strength = (0.3 * vol_n) + (0.25 * drift_risk) + (0.25 * latency_risk) + (0.2 * source_risk)
            attempt_factor = max(0.0, min(1.0, min(short_attempts / 4.0, long_attempts / 8.0)))
            confidence_score = round(max(0.0, min(1.0, (0.5 * signal_strength) + (0.5 * attempt_factor))), 4)
            confidence_level = "high" if confidence_score >= 0.66 else "medium" if confidence_score >= 0.33 else "low"

            data["trend_summary"] = {
                "score_1h": short_score,
                "score_24h": long_score,
                "volatility_24h": long_volatility,
                "anomaly_threshold": anomaly_threshold,
                "delta_1h_vs_24h": delta,
                "anomaly": anomaly,
                "reason": reason,
                "source_weighting": {
                    "enabled": True,
                    "default_weight": cls._source_type_weight("default"),
                    "autotune_enabled": autotune_enabled,
                    "autotune_strength": round(float(autotune_strength), 4),
                    "learned": learned_source_weights,
                },
                "source_adaptive_penalty": source_adaptive_penalty,
                "latency_regime": latency_regime,
                "drift_1h": short_drift,
                "drift_24h": long_drift,
                "drift_gap": (
                    round(
                        float(short_drift.get("intra_window_delta", 0.0) or 0.0)
                        - float(long_drift.get("intra_window_delta", 0.0) or 0.0),
                        4,
                    )
                    if short_drift.get("intra_window_delta") is not None and long_drift.get("intra_window_delta") is not None
                    else None
                ),
                "drift_time_series": drift_ts,
                "ewma": {
                    "enabled": ewma_enabled,
                    "alpha": round(float(ewma_alpha), 4),
                },
                "risk_confidence": {
                    "score": confidence_score,
                    "level": confidence_level,
                    "attempt_factor": round(float(attempt_factor), 4),
                    "signal_strength": round(float(signal_strength), 4),
                    "components": {
                        "volatility": round(float(vol_n), 4),
                        "drift": round(float(drift_risk), 4),
                        "latency": round(float(latency_risk), 4),
                        "source": round(float(source_risk), 4),
                    },
                },
            }
        data.setdefault("path", str(path))
        return data

    @classmethod
    def get_calendar_risk_components_timeseries(
        cls,
        lookback_hours: int = 24,
        bucket_minutes: int = 60,
        max_points: int = 48,
    ) -> dict[str, Any]:
        """Compute risk component time-series from calendar sync history buckets."""
        status = cls.get_calendar_sync_status()
        history = status.get("history", []) if isinstance(status, dict) else []
        if not isinstance(history, list) or not history:
            return {
                "lookback_hours": int(max(1, lookback_hours)),
                "bucket_minutes": int(max(5, bucket_minutes)),
                "points": [],
            }

        lookback_hours = max(1, int(lookback_hours))
        bucket_minutes = max(5, int(bucket_minutes))
        max_points = max(1, int(max_points))
        now = datetime.now()
        cutoff = now - timedelta(hours=lookback_hours)

        buckets: dict[str, list[dict[str, Any]]] = {}
        for h in history:
            if not isinstance(h, dict):
                continue
            dt = cls._safe_iso_to_dt(h.get("finished_at"))
            if dt is None or dt < cutoff:
                continue
            minute_slot = (dt.minute // bucket_minutes) * bucket_minutes
            bdt = dt.replace(minute=minute_slot, second=0, microsecond=0)
            key = bdt.isoformat()
            if key not in buckets:
                buckets[key] = []
            buckets[key].append(h)

        try:
            abs_trigger_ms = float(os.getenv("AGENT_CANARY_SYNC_LATENCY_ABS_TRIGGER_MS", "60"))
        except Exception:
            abs_trigger_ms = 60.0
        abs_trigger_ms = max(10.0, min(2000.0, abs_trigger_ms))

        points: list[dict[str, Any]] = []
        for key in sorted(buckets.keys()):
            entries = buckets[key]
            weighted_outcomes: list[float] = []
            weighted_success_sum = 0.0
            weighted_total = 0.0
            lats: list[float] = []

            for e in entries:
                s_type = str(e.get("source_type", "unknown"))
                src_w = cls._source_type_weight(s_type)
                outcome = cls._outcome_weight(str(e.get("status", ""))) * src_w
                weighted_outcomes.append(outcome)
                success = 1.0 if cls._is_success_sync_status(e.get("status", "")) else 0.0
                weighted_success_sum += success * src_w
                weighted_total += src_w
                try:
                    lat = float(e.get("latency_ms", 0) or 0)
                    if lat > 0:
                        lats.append(lat)
                except Exception:
                    pass

            if not weighted_outcomes:
                continue

            mean = sum(weighted_outcomes) / len(weighted_outcomes)
            var = sum((w - mean) ** 2 for w in weighted_outcomes) / len(weighted_outcomes)
            volatility = max(0.0, min(1.0, var ** 0.5))

            p95 = cls._percentile(lats, 95.0)
            latency_component = max(0.0, min(1.0, float(p95 or 0.0) / abs_trigger_ms))

            success_rate_w = (weighted_success_sum / weighted_total) if weighted_total > 0 else 0.5
            source_component = max(0.0, min(1.0, 1.0 - success_rate_w))

            ordered = sorted(
                entries,
                key=lambda x: cls._safe_iso_to_dt(x.get("finished_at")) or datetime.min,
            )
            vals = [
                cls._outcome_weight(str(e.get("status", "")))
                * cls._source_type_weight(str(e.get("source_type", "unknown")))
                for e in ordered
            ]
            split = max(1, len(vals) // 2)
            first_avg = sum(vals[:split]) / len(vals[:split])
            last_vals = vals[split:] if vals[split:] else vals[-1:]
            last_avg = sum(last_vals) / len(last_vals)
            drift_component = max(0.0, min(1.0, abs(last_avg - first_avg)))

            signal_strength = (0.3 * volatility) + (0.25 * drift_component) + (0.25 * latency_component) + (0.2 * source_component)
            attempt_factor = max(0.0, min(1.0, len(entries) / 6.0))
            score = max(0.0, min(1.0, (0.5 * signal_strength) + (0.5 * attempt_factor)))

            points.append(
                {
                    "bucket_start": key,
                    "attempts": len(entries),
                    "risk_score": round(float(score), 4),
                    "signal_strength": round(float(signal_strength), 4),
                    "components": {
                        "volatility": round(float(volatility), 4),
                        "drift": round(float(drift_component), 4),
                        "latency": round(float(latency_component), 4),
                        "source": round(float(source_component), 4),
                    },
                }
            )

        return {
            "lookback_hours": lookback_hours,
            "bucket_minutes": bucket_minutes,
            "points": points[-max_points:],
        }

    @classmethod
    def build_trend_explainability_tree(
        cls,
        decision_payload: dict[str, Any],
        sync_status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a compact explainability tree for trend canary decisions."""
        decision = str((decision_payload or {}).get("decision", "hold"))
        reason = (decision_payload or {}).get("reason", [])
        if not isinstance(reason, list):
            reason = [str(reason)]

        sync_risk = (decision_payload or {}).get("sync_risk", {}) or {}
        trend = (sync_status or {}).get("trend_summary", {}) if isinstance(sync_status, dict) else {}
        rc = (trend.get("risk_confidence", {}) if isinstance(trend, dict) else {}) or {}

        nodes = [
            {
                "id": "decision",
                "label": "Canary Decision",
                "value": decision,
                "reason": reason,
            },
            {
                "id": "sync_risk",
                "label": "Sync Risk",
                "value": sync_risk.get("score"),
                "level": sync_risk.get("level"),
                "status": sync_risk.get("status"),
            },
            {
                "id": "risk_confidence",
                "label": "Risk Confidence",
                "value": rc.get("score"),
                "level": rc.get("level"),
            },
        ]

        leaves = []
        comps = rc.get("components", {}) if isinstance(rc.get("components", {}), dict) else {}
        for k in ["volatility", "drift", "latency", "source"]:
            leaves.append({"id": f"component_{k}", "label": k, "value": comps.get(k)})

        return {
            "root": "decision",
            "nodes": nodes,
            "leaves": leaves,
        }

    @classmethod
    def _append_trend_explainability_snapshot(cls, payload: dict[str, Any]) -> None:
        path = cls._trend_explainability_snapshot_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        rows: list[str] = []
        if path.exists():
            try:
                rows = [r for r in path.read_text(encoding="utf-8").splitlines() if r.strip()]
            except Exception:
                rows = []

        rows.append(json.dumps(payload, ensure_ascii=False))
        try:
            keep = int(os.getenv("AGENT_CANARY_TREND_EXPLAINABILITY_SNAPSHOT_LIMIT", "200"))
        except Exception:
            keep = 200
        keep = max(10, keep)
        rows = rows[-keep:]
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    @classmethod
    def get_trend_explainability_snapshots(cls, limit: int = 50) -> dict[str, Any]:
        path = cls._trend_explainability_snapshot_path()
        if not path.exists():
            return {"path": str(path), "items": []}

        try:
            lines = [r for r in path.read_text(encoding="utf-8").splitlines() if r.strip()]
        except Exception:
            return {"path": str(path), "items": []}

        out: list[dict[str, Any]] = []
        for row in lines[-max(1, int(limit)) :]:
            try:
                parsed = json.loads(row)
                if isinstance(parsed, dict):
                    out.append(parsed)
            except Exception:
                continue
        return {"path": str(path), "items": out}

    @classmethod
    def _write_calendar_sync_status(cls, payload: dict[str, Any]) -> None:
        path = cls._calendar_sync_status_path()
        existing: dict[str, Any] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    existing = loaded
            except Exception:
                existing = {}

        history = existing.get("history", []) if isinstance(existing.get("history"), list) else []
        entry = {
            "finished_at": payload.get("finished_at"),
            "status": payload.get("status"),
            "outcome": payload.get("outcome"),
            "ok": payload.get("ok"),
            "source": payload.get("source"),
            "source_type": payload.get("source_type"),
            "merge": payload.get("merge"),
            "imported": payload.get("imported"),
            "written": payload.get("written"),
            "changed": payload.get("changed"),
            "latency_ms": payload.get("latency_ms"),
            "http_status": payload.get("http_status"),
            "error": payload.get("error"),
            "consecutive_failures": payload.get("consecutive_failures"),
            "backoff_sec": payload.get("backoff_sec"),
            "backoff_jitter": payload.get("backoff_jitter"),
        }
        history.append(entry)
        try:
            history_limit = int(os.getenv("AGENT_CANARY_CALENDAR_SYNC_HISTORY_LIMIT", "20"))
        except Exception:
            history_limit = 20
        history_limit = max(1, history_limit)
        payload = {**payload, "history": history[-history_limit:]}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _calendar_signature(row: dict[str, Any]) -> str:
        dates = row.get("dates") if isinstance(row.get("dates"), list) else []
        dates_key = ",".join(sorted(str(x).strip() for x in dates if str(x).strip()))
        return "|".join(
            [
                str(row.get("label", "")).strip(),
                str(row.get("date", "")).strip(),
                str(row.get("month_day", "")).strip(),
                str(row.get("start_date", "")).strip(),
                str(row.get("end_date", "")).strip(),
                dates_key,
            ]
        )

    @classmethod
    def _calendar_rows_hash(cls, rows: list[dict[str, Any]]) -> str:
        normalized = [cls._normalize_calendar_override(row) for row in rows]
        cleaned = [row for row in normalized if row is not None]
        payload = json.dumps(
            sorted(cleaned, key=cls._calendar_signature),
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _slugify_label(value: str) -> str:
        text = str(value or "").strip().lower()
        text = re.sub(r"[^a-z0-9]+", "_", text)
        return text.strip("_") or "calendar_event"

    @classmethod
    def _normalize_calendar_override(cls, row: dict[str, Any]) -> dict[str, Any] | None:
        label = str(row.get("label", "")).strip()
        if not label:
            return None

        cleaned: dict[str, Any] = {"label": label}
        for key in ["date", "month_day", "start_date", "end_date", "severity"]:
            value = str(row.get(key, "")).strip()
            if value:
                cleaned[key] = value

        dates = row.get("dates")
        if isinstance(dates, list):
            cleaned_dates = sorted({str(x).strip() for x in dates if str(x).strip()})
            if cleaned_dates:
                cleaned["dates"] = cleaned_dates

        if not any(k in cleaned for k in ["date", "month_day", "start_date", "end_date", "dates"]):
            return None

        return cleaned

    @staticmethod
    def _parse_ics_datetime(value: str) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1]
        for fmt in ["%Y%m%dT%H%M%S", "%Y%m%dT%H%M", "%Y%m%d"]:
            try:
                return datetime.strptime(raw, fmt)
            except Exception:
                continue
        return None

    @classmethod
    def _parse_ics_calendar_overrides(cls, raw: str) -> list[dict[str, Any]]:
        unfolded: list[str] = []
        for line in str(raw).splitlines():
            if line.startswith((" ", "\t")) and unfolded:
                unfolded[-1] += line[1:]
            else:
                unfolded.append(line.strip())

        events: list[dict[str, str]] = []
        current: dict[str, str] | None = None
        for line in unfolded:
            if line == "BEGIN:VEVENT":
                current = {}
                continue
            if line == "END:VEVENT":
                if current:
                    events.append(current)
                current = None
                continue
            if current is None or ":" not in line:
                continue
            key, value = line.split(":", 1)
            current[key] = value.strip()

        out: list[dict[str, Any]] = []
        for event in events:
            summary = event.get("SUMMARY", "")
            label = cls._slugify_label(summary)
            dtstart_raw = next((v for k, v in event.items() if k.startswith("DTSTART")), "")
            dtend_raw = next((v for k, v in event.items() if k.startswith("DTEND")), "")
            dtstart = cls._parse_ics_datetime(dtstart_raw)
            dtend = cls._parse_ics_datetime(dtend_raw)
            if dtstart is None:
                continue

            row: dict[str, Any] = {"label": label}
            if dtend is not None and dtend.date() > dtstart.date():
                inclusive_end = dtend.date() - timedelta(days=1)
                row["start_date"] = dtstart.date().isoformat()
                row["end_date"] = inclusive_end.isoformat()
            else:
                row["date"] = dtstart.date().isoformat()

            severity = event.get("X-SEVERITY") or event.get("SEVERITY") or event.get("PRIORITY") or ""
            categories = str(event.get("CATEGORIES", "")).lower()
            sev_lower = str(severity).strip().lower()
            if sev_lower in {"1", "2", "3", "critical"}:
                row["severity"] = "critical"
            elif sev_lower in {"4", "5", "high"}:
                row["severity"] = "high"
            elif sev_lower in {"6", "7", "medium"}:
                row["severity"] = "medium"
            elif sev_lower in {"8", "9", "low"}:
                row["severity"] = "low"
            elif "critical" in categories:
                row["severity"] = "critical"
            elif "high" in categories:
                row["severity"] = "high"

            norm = cls._normalize_calendar_override(row)
            if norm is not None:
                out.append(norm)
        return out

    @classmethod
    def _load_calendar_overrides_from_source(cls, source: str) -> list[dict[str, Any]]:
        rows, _meta = cls._load_calendar_overrides_from_source_with_meta(source)
        return rows

    @classmethod
    def _load_calendar_overrides_from_source_with_meta(
        cls,
        source: str,
        prev_status: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        raw = ""
        src = str(source).strip()
        if not src:
            return [], {"remote": False}

        meta: dict[str, Any] = {"remote": False}
        if src.startswith("http://") or src.startswith("https://"):
            meta["remote"] = True
            req = Request(src)
            if isinstance(prev_status, dict):
                if str(prev_status.get("source", "")).strip() == src:
                    etag = str(prev_status.get("http_etag", "")).strip()
                    last_mod = str(prev_status.get("http_last_modified", "")).strip()
                    if etag:
                        req.add_header("If-None-Match", etag)
                    if last_mod:
                        req.add_header("If-Modified-Since", last_mod)

            try:
                with urlopen(req, timeout=10) as resp:  # nosec B310 - controlled by user config
                    raw = resp.read().decode("utf-8")
                    meta["http_status"] = int(getattr(resp, "status", 200) or 200)
                    meta["http_etag"] = str(resp.headers.get("ETag", "")).strip() or None
                    meta["http_last_modified"] = str(resp.headers.get("Last-Modified", "")).strip() or None
            except HTTPError as http_exc:
                if int(getattr(http_exc, "code", 0) or 0) == 304:
                    meta["http_status"] = 304
                    meta["not_modified"] = True
                    meta["http_etag"] = str((prev_status or {}).get("http_etag", "")).strip() or None
                    meta["http_last_modified"] = (
                        str((prev_status or {}).get("http_last_modified", "")).strip() or None
                    )
                    return [], meta
                raise
        else:
            raw = Path(src).read_text(encoding="utf-8")

        lowered = src.lower()
        if lowered.endswith(".ics") or "begin:vcalendar" in raw.lower():
            return cls._parse_ics_calendar_overrides(raw), meta

        data = json.loads(raw)
        if not isinstance(data, list):
            return [], meta

        out: list[dict[str, Any]] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            norm = cls._normalize_calendar_override(row)
            if norm is not None:
                out.append(norm)
        return out, meta

    @classmethod
    def sync_calendar_overrides(
        cls,
        source: str | None = None,
        *,
        merge: bool = True,
    ) -> dict[str, Any]:
        started_dt = datetime.now()
        started_at = started_dt.isoformat()
        src = str(source or os.getenv("AGENT_CANARY_CALENDAR_SYNC_SOURCE", "")).strip()
        source_type = cls._infer_calendar_source_type(src)
        if not src:
            finished_dt = datetime.now()
            result = {
                "ok": False,
                "status": "error",
                "error": "calendar_sync_source_missing",
                "imported": 0,
                "written": 0,
                "source": src,
                "source_type": source_type,
                "merge": merge,
                "started_at": started_at,
                "finished_at": finished_dt.isoformat(),
                "latency_ms": round((finished_dt - started_dt).total_seconds() * 1000.0, 2),
                "outcome": "error",
                "path": str(cls._calendar_file_path()),
                "consecutive_failures": 0,
            }
            cls._write_calendar_sync_status(result)
            return result

        try:
            prev_status = cls.get_calendar_sync_status()
            imported_rows, fetch_meta = cls._load_calendar_overrides_from_source_with_meta(src, prev_status)
            remote_not_modified = bool(fetch_meta.get("not_modified", False))
            current_rows = cls._read_calendar_overrides_file() if merge else []
            if remote_not_modified and not merge:
                current_rows = cls._read_calendar_overrides_file()
            current_hash = cls._calendar_rows_hash(current_rows)

            merged: dict[str, dict[str, Any]] = {}
            for row in current_rows + imported_rows:
                norm = cls._normalize_calendar_override(row)
                if norm is None:
                    continue
                merged[cls._calendar_signature(norm)] = norm

            final_rows = sorted(
                merged.values(),
                key=lambda r: (
                    str(r.get("label", "")),
                    str(r.get("date", r.get("start_date", r.get("month_day", "")))),
                ),
            )
            final_hash = cls._calendar_rows_hash(final_rows)
            changed = current_hash != final_hash
            path = cls._calendar_file_path()
            if changed or not path.exists():
                path.write_text(json.dumps(final_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            finished_dt = datetime.now()
            outcome = "ok" if changed else ("noop_remote_not_modified" if remote_not_modified else "noop")
            result = {
                "ok": True,
                "status": outcome,
                "source": src,
                "source_type": source_type,
                "merge": merge,
                "imported": len(imported_rows),
                "written": len(final_rows),
                "path": str(path),
                "changed": changed,
                "source_hash": final_hash,
                "started_at": started_at,
                "finished_at": finished_dt.isoformat(),
                "latency_ms": round((finished_dt - started_dt).total_seconds() * 1000.0, 2),
                "outcome": outcome,
                "consecutive_failures": 0,
                "http_status": fetch_meta.get("http_status"),
                "http_etag": fetch_meta.get("http_etag"),
                "http_last_modified": fetch_meta.get("http_last_modified"),
            }
            cls._write_calendar_sync_status(result)
            return result
        except Exception as exc:
            prev_status = cls.get_calendar_sync_status()
            prev_failures = 0
            if isinstance(prev_status, dict):
                try:
                    prev_failures = int(prev_status.get("consecutive_failures", 0) or 0)
                except Exception:
                    prev_failures = 0
            failures = prev_failures + 1
            try:
                base_backoff = int(os.getenv("AGENT_CANARY_CALENDAR_SYNC_BACKOFF_BASE_SEC", "120"))
            except Exception:
                base_backoff = 120
            try:
                max_backoff = int(os.getenv("AGENT_CANARY_CALENDAR_SYNC_MAX_BACKOFF_SEC", "3600"))
            except Exception:
                max_backoff = 3600
            try:
                jitter_ratio = float(os.getenv("AGENT_CANARY_CALENDAR_SYNC_BACKOFF_JITTER", "0.15"))
            except Exception:
                jitter_ratio = 0.15
            jitter_ratio = cls._clamp(jitter_ratio, 0.0, 0.9)
            base_backoff = max(30, base_backoff)
            max_backoff = max(base_backoff, max_backoff)
            raw_backoff = float(base_backoff * (2 ** max(0, failures - 1)))
            if jitter_ratio > 0.0:
                factor = random.uniform(1.0 - jitter_ratio, 1.0 + jitter_ratio)
                raw_backoff *= factor
            backoff_sec = int(min(max_backoff, max(base_backoff, raw_backoff)))
            finished_dt = datetime.now()
            retry_after_dt = finished_dt + timedelta(seconds=backoff_sec)
            result = {
                "ok": False,
                "status": "error",
                "source": src,
                "source_type": source_type,
                "merge": merge,
                "imported": 0,
                "written": 0,
                "path": str(cls._calendar_file_path()),
                "started_at": started_at,
                "finished_at": finished_dt.isoformat(),
                "latency_ms": round((finished_dt - started_dt).total_seconds() * 1000.0, 2),
                "outcome": "error",
                "error": f"{exc.__class__.__name__}: {exc}",
                "consecutive_failures": failures,
                "backoff_sec": int(backoff_sec),
                "backoff_jitter": round(float(jitter_ratio), 4),
                "next_retry_at": retry_after_dt.isoformat(),
            }
            cls._write_calendar_sync_status(result)
            raise

    @classmethod
    def maybe_auto_sync_calendar_overrides(cls, force: bool = False) -> dict[str, Any]:
        auto_sync = os.getenv("AGENT_CANARY_CALENDAR_AUTO_SYNC", "0").strip() == "1"
        src = str(os.getenv("AGENT_CANARY_CALENDAR_SYNC_SOURCE", "")).strip()
        if not force and (not auto_sync or not src):
            return {"ok": False, "status": "skipped", "reason": "auto_sync_disabled_or_source_missing"}

        try:
            interval_sec = int(os.getenv("AGENT_CANARY_CALENDAR_SYNC_INTERVAL_SEC", "21600"))
        except Exception:
            interval_sec = 21600
        interval_sec = max(60, interval_sec)

        status = cls.get_calendar_sync_status()
        finished_at = cls._safe_iso_to_dt(status.get("finished_at")) if isinstance(status, dict) else None
        stale = True
        if finished_at is not None:
            stale = (datetime.now() - finished_at).total_seconds() >= interval_sec

        next_retry_at = cls._safe_iso_to_dt(status.get("next_retry_at")) if isinstance(status, dict) else None
        if not force and next_retry_at is not None and next_retry_at > datetime.now():
            return {
                "ok": False,
                "status": "cooldown",
                "reason": "backoff_active",
                "retry_after": next_retry_at.isoformat(),
                "source": src,
            }

        try:
            error_cooldown_sec = int(os.getenv("AGENT_CANARY_CALENDAR_SYNC_ERROR_COOLDOWN_SEC", "900"))
        except Exception:
            error_cooldown_sec = 900
        error_cooldown_sec = max(60, error_cooldown_sec)

        last_status = str(status.get("status", "")).strip().lower() if isinstance(status, dict) else ""
        if not force and last_status == "error" and finished_at is not None:
            retry_after = (finished_at + timedelta(seconds=error_cooldown_sec))
            if retry_after > datetime.now():
                return {
                    "ok": False,
                    "status": "cooldown",
                    "reason": "recent_sync_error",
                    "retry_after": retry_after.isoformat(),
                    "source": src,
                }

        if not force and not stale:
            return {
                "ok": True,
                "status": "fresh",
                "source": src,
                "reason": "interval_not_elapsed",
                "finished_at": status.get("finished_at"),
            }

        merge = os.getenv("AGENT_CANARY_CALENDAR_SYNC_MERGE", "1").strip() != "0"
        return cls.sync_calendar_overrides(source=src, merge=merge)

    @staticmethod
    def _parse_iso_date(value: Any) -> Any:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value)).date()
        except Exception:
            return None

    @staticmethod
    def _calendar_severity_weight(row: dict[str, Any]) -> tuple[str, float]:
        raw = row.get("severity", "medium")
        if isinstance(raw, (int, float)):
            return "custom", QualityEvaluator._clamp(float(raw), 0.5, 1.5)

        label = str(raw or "medium").strip().lower()
        mapping = {
            "low": 0.85,
            "medium": 1.0,
            "high": 1.15,
            "critical": 1.3,
        }
        return label, mapping.get(label, 1.0)

    @staticmethod
    def _calendar_match_labels(
        dt: datetime,
        overrides: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        matched: list[dict[str, Any]] = []
        current_date = dt.date()
        iso_date = current_date.isoformat()
        month_day = dt.strftime("%m-%d")
        for row in overrides:
            label = str(row.get("label", "")).strip()
            if not label:
                continue
            exact_date = str(row.get("date", "")).strip()
            recurring = str(row.get("month_day", "")).strip()
            if exact_date and exact_date == iso_date:
                matched.append(row)
                continue
            if recurring and recurring == month_day:
                matched.append(row)
                continue

            dates = row.get("dates")
            if isinstance(dates, list) and iso_date in {str(x).strip() for x in dates if str(x).strip()}:
                matched.append(row)
                continue

            start_date = QualityEvaluator._parse_iso_date(row.get("start_date"))
            end_date = QualityEvaluator._parse_iso_date(row.get("end_date"))
            if start_date and end_date and start_date <= current_date <= end_date:
                matched.append(row)
        return matched

    def summary(self, lines: int = 1000, days: float = 7.0) -> dict[str, Any]:
        events = self._load_trace_events(lines=lines)

        tool_results = [e for e in events if e.get("event") == "tool_result"]
        tool_total = len(tool_results)
        tool_ok = sum(1 for e in tool_results if bool(e.get("ok")))
        tool_success_rate = (tool_ok / tool_total) if tool_total else None

        denied_count = sum(1 for e in events if e.get("event") == "tool_denied")
        trace_rollbacks = sum(
            1
            for e in events
            if e.get("event") in {"auto_rollback_applied", "auto_rollback_applied_broad"}
        )

        now_ts = time.time()
        days = max(0.25, float(days))
        window_seconds = days * 86400.0
        cutoff = now_ts - window_seconds

        mem_records = self.memory.load_all()
        mem_recent = [r for r in mem_records if float(r.ts) >= cutoff]
        mem_fix_recent = [r for r in mem_recent if r.kind == "fix_attempt"]
        mem_fix_total = len(mem_fix_recent)
        mem_fix_ok = sum(1 for r in mem_fix_recent if r.outcome == "success")
        mem_fix_rate = (mem_fix_ok / mem_fix_total) if mem_fix_total else None
        mem_rollbacks = sum(1 for r in mem_recent if r.kind == "rollback")

        combined_rollbacks = trace_rollbacks + mem_rollbacks

        if tool_success_rate is None and mem_fix_rate is None:
            health_score = 0.0
        else:
            t = tool_success_rate if tool_success_rate is not None else 0.0
            m = mem_fix_rate if mem_fix_rate is not None else 0.0
            health_score = ((0.6 * t) + (0.4 * m)) * 100.0

        risk_penalty = min(30.0, combined_rollbacks * 2.0 + denied_count * 0.5)
        final_score = max(0.0, min(100.0, health_score - risk_penalty))

        if final_score >= 80.0:
            status = "healthy"
        elif final_score >= 60.0:
            status = "watch"
        else:
            status = "critical"

        out = {
            "status": status,
            "score": round(final_score, 2),
            "window_days": days,
            "trace": {
                "events": len(events),
                "tool_results": tool_total,
                "tool_success_rate": round(tool_success_rate, 4) if tool_success_rate is not None else None,
                "denied": denied_count,
                "rollbacks": trace_rollbacks,
            },
            "memory": {
                "recent_records": len(mem_recent),
                "fix_attempts": mem_fix_total,
                "fix_success_rate": round(mem_fix_rate, 4) if mem_fix_rate is not None else None,
                "rollbacks": mem_rollbacks,
            },
            "promotion": {
                "ready": bool(status == "healthy" and combined_rollbacks <= 2),
                "reason": (
                    "ok" if status == "healthy" and combined_rollbacks <= 2 else "stability_or_quality_below_threshold"
                ),
            },
        }
        out["canary"] = self.canary_decision(out)
        return out

    def canary_decision(
        self,
        summary: dict[str, Any],
        min_promote_score: float = 80.0,
        rollback_score: float = 45.0,
        max_denied: int = 5,
        max_rollbacks: int = 2,
        adaptive: bool = False,
    ) -> dict[str, Any]:
        """Derive canary decision from evaluator summary.

        Decisions:
        - promote: quality is strong and risk signals are low
        - hold: continue canary, do not promote yet
        - rollback: severe degradation, recommend rollback
        """
        if adaptive:
            dyn = self.adaptive_thresholds(
                summary,
                min_promote_base=min_promote_score,
                rollback_base=rollback_score,
                max_denied_base=max_denied,
                max_rollbacks_base=max_rollbacks,
            )
            min_promote_score = float(dyn["min_promote_score"])
            rollback_score = float(dyn["rollback_score"])
            max_denied = int(dyn["max_denied"])
            max_rollbacks = int(dyn["max_rollbacks"])

        score = float(summary.get("score", 0.0) or 0.0)
        trace = summary.get("trace", {}) if isinstance(summary.get("trace"), dict) else {}
        memory = summary.get("memory", {}) if isinstance(summary.get("memory"), dict) else {}

        denied = int(trace.get("denied", 0) or 0)
        trace_rollbacks = int(trace.get("rollbacks", 0) or 0)
        mem_rollbacks = int(memory.get("rollbacks", 0) or 0)
        total_rollbacks = trace_rollbacks + mem_rollbacks

        reasons: list[str] = []

        if score < rollback_score:
            reasons.append("score_below_rollback_threshold")
        if denied > max_denied:
            reasons.append("denied_above_threshold")
        if total_rollbacks > max_rollbacks:
            reasons.append("rollback_count_above_threshold")

        if reasons:
            if score < rollback_score:
                return {
                    "decision": "rollback",
                    "reason": reasons,
                    "adaptive": adaptive,
                    "thresholds": {
                        "min_promote_score": min_promote_score,
                        "rollback_score": rollback_score,
                        "max_denied": max_denied,
                        "max_rollbacks": max_rollbacks,
                    },
                }
            return {
                "decision": "hold",
                "reason": reasons,
                "adaptive": adaptive,
                "thresholds": {
                    "min_promote_score": min_promote_score,
                    "rollback_score": rollback_score,
                    "max_denied": max_denied,
                    "max_rollbacks": max_rollbacks,
                },
            }

        if score >= min_promote_score:
            return {
                "decision": "promote",
                "reason": ["quality_and_risk_within_thresholds"],
                "adaptive": adaptive,
                "thresholds": {
                    "min_promote_score": min_promote_score,
                    "rollback_score": rollback_score,
                    "max_denied": max_denied,
                    "max_rollbacks": max_rollbacks,
                },
            }

        return {
            "decision": "hold",
            "reason": ["score_not_high_enough_for_promotion"],
            "adaptive": adaptive,
            "thresholds": {
                "min_promote_score": min_promote_score,
                "rollback_score": rollback_score,
                "max_denied": max_denied,
                "max_rollbacks": max_rollbacks,
            },
        }

    @staticmethod
    def _clamp(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, value))

    def adaptive_thresholds(
        self,
        summary: dict[str, Any],
        min_promote_base: float = 80.0,
        rollback_base: float = 45.0,
        max_denied_base: int = 5,
        max_rollbacks_base: int = 2,
    ) -> dict[str, Any]:
        """Compute dynamic canary thresholds from current workload and risk signals."""
        trace = summary.get("trace", {}) if isinstance(summary.get("trace"), dict) else {}
        memory = summary.get("memory", {}) if isinstance(summary.get("memory"), dict) else {}

        tool_results = int(trace.get("tool_results", 0) or 0)
        denied = int(trace.get("denied", 0) or 0)
        trace_rollbacks = int(trace.get("rollbacks", 0) or 0)
        mem_rollbacks = int(memory.get("rollbacks", 0) or 0)
        total_rollbacks = trace_rollbacks + mem_rollbacks

        denied_ratio = (denied / tool_results) if tool_results > 0 else 0.0
        rollback_ratio = (total_rollbacks / tool_results) if tool_results > 0 else 0.0
        low_sample_penalty = 1.0 if tool_results < 20 else 0.0

        volatility = self._clamp((denied_ratio * 2.0) + (rollback_ratio * 4.0) + low_sample_penalty, 0.0, 1.0)

        min_promote_score = round(self._clamp(min_promote_base + (12.0 * volatility), 70.0, 95.0), 2)
        rollback_score = round(self._clamp(rollback_base - (8.0 * volatility), 25.0, 55.0), 2)
        max_denied = max(2, int(round(max_denied_base - (2.0 * volatility))))
        max_rollbacks = max(1, int(round(max_rollbacks_base - (1.0 * volatility))))

        return {
            "min_promote_score": min_promote_score,
            "rollback_score": rollback_score,
            "max_denied": max_denied,
            "max_rollbacks": max_rollbacks,
            "signals": {
                "tool_results": tool_results,
                "denied": denied,
                "total_rollbacks": total_rollbacks,
                "denied_ratio": round(denied_ratio, 4),
                "rollback_ratio": round(rollback_ratio, 4),
                "volatility": round(volatility, 4),
            },
        }

    def _score_from_metrics(
        self,
        tool_success_rate: float | None,
        mem_fix_rate: float | None,
        denied_count: int,
        trace_rollbacks: int,
        mem_rollbacks: int,
    ) -> tuple[float, str]:
        if tool_success_rate is None and mem_fix_rate is None:
            health_score = 0.0
        else:
            t = tool_success_rate if tool_success_rate is not None else 0.0
            m = mem_fix_rate if mem_fix_rate is not None else 0.0
            health_score = ((0.6 * t) + (0.4 * m)) * 100.0

        combined_rollbacks = trace_rollbacks + mem_rollbacks
        risk_penalty = min(30.0, combined_rollbacks * 2.0 + denied_count * 0.5)
        final_score = max(0.0, min(100.0, health_score - risk_penalty))

        if final_score >= 80.0:
            status = "healthy"
        elif final_score >= 60.0:
            status = "watch"
        else:
            status = "critical"
        return round(final_score, 2), status

    def trend_window_summary(
        self,
        windows_hours: list[int] | None = None,
        lines: int = 20000,
    ) -> dict[str, Any]:
        """Compute evaluator metrics for multiple trailing time windows."""
        windows = windows_hours or [1, 24]
        windows = sorted({max(1, int(w)) for w in windows})
        events = self._load_trace_events(lines=lines)
        now_ts = time.time()

        mem_records = self.memory.load_all()
        out_windows: dict[str, Any] = {}

        for wh in windows:
            cutoff = now_ts - (float(wh) * 3600.0)
            e_recent = [e for e in events if (self._safe_iso_to_ts(e.get("ts")) or 0.0) >= cutoff]
            m_recent = [r for r in mem_records if float(r.ts) >= cutoff]

            tool_results = [e for e in e_recent if e.get("event") == "tool_result"]
            tool_total = len(tool_results)
            tool_ok = sum(1 for e in tool_results if bool(e.get("ok")))
            tool_success_rate = (tool_ok / tool_total) if tool_total else None

            denied_count = sum(1 for e in e_recent if e.get("event") == "tool_denied")
            trace_rollbacks = sum(
                1
                for e in e_recent
                if e.get("event") in {"auto_rollback_applied", "auto_rollback_applied_broad"}
            )

            mem_fix_recent = [r for r in m_recent if r.kind == "fix_attempt"]
            mem_fix_total = len(mem_fix_recent)
            mem_fix_ok = sum(1 for r in mem_fix_recent if r.outcome == "success")
            mem_fix_rate = (mem_fix_ok / mem_fix_total) if mem_fix_total else None
            mem_rollbacks = sum(1 for r in m_recent if r.kind == "rollback")

            score, status = self._score_from_metrics(
                tool_success_rate=tool_success_rate,
                mem_fix_rate=mem_fix_rate,
                denied_count=denied_count,
                trace_rollbacks=trace_rollbacks,
                mem_rollbacks=mem_rollbacks,
            )

            out_windows[f"{wh}h"] = {
                "status": status,
                "score": score,
                "trace": {
                    "events": len(e_recent),
                    "tool_results": tool_total,
                    "tool_success_rate": round(tool_success_rate, 4) if tool_success_rate is not None else None,
                    "denied": denied_count,
                    "rollbacks": trace_rollbacks,
                },
                "memory": {
                    "recent_records": len(m_recent),
                    "fix_attempts": mem_fix_total,
                    "fix_success_rate": round(mem_fix_rate, 4) if mem_fix_rate is not None else None,
                    "rollbacks": mem_rollbacks,
                },
            }

        return {
            "generated_at": datetime.now().isoformat(),
            "windows": out_windows,
        }

    def trend_canary_decision(
        self,
        windows_hours: list[int] | None = None,
        lines: int = 20000,
        use_ewma: bool = False,
        ewma_alpha: float = 0.35,
        use_seasonality: bool = False,
        seasonality_lookback_days: int = 14,
    ) -> dict[str, Any]:
        """Decide canary action based on short-vs-long window trend."""
        summary = self.trend_window_summary(windows_hours=windows_hours or [1, 24], lines=lines)
        windows = summary.get("windows", {})

        sync_risk: dict[str, Any] = {
            "enabled": os.getenv("AGENT_CANARY_TREND_SYNC_RISK", "1").strip() == "1",
            "status": "unavailable",
            "score": None,
            "level": None,
            "components": {},
        }
        if sync_risk["enabled"]:
            try:
                sync_status = self.get_calendar_sync_status()
                tr = (sync_status.get("trend_summary", {}) if isinstance(sync_status, dict) else {}) or {}
                rc = (tr.get("risk_confidence", {}) if isinstance(tr, dict) else {}) or {}
                raw_score = rc.get("score")
                score = None
                if raw_score is not None:
                    score = round(self._clamp(float(raw_score), 0.0, 1.0), 4)
                sync_risk = {
                    "enabled": True,
                    "status": "ok",
                    "score": score,
                    "level": rc.get("level"),
                    "components": rc.get("components", {}),
                    "signal_strength": rc.get("signal_strength"),
                    "attempt_factor": rc.get("attempt_factor"),
                }
            except Exception as exc:
                sync_risk = {
                    "enabled": True,
                    "status": "error",
                    "score": None,
                    "level": None,
                    "components": {},
                    "error": f"{exc.__class__.__name__}: {exc}",
                }

        short = windows.get("1h") or next(iter(windows.values()), None)
        longw = windows.get("24h") or short
        if not short or not longw:
            return {
                "decision": "hold",
                "reason": ["insufficient_window_data"],
                "trend": summary,
            }

        s_score = float(short.get("score", 0.0) or 0.0)
        l_score = float(longw.get("score", 0.0) or 0.0)
        s_status = str(short.get("status", "critical"))
        l_status = str(longw.get("status", "critical"))
        if use_ewma:
            try:
                ewma_alpha = float(ewma_alpha)
            except Exception:
                ewma_alpha = 0.35
            ewma_alpha = self._clamp(ewma_alpha, 0.05, 0.95)
            # EWMA: short pencereyi uzun pencere ile yumuşat
            s_score = round((ewma_alpha * s_score) + ((1.0 - ewma_alpha) * l_score), 2)

        seasonality = {
            "enabled": bool(use_seasonality),
            "lookback_days": int(max(1, seasonality_lookback_days)),
            "hour_baseline": None,
            "hour_observed": None,
            "dow_hour_baseline": None,
            "dow_observed": None,
            "used_baseline": None,
            "cluster_blended_baseline": None,
            "weekday_cluster": None,
            "cluster_baseline": None,
            "neighbor_baseline": None,
            "cluster_similarity": 0.0,
            "cluster_adaptive_coef": 0.0,
            "cluster_coverage": 0,
            "neighbor_coverage": 0,
            "hour_neighbor_baseline": None,
            "hour_neighbor_similarity": 0.0,
            "hour_neighbor_adaptive_coef": 0.0,
            "hour_neighbor_coverage": 0,
            "matrix_blended_baseline": None,
            "dow_hour_samples": 0,
            "hour_samples": 0,
            "sample_confidence": 0.0,
            "variance_confidence": 0.0,
            "volatility_confidence": 0.0,
            "drift_confidence": 1.0,
            "variance": 0.0,
            "volatility": 0.0,
            "trend_drift": 0.0,
            "confidence": 0.0,
            "adjustment": 0.0,
        }
        if use_seasonality:
            trend_drift = abs(s_score - l_score)
            adj = self._seasonality_adjustment(
                lookback_days=max(1, int(seasonality_lookback_days)),
                lines=lines,
                trend_drift=trend_drift,
            )
            seasonality.update(adj)
            s_score = round(self._clamp(s_score + float(adj.get("adjustment", 0.0) or 0.0), 0.0, 100.0), 2)

        delta = round(s_score - l_score, 2)

        if s_status == "critical" and l_score >= 70.0:
            decision = "rollback"
            reason = ["short_window_critical_degradation", f"delta={delta}"]
        elif delta <= -15.0:
            decision = "hold"
            reason = ["short_window_drop_detected", f"delta={delta}"]
        elif s_status == "healthy" and l_status == "healthy" and delta >= -3.0:
            decision = "promote"
            reason = ["stable_or_improving_healthy_trend", f"delta={delta}"]
        else:
            decision = "hold"
            reason = ["trend_not_strong_enough", f"delta={delta}"]

        # Optional guard: degrade decision when calendar sync risk confidence is high.
        if sync_risk.get("enabled") and sync_risk.get("status") == "ok" and sync_risk.get("score") is not None:
            try:
                med = float(os.getenv("AGENT_CANARY_TREND_SYNC_RISK_MEDIUM", "0.5"))
            except Exception:
                med = 0.5
            try:
                high = float(os.getenv("AGENT_CANARY_TREND_SYNC_RISK_HIGH", "0.75"))
            except Exception:
                high = 0.75
            med = self._clamp(med, 0.0, 1.0)
            high = self._clamp(high, med, 1.0)
            rs = float(sync_risk.get("score", 0.0) or 0.0)
            if rs >= high:
                decision = "rollback"
                reason = ["sync_risk_high", f"risk={round(rs, 4)}"]
            elif rs >= med and decision == "promote":
                decision = "hold"
                reason = ["sync_risk_medium", f"risk={round(rs, 4)}"]

        result = {
            "decision": decision,
            "reason": reason,
            "ewma": {"enabled": use_ewma, "alpha": round(float(ewma_alpha), 4)},
            "seasonality": seasonality,
            "sync_risk": sync_risk,
            "trend": summary,
        }

        persist_enabled = os.getenv("AGENT_CANARY_TREND_EXPLAINABILITY_SNAPSHOT", "1").strip() == "1"
        if persist_enabled:
            try:
                sync_status = self.get_calendar_sync_status()
                snapshot = {
                    "ts": datetime.now().isoformat(),
                    "decision": decision,
                    "reason": reason,
                    "sync_risk": sync_risk,
                    "trend_short": short,
                    "trend_long": longw,
                    "risk_confidence": ((sync_status.get("trend_summary", {}) or {}).get("risk_confidence", {})),
                    "explainability_tree": self.build_trend_explainability_tree(result, sync_status),
                }
                self._append_trend_explainability_snapshot(snapshot)
            except Exception:
                pass

        return result

    def _seasonality_adjustment(
        self,
        lookback_days: int = 14,
        lines: int = 20000,
        trend_drift: float | None = None,
        reference_now: datetime | None = None,
        calendar_overrides: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Estimate period baselines and produce a confidence-weighted score adjustment.

        Positive adjustment softens false alarms during historically noisy slots.
        Negative adjustment tightens decision during historically stable slots.
        """
        events = self._load_trace_events(lines=lines)
        now = reference_now or datetime.now()
        now_ts = time.time()
        cutoff = now_ts - (float(max(1, lookback_days)) * 86400.0)
        overrides = calendar_overrides if calendar_overrides is not None else self._load_calendar_overrides()

        buckets_hour: dict[int, dict[str, int]] = {}
        buckets_dow_hour: dict[str, dict[str, int]] = {}
        buckets_calendar_hour: dict[str, dict[str, int]] = {}

        def _ensure_bucket(container: dict[str, dict[str, int]] | dict[int, dict[str, int]], key: str | int):
            if key not in container:
                container[key] = {"total": 0, "ok": 0, "denied": 0, "rollbacks": 0}
            return container[key]

        for e in events:
            dt = self._safe_iso_to_dt(e.get("ts"))
            if dt is None:
                continue
            if dt.timestamp() < cutoff:
                continue
            h = dt.hour
            dow = dt.weekday()
            dh = f"{dow}-{h}"
            b_hour = _ensure_bucket(buckets_hour, h)
            b_dh = _ensure_bucket(buckets_dow_hour, dh)

            if e.get("event") == "tool_result":
                b_hour["total"] += 1
                b_dh["total"] += 1
                if bool(e.get("ok")):
                    b_hour["ok"] += 1
                    b_dh["ok"] += 1
            elif e.get("event") == "tool_denied":
                b_hour["denied"] += 1
                b_dh["denied"] += 1
            elif e.get("event") in {"auto_rollback_applied", "auto_rollback_applied_broad"}:
                b_hour["rollbacks"] += 1
                b_dh["rollbacks"] += 1

            matched_labels = self._calendar_match_labels(dt, overrides)
            for row in matched_labels:
                label = str(row.get("label", "")).strip()
                if not label:
                    continue
                cal_key = f"{label}-{h}"
                b_cal = _ensure_bucket(buckets_calendar_hour, cal_key)
                if e.get("event") == "tool_result":
                    b_cal["total"] += 1
                    if bool(e.get("ok")):
                        b_cal["ok"] += 1
                elif e.get("event") == "tool_denied":
                    b_cal["denied"] += 1
                elif e.get("event") in {"auto_rollback_applied", "auto_rollback_applied_broad"}:
                    b_cal["rollbacks"] += 1

        # Hour and day-of-week-hour baseline score tables
        hour_scores: dict[int, float] = {}
        dow_hour_scores: dict[str, float] = {}
        calendar_hour_scores: dict[str, float] = {}

        def _score_bucket(b: dict[str, int]) -> float | None:
            total = int(b.get("total", 0) or 0)
            ok = int(b.get("ok", 0) or 0)
            denied = int(b.get("denied", 0) or 0)
            rollbacks = int(b.get("rollbacks", 0) or 0)

            if total <= 0:
                return None
            sr = ok / total
            score = (sr * 100.0) - min(20.0, denied * 0.8 + rollbacks * 2.0)
            return round(self._clamp(score, 0.0, 100.0), 2)

        for h, b in buckets_hour.items():
            s = _score_bucket(b)
            if s is not None:
                hour_scores[h] = s

        for dh, b in buckets_dow_hour.items():
            s = _score_bucket(b)
            if s is not None:
                dow_hour_scores[dh] = s

        for cal_key, b in buckets_calendar_hour.items():
            s = _score_bucket(b)
            if s is not None:
                calendar_hour_scores[cal_key] = s

        if not hour_scores and not dow_hour_scores:
            return {
                "hour_baseline": None,
                "dow_hour_baseline": None,
                "hour_observed": now.hour,
                "dow_observed": now.weekday(),
                "used_baseline": None,
                "cluster_blended_baseline": None,
                "weekday_cluster": None,
                "cluster_baseline": None,
                "neighbor_baseline": None,
                "cluster_similarity": 0.0,
                "cluster_adaptive_coef": 0.0,
                "cluster_coverage": 0,
                "neighbor_coverage": 0,
                "hour_neighbor_baseline": None,
                "hour_neighbor_similarity": 0.0,
                "hour_neighbor_adaptive_coef": 0.0,
                "hour_neighbor_coverage": 0,
                "matrix_blended_baseline": None,
                "calendar_override_active": False,
                "calendar_label": None,
                "calendar_baseline": None,
                "calendar_adaptive_coef": 0.0,
                "calendar_coverage": 0,
                "calendar_severity": "medium",
                "calendar_severity_weight": 1.0,
                "dow_hour_samples": 0,
                "hour_samples": 0,
                "sample_confidence": 0.0,
                "variance_confidence": 0.0,
                "volatility_confidence": 0.0,
                "drift_confidence": 1.0,
                "variance": 0.0,
                "volatility": 0.0,
                "trend_drift": round(float(trend_drift or 0.0), 2),
                "confidence": 0.0,
                "adjustment": 0.0,
            }

        current_hour = now.hour
        current_dow = now.weekday()
        current_dh = f"{current_dow}-{current_hour}"
        current_calendar_matches = self._calendar_match_labels(now, overrides)
        current_calendar = current_calendar_matches[0] if current_calendar_matches else None
        calendar_label = str(current_calendar.get("label", "")).strip() if current_calendar else None

        if hour_scores:
            hour_baseline = float(hour_scores.get(current_hour, sum(hour_scores.values()) / len(hour_scores)))
            global_baseline = float(sum(hour_scores.values()) / len(hour_scores))
        else:
            hour_baseline = None
            global_baseline = float(sum(dow_hour_scores.values()) / len(dow_hour_scores))

        dow_hour_baseline = None
        if dow_hour_scores:
            dow_hour_baseline = float(dow_hour_scores.get(current_dh, global_baseline))

        if current_dow < 5:
            cluster_days = [0, 1, 2, 3, 4]
            weekday_cluster = "weekday"
            cluster_max = 5.0
        else:
            cluster_days = [5, 6]
            weekday_cluster = "weekend"
            cluster_max = 2.0

        cluster_vals = [
            float(dow_hour_scores[f"{d}-{current_hour}"])
            for d in cluster_days
            if f"{d}-{current_hour}" in dow_hour_scores
        ]
        cluster_baseline = (sum(cluster_vals) / len(cluster_vals)) if cluster_vals else None
        cluster_coverage = len(cluster_vals)

        neighbor_days = [(current_dow - 1) % 7, (current_dow + 1) % 7]
        neighbor_vals = [
            float(dow_hour_scores[f"{d}-{current_hour}"])
            for d in neighbor_days
            if f"{d}-{current_hour}" in dow_hour_scores
        ]
        neighbor_baseline = (sum(neighbor_vals) / len(neighbor_vals)) if neighbor_vals else None
        neighbor_coverage = len(neighbor_vals)

        cluster_sample_conf = self._clamp(cluster_coverage / cluster_max, 0.0, 1.0)
        neighbor_sample_conf = self._clamp(neighbor_coverage / 2.0, 0.0, 1.0)
        if cluster_baseline is not None and neighbor_baseline is not None:
            cluster_similarity = self._clamp(1.0 - (abs(cluster_baseline - neighbor_baseline) / 35.0), 0.0, 1.0)
        elif cluster_baseline is not None and dow_hour_baseline is not None:
            cluster_similarity = self._clamp(1.0 - (abs(cluster_baseline - dow_hour_baseline) / 40.0), 0.0, 1.0)
        else:
            cluster_similarity = 0.5

        cluster_adaptive_coef = self._clamp(
            (0.6 * cluster_similarity) + (0.25 * cluster_sample_conf) + (0.15 * neighbor_sample_conf),
            0.0,
            1.0,
        )

        neighbor_hours = [(current_hour - 1) % 24, (current_hour + 1) % 24]
        hour_neighbor_vals = [
            float(dow_hour_scores[f"{d}-{h}"])
            for d in cluster_days
            for h in neighbor_hours
            if f"{d}-{h}" in dow_hour_scores
        ]
        hour_neighbor_baseline = (sum(hour_neighbor_vals) / len(hour_neighbor_vals)) if hour_neighbor_vals else None
        hour_neighbor_coverage = len(hour_neighbor_vals)
        hour_neighbor_sample_conf = self._clamp(hour_neighbor_coverage / (len(cluster_days) * 2.0), 0.0, 1.0)

        if hour_neighbor_baseline is not None and cluster_baseline is not None:
            hour_neighbor_similarity = self._clamp(
                1.0 - (abs(hour_neighbor_baseline - cluster_baseline) / 35.0),
                0.0,
                1.0,
            )
        elif hour_neighbor_baseline is not None and dow_hour_baseline is not None:
            hour_neighbor_similarity = self._clamp(
                1.0 - (abs(hour_neighbor_baseline - dow_hour_baseline) / 40.0),
                0.0,
                1.0,
            )
        else:
            hour_neighbor_similarity = 0.5

        hour_neighbor_adaptive_coef = self._clamp(
            (0.7 * hour_neighbor_similarity) + (0.3 * hour_neighbor_sample_conf),
            0.0,
            1.0,
        )

        hour_bucket = buckets_hour.get(current_hour, {"total": 0})
        dow_hour_bucket = buckets_dow_hour.get(current_dh, {"total": 0})
        hour_samples = int(hour_bucket.get("total", 0) or 0)
        dow_hour_samples = int(dow_hour_bucket.get("total", 0) or 0)

        # Confidence weighting: sparse or volatile dow-hour cells should not dominate.
        # At 12+ samples, sample confidence saturates.
        sample_confidence = self._clamp(dow_hour_samples / 12.0, 0.0, 1.0)
        ok = int(dow_hour_bucket.get("ok", 0) or 0)
        denied = int(dow_hour_bucket.get("denied", 0) or 0)
        rollbacks = int(dow_hour_bucket.get("rollbacks", 0) or 0)
        if dow_hour_samples > 0:
            p = self._clamp(ok / max(1, dow_hour_samples), 0.0, 1.0)
            variance = p * (1.0 - p)  # Bernoulli variance in [0, 0.25]
        else:
            variance = 0.25
        variance_confidence = self._clamp(1.0 - (variance / 0.25), 0.0, 1.0)
        volatility = self._clamp(((denied * 0.5) + (rollbacks * 1.5)) / max(1, dow_hour_samples), 0.0, 1.0)
        volatility_confidence = self._clamp(1.0 - volatility, 0.0, 1.0)

        drift = abs(float(trend_drift or 0.0))
        # Large short-vs-long drift means current regime is unstable; trust seasonality less.
        drift_confidence = self._clamp(1.0 - (drift / 30.0), 0.0, 1.0)

        raw_confidence = sample_confidence * (
            (0.5 * variance_confidence)
            + (0.3 * volatility_confidence)
            + (0.2 * drift_confidence)
        )

        if dow_hour_baseline is not None and cluster_baseline is not None:
            cluster_blended_baseline = (
                (cluster_adaptive_coef * float(dow_hour_baseline))
                + ((1.0 - cluster_adaptive_coef) * float(cluster_baseline))
            )
        elif cluster_baseline is not None:
            cluster_blended_baseline = float(cluster_baseline)
        else:
            cluster_blended_baseline = dow_hour_baseline

        if cluster_blended_baseline is not None and hour_neighbor_baseline is not None:
            matrix_blended_baseline = (
                (hour_neighbor_adaptive_coef * float(cluster_blended_baseline))
                + ((1.0 - hour_neighbor_adaptive_coef) * float(hour_neighbor_baseline))
            )
        elif hour_neighbor_baseline is not None:
            matrix_blended_baseline = float(hour_neighbor_baseline)
        else:
            matrix_blended_baseline = cluster_blended_baseline

        calendar_baseline = None
        calendar_coverage = 0
        calendar_adaptive_coef = 0.0
        calendar_override_active = bool(calendar_label)
        calendar_severity = "medium"
        calendar_severity_weight = 1.0
        if calendar_label:
            cal_key = f"{calendar_label}-{current_hour}"
            if cal_key in calendar_hour_scores:
                calendar_baseline = float(calendar_hour_scores[cal_key])
            calendar_bucket = buckets_calendar_hour.get(cal_key, {"total": 0})
            calendar_coverage = int(calendar_bucket.get("total", 0) or 0)
            calendar_severity, calendar_severity_weight = self._calendar_severity_weight(current_calendar or {})
            calendar_adaptive_coef = self._clamp((calendar_coverage / 6.0) * calendar_severity_weight, 0.0, 1.0)

        if matrix_blended_baseline is not None and calendar_baseline is not None:
            matrix_blended_baseline = (
                (calendar_adaptive_coef * float(calendar_baseline))
                + ((1.0 - calendar_adaptive_coef) * float(matrix_blended_baseline))
            )
        elif calendar_baseline is not None:
            matrix_blended_baseline = float(calendar_baseline)

        confidence = raw_confidence * (0.65 + (0.2 * cluster_adaptive_coef) + (0.15 * hour_neighbor_adaptive_coef))
        if calendar_override_active:
            confidence *= self._clamp((0.75 + (0.2 * calendar_adaptive_coef)) * calendar_severity_weight, 0.5, 1.25)
        fallback_baseline = float(hour_baseline if hour_baseline is not None else global_baseline)
        if matrix_blended_baseline is not None:
            used_baseline = (confidence * float(matrix_blended_baseline)) + ((1.0 - confidence) * fallback_baseline)
        else:
            used_baseline = fallback_baseline

        # If current slot is historically weaker than global baseline, loosen up slightly.
        # If stronger, tighten a bit. Clamp to +/-6 to avoid overreaction.
        adjustment = self._clamp((global_baseline - used_baseline) * 0.2, -6.0, 6.0)

        return {
            "hour_baseline": round(float(hour_baseline), 2) if hour_baseline is not None else None,
            "dow_hour_baseline": round(float(dow_hour_baseline), 2) if dow_hour_baseline is not None else None,
            "hour_observed": current_hour,
            "dow_observed": current_dow,
            "cluster_blended_baseline": (
                round(float(cluster_blended_baseline), 2) if cluster_blended_baseline is not None else None
            ),
            "weekday_cluster": weekday_cluster,
            "cluster_baseline": round(float(cluster_baseline), 2) if cluster_baseline is not None else None,
            "neighbor_baseline": round(float(neighbor_baseline), 2) if neighbor_baseline is not None else None,
            "cluster_similarity": round(float(cluster_similarity), 4),
            "cluster_adaptive_coef": round(float(cluster_adaptive_coef), 4),
            "cluster_coverage": int(cluster_coverage),
            "neighbor_coverage": int(neighbor_coverage),
            "hour_neighbor_baseline": (
                round(float(hour_neighbor_baseline), 2) if hour_neighbor_baseline is not None else None
            ),
            "hour_neighbor_similarity": round(float(hour_neighbor_similarity), 4),
            "hour_neighbor_adaptive_coef": round(float(hour_neighbor_adaptive_coef), 4),
            "hour_neighbor_coverage": int(hour_neighbor_coverage),
            "matrix_blended_baseline": (
                round(float(matrix_blended_baseline), 2) if matrix_blended_baseline is not None else None
            ),
            "calendar_override_active": calendar_override_active,
            "calendar_label": calendar_label,
            "calendar_baseline": round(float(calendar_baseline), 2) if calendar_baseline is not None else None,
            "calendar_adaptive_coef": round(float(calendar_adaptive_coef), 4),
            "calendar_coverage": int(calendar_coverage),
            "calendar_severity": calendar_severity,
            "calendar_severity_weight": round(float(calendar_severity_weight), 4),
            "used_baseline": round(float(used_baseline), 2),
            "dow_hour_samples": dow_hour_samples,
            "hour_samples": hour_samples,
            "sample_confidence": round(float(sample_confidence), 4),
            "variance_confidence": round(float(variance_confidence), 4),
            "volatility_confidence": round(float(volatility_confidence), 4),
            "drift_confidence": round(float(drift_confidence), 4),
            "variance": round(float(variance), 4),
            "volatility": round(float(volatility), 4),
            "trend_drift": round(float(drift), 2),
            "confidence": round(float(confidence), 4),
            "adjustment": round(float(adjustment), 2),
        }
