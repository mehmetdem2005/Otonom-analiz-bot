"""Dream/Consolidation system for MemoryStore (ADIM-3).

Trigger: 24h elapsed AND min 5 new sessions since last dream.
Operations:
  - Duplicate detection (same path + similar objective/hint)
  - Record merging: combined hints, latest ts, hit_count accumulated
  - Importance scoring: hit_count * recency_factor
  - Lock file ensures single concurrent dream process
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any

from memory_store import MemoryRecord, MemoryStore


_STATUS_FILENAME = "dream_status.json"
_LOCK_FILENAME = "dream.lock"
_LOCK_TIMEOUT_SEC = 300  # 5 min stale lock guard


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-zçşğüöıA-ZÇŞĞÜÖİ0-9_./]+", text.lower()))


def _overlap(a: str, b: str) -> float:
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta | tb), 1)


def _recency_factor(ts: float, half_life_days: float = 30.0) -> float:
    age_days = (time.time() - ts) / 86400.0
    return math.exp(-age_days / half_life_days)


class DreamConsolidator:
    """Session tracker + memory consolidation process."""

    def __init__(
        self,
        memory: MemoryStore,
        status_path: Path | None = None,
        lock_path: Path | None = None,
    ) -> None:
        self.memory = memory
        base = memory.path.parent
        self.status_path = status_path or (base / _STATUS_FILENAME)
        self.lock_path = lock_path or (base / _LOCK_FILENAME)

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Return current dream status (creates default if missing)."""
        if not self.status_path.exists():
            return {
                "last_dream_ts": None,
                "sessions_since_dream": 0,
                "total_dreams": 0,
                "last_dream_result": None,
                "session_ids": [],
            }
        try:
            return json.loads(self.status_path.read_text(encoding="utf-8"))
        except Exception:
            return {
                "last_dream_ts": None,
                "sessions_since_dream": 0,
                "total_dreams": 0,
                "last_dream_result": None,
                "session_ids": [],
            }

    def _write_status(self, status: dict[str, Any]) -> None:
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        self.status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")

    def record_session(self, session_id: str) -> None:
        """Increment session counter since last dream."""
        st = self.get_status()
        ids: list = st.get("session_ids") or []
        if session_id not in ids:
            ids.append(session_id)
        st["session_ids"] = ids
        st["sessions_since_dream"] = len(ids)
        self._write_status(st)

    # ------------------------------------------------------------------
    # Trigger
    # ------------------------------------------------------------------

    def should_dream(
        self,
        min_sessions: int = 5,
        min_hours: float = 24.0,
    ) -> tuple[bool, str]:
        """Return (True, reason) if dream should run, else (False, reason)."""
        st = self.get_status()
        sessions = int(st.get("sessions_since_dream") or 0)
        last_ts = st.get("last_dream_ts")

        if sessions < min_sessions:
            return False, f"sessions_since_dream={sessions} < {min_sessions}"

        if last_ts is not None:
            elapsed_h = (time.time() - float(last_ts)) / 3600.0
            if elapsed_h < min_hours:
                return False, f"elapsed={elapsed_h:.1f}h < {min_hours}h"

        return True, f"sessions={sessions}, ready"

    # ------------------------------------------------------------------
    # Lock
    # ------------------------------------------------------------------

    def _acquire_lock(self) -> bool:
        if self.lock_path.exists():
            try:
                age = time.time() - self.lock_path.stat().st_mtime
                if age < _LOCK_TIMEOUT_SEC:
                    return False  # Lock held
            except Exception:
                pass
            # Stale lock – remove
            try:
                self.lock_path.unlink()
            except Exception:
                return False
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            self.lock_path.write_text(str(os.getpid()), encoding="utf-8")
            return True
        except Exception:
            return False

    def _release_lock(self) -> None:
        try:
            if self.lock_path.exists():
                self.lock_path.unlink()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Consolidation logic
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_group(records: list[MemoryRecord]) -> MemoryRecord:
        """Merge a list of duplicate records into one consolidated record."""
        primary = sorted(records, key=lambda r: r.ts, reverse=True)[0]

        all_hints = list({r.hint for r in records if r.hint})
        merged_hint = " | ".join(all_hints[:3])

        all_objectives = list({r.objective for r in records if r.objective})
        merged_obj = all_objectives[0] if all_objectives else primary.objective

        hit_count = len(records)
        importance = hit_count * _recency_factor(primary.ts)

        merged_meta: dict[str, Any] = {
            **primary.meta,
            "hit_count": hit_count,
            "importance_score": round(importance, 4),
            "merged_from": hit_count,
            "dream_merged": True,
        }

        return MemoryRecord(
            kind=primary.kind,
            objective=merged_obj,
            outcome=primary.outcome,
            path=primary.path,
            line=primary.line,
            plan_json=primary.plan_json,
            hint=merged_hint,
            meta=merged_meta,
            ts=primary.ts,
        )

    def consolidate(
        self,
        similarity_threshold: float = 0.6,
    ) -> dict[str, Any]:
        """Run the dream consolidation and rewrite memory file.

        Returns a summary dict with merged_count, removed_count, kept_count.
        """
        records = self.memory.load_all()
        if not records:
            return {"merged_count": 0, "removed_count": 0, "kept_count": 0, "total_before": 0}

        total_before = len(records)

        # Group by (path, kind) first
        groups: dict[tuple[str, str], list[MemoryRecord]] = {}
        ungrouped: list[MemoryRecord] = []
        for rec in records:
            key = (rec.path or "", rec.kind or "")
            groups.setdefault(key, []).append(rec)

        consolidated: list[MemoryRecord] = []
        merged_count = 0
        removed_count = 0

        for (path, kind), group in groups.items():
            if len(group) == 1:
                consolidated.append(group[0])
                continue

            # Within group, cluster by objective+hint similarity
            clusters: list[list[MemoryRecord]] = []
            assigned = [False] * len(group)

            for i, rec in enumerate(group):
                if assigned[i]:
                    continue
                cluster = [rec]
                assigned[i] = True
                for j in range(i + 1, len(group)):
                    if assigned[j]:
                        continue
                    sim = _overlap(
                        f"{rec.objective} {rec.hint}",
                        f"{group[j].objective} {group[j].hint}",
                    )
                    if sim >= similarity_threshold:
                        cluster.append(group[j])
                        assigned[j] = True
                clusters.append(cluster)

            for cluster in clusters:
                if len(cluster) > 1:
                    merged = self._merge_group(cluster)
                    consolidated.append(merged)
                    merged_count += 1
                    removed_count += len(cluster) - 1
                else:
                    consolidated.append(cluster[0])

        # Rewrite memory file
        self.memory.path.write_text("", encoding="utf-8")
        # Also clear and rebuild indexes
        if self.memory.index_path.exists():
            self.memory.index_path.write_text("", encoding="utf-8")
        if self.memory.ann_index_path.exists():
            self.memory.ann_index_path.write_text("", encoding="utf-8")

        for rec in consolidated:
            self.memory.record(rec)

        kept_count = len(consolidated)
        return {
            "total_before": total_before,
            "merged_count": merged_count,
            "removed_count": removed_count,
            "kept_count": kept_count,
        }

    # ------------------------------------------------------------------
    # maybe_dream: full trigger + lock + consolidate cycle
    # ------------------------------------------------------------------

    def maybe_dream(
        self,
        force: bool = False,
        min_sessions: int = 5,
        min_hours: float = 24.0,
        similarity_threshold: float = 0.6,
    ) -> dict[str, Any]:
        """Check trigger, acquire lock, consolidate, release lock, update status.

        Returns a result dict with 'ran', 'reason', and optional 'result'.
        """
        if not force:
            ok, reason = self.should_dream(min_sessions=min_sessions, min_hours=min_hours)
            if not ok:
                return {"ran": False, "reason": reason}

        if not self._acquire_lock():
            return {"ran": False, "reason": "lock_held"}

        try:
            result = self.consolidate(similarity_threshold=similarity_threshold)
        except Exception as exc:
            self._release_lock()
            return {"ran": False, "reason": f"error:{type(exc).__name__}:{exc}", "result": None}

        self._release_lock()

        # Update status
        st = self.get_status()
        st["last_dream_ts"] = time.time()
        st["sessions_since_dream"] = 0
        st["session_ids"] = []
        st["total_dreams"] = int(st.get("total_dreams") or 0) + 1
        st["last_dream_result"] = result
        self._write_status(st)

        return {"ran": True, "reason": "consolidated", "result": result}
