"""Persistent cross-run memory store for agent learning.

Records: fix attempts, tool outcomes, candidate rankings, rollback decisions.
Each record is a JSONL line appended to a single file.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


_DEFAULT_PATH = Path("memory/agent_memory.jsonl")


@dataclass
class MemoryRecord:
    kind: str                          # e.g. "fix_attempt", "rollback", "plan"
    objective: str
    outcome: str                       # "success" | "failure" | "rollback"
    path: str = ""
    line: int = 0
    plan_json: dict[str, Any] = field(default_factory=dict)
    hint: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


class MemoryStore:
    def __init__(self, file_path: Path | str | None = None) -> None:
        self.path = Path(file_path) if file_path else _DEFAULT_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    def record(self, rec: MemoryRecord) -> None:
        line = json.dumps(asdict(rec), ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def record_fix_attempt(
        self,
        *,
        objective: str,
        path: str,
        line: int,
        outcome: str,
        hint: str = "",
        plan_json: dict[str, Any] | None = None,
    ) -> None:
        self.record(
            MemoryRecord(
                kind="fix_attempt",
                objective=objective,
                outcome=outcome,
                path=path,
                line=line,
                hint=hint,
                plan_json=plan_json or {},
            )
        )

    def record_rollback(
        self,
        *,
        objective: str,
        path: str,
        hint: str = "",
        trigger: str = "narrow_test_fail",
    ) -> None:
        self.record(
            MemoryRecord(
                kind="rollback",
                objective=objective,
                outcome="rollback",
                path=path,
                hint=hint,
                meta={"trigger": trigger},
            )
        )

    # ------------------------------------------------------------------
    # Read / recall
    # ------------------------------------------------------------------
    def load_all(self) -> list[MemoryRecord]:
        if not self.path.exists():
            return []
        records = []
        with self.path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    records.append(MemoryRecord(**json.loads(raw)))
                except Exception:
                    pass
        return records

    def recall_failures_for_path(self, path: str, limit: int = 5) -> list[MemoryRecord]:
        return [
            r for r in self.load_all()
            if r.path == path and r.outcome != "success"
        ][-limit:]

    def recall_recent(self, limit: int = 20) -> list[MemoryRecord]:
        recs = self.load_all()
        recs.sort(key=lambda r: r.ts)
        return recs[-limit:]

    def success_rate_for_path(self, path: str) -> float:
        recs = [r for r in self.load_all() if r.path == path]
        if not recs:
            return 0.0
        ok = sum(1 for r in recs if r.outcome == "success")
        return ok / len(recs)

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        if not text:
            return set()
        return {t for t in re.split(r"[^a-z0-9_]+", text.lower()) if len(t) > 1}

    def semantic_search(self, query: str, limit: int = 10, path: str = "") -> list[dict[str, Any]]:
        """Lightweight semantic retrieval using token overlap (Jaccard-like score)."""
        q_tokens = self._tokenize(query)
        if not q_tokens:
            return []

        scored: list[dict[str, Any]] = []
        for rec in self.load_all():
            if path and rec.path != path:
                continue
            hay = f"{rec.objective} {rec.hint} {rec.path} {rec.kind} {rec.outcome}"
            r_tokens = self._tokenize(hay)
            if not r_tokens:
                continue
            inter = len(q_tokens & r_tokens)
            if inter == 0:
                continue
            union = len(q_tokens | r_tokens)
            score = inter / union if union else 0.0
            scored.append(
                {
                    "score": round(score, 4),
                    "record": rec,
                }
            )

        scored.sort(key=lambda x: (x["score"], x["record"].ts), reverse=True)
        return scored[: max(1, int(limit))]
