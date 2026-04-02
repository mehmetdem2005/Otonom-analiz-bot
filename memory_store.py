"""Persistent cross-run memory store for agent learning.

Records: fix attempts, tool outcomes, candidate rankings, rollback decisions.
Each record is a JSONL line appended to a single file.
"""

from __future__ import annotations

import json
import re
import time
import hashlib
import math
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from statistics import mean


_DEFAULT_PATH = Path("memory/agent_memory.jsonl")
_HASH_INDEX_SCHEMA = "hindex-1"
_ANN_INDEX_SCHEMA = "annindex-1"


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
    _st_model = None
    _st_model_failed = False

    def __init__(self, file_path: Path | str | None = None) -> None:
        self.path = Path(file_path) if file_path else _DEFAULT_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path = self.path.with_name(f"{self.path.name}.hindex.jsonl")
        self.ann_index_path = self.path.with_name(f"{self.path.name}.ann.jsonl")

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    def record(self, rec: MemoryRecord) -> None:
        line = json.dumps(asdict(rec), ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        self._append_hash_index_entry(rec)

    @staticmethod
    def _vec_to_pairs(vec: dict[int, float]) -> list[list[float]]:
        return [[int(k), float(v)] for k, v in sorted(vec.items(), key=lambda x: x[0])]

    @staticmethod
    def _pairs_to_vec(pairs: list[list[float]]) -> dict[int, float]:
        out: dict[int, float] = {}
        for pair in pairs:
            if not isinstance(pair, list) or len(pair) != 2:
                continue
            try:
                out[int(pair[0])] = float(pair[1])
            except Exception:
                continue
        return out

    def _record_text(self, rec: MemoryRecord) -> str:
        return f"{rec.objective} {rec.hint} {rec.path} {rec.kind} {rec.outcome}"

    def _append_hash_index_entry(self, rec: MemoryRecord) -> None:
        vec = self._hashed_embedding(self._record_text(rec))
        payload = {
            "schema": _HASH_INDEX_SCHEMA,
            "ts": rec.ts,
            "path": rec.path,
            "record": asdict(rec),
            "vec": self._vec_to_pairs(vec),
        }
        line = json.dumps(payload, ensure_ascii=False)
        with self.index_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        self._append_ann_index_entry(rec, vec)

    @staticmethod
    def _ann_dim_for_bit(bit: int, dim: int = 256) -> int:
        digest = hashlib.sha1(f"ann-bit-{bit}".encode("utf-8")).hexdigest()
        return int(digest[:8], 16) % dim

    @classmethod
    def _ann_key(cls, vec: dict[int, float], bits: int = 24, dim: int = 256) -> str:
        key_bits = []
        for b in range(bits):
            idx = cls._ann_dim_for_bit(b, dim=dim)
            val = vec.get(idx, 0.0)
            key_bits.append("1" if val >= 0.0 else "0")
        return "".join(key_bits)

    def _append_ann_index_entry(self, rec: MemoryRecord, vec: dict[int, float]) -> None:
        payload = {
            "schema": _ANN_INDEX_SCHEMA,
            "ts": rec.ts,
            "path": rec.path,
            "key": self._ann_key(vec),
            "record": asdict(rec),
            "vec": self._vec_to_pairs(vec),
        }
        line = json.dumps(payload, ensure_ascii=False)
        with self.ann_index_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    @staticmethod
    def _line_count(path: Path) -> int:
        if not path.exists():
            return 0
        count = 0
        with path.open("r", encoding="utf-8") as fh:
            for _ in fh:
                count += 1
        return count

    def rebuild_hash_index(self) -> int:
        records = self.load_all()
        with self.index_path.open("w", encoding="utf-8") as fh:
            for rec in records:
                vec = self._hashed_embedding(self._record_text(rec))
                payload = {
                    "schema": _HASH_INDEX_SCHEMA,
                    "ts": rec.ts,
                    "path": rec.path,
                    "record": asdict(rec),
                    "vec": self._vec_to_pairs(vec),
                }
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        with self.ann_index_path.open("w", encoding="utf-8") as fh:
            for rec in records:
                vec = self._hashed_embedding(self._record_text(rec))
                payload = {
                    "schema": _ANN_INDEX_SCHEMA,
                    "ts": rec.ts,
                    "path": rec.path,
                    "key": self._ann_key(vec),
                    "record": asdict(rec),
                    "vec": self._vec_to_pairs(vec),
                }
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return len(records)

    def ensure_hash_index(self) -> None:
        data_lines = self._line_count(self.path)
        index_lines = self._line_count(self.index_path)
        ann_lines = self._line_count(self.ann_index_path)
        if data_lines == 0 and index_lines > 0:
            self.index_path.unlink(missing_ok=True)
            self.ann_index_path.unlink(missing_ok=True)
            return
        if data_lines > 0 and (data_lines != index_lines or data_lines != ann_lines):
            self.rebuild_hash_index()

    def _iter_hash_index(self) -> list[tuple[MemoryRecord, dict[int, float]]]:
        if not self.index_path.exists():
            return []
        out: list[tuple[MemoryRecord, dict[int, float]]] = []
        with self.index_path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                    if obj.get("schema") != _HASH_INDEX_SCHEMA:
                        continue
                    rec = MemoryRecord(**obj.get("record", {}))
                    vec = self._pairs_to_vec(obj.get("vec", []))
                    out.append((rec, vec))
                except Exception:
                    continue
        return out

    def _iter_ann_index(self) -> list[tuple[MemoryRecord, dict[int, float], str]]:
        if not self.ann_index_path.exists():
            return []
        out: list[tuple[MemoryRecord, dict[int, float], str]] = []
        with self.ann_index_path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                    if obj.get("schema") != _ANN_INDEX_SCHEMA:
                        continue
                    rec = MemoryRecord(**obj.get("record", {}))
                    vec = self._pairs_to_vec(obj.get("vec", []))
                    key = str(obj.get("key", ""))
                    out.append((rec, vec, key))
                except Exception:
                    continue
        return out

    @staticmethod
    def _hamming_distance(a: str, b: str) -> int:
        n = min(len(a), len(b))
        return sum(1 for i in range(n) if a[i] != b[i]) + abs(len(a) - len(b))

    def _ann_candidates(
        self,
        query_key: str,
        path: str = "",
        max_candidates: int = 200,
    ) -> list[tuple[MemoryRecord, dict[int, float]]]:
        rows = self._iter_ann_index()
        if path:
            rows = [r for r in rows if r[0].path == path]

        exact = [(rec, vec) for rec, vec, key in rows if key == query_key]
        if len(exact) >= min(20, max_candidates):
            return exact[:max_candidates]

        prefix = query_key[:12]
        near = [(rec, vec, key) for rec, vec, key in rows if key.startswith(prefix)]
        near.sort(key=lambda x: self._hamming_distance(x[2], query_key))
        merged = exact + [(rec, vec) for rec, vec, _ in near if (rec, vec) not in exact]
        if len(merged) >= min(20, max_candidates):
            return merged[:max_candidates]

        # Fallback: approximate nearest keys across all rows.
        global_near = sorted(rows, key=lambda x: self._hamming_distance(x[2], query_key))
        for rec, vec, _ in global_near:
            if (rec, vec) not in merged:
                merged.append((rec, vec))
            if len(merged) >= max_candidates:
                break
        return merged[:max_candidates]

    def recommended_mode(self) -> str:
        """Return a practical default search mode based on current data and capabilities."""
        records = self._line_count(self.path)
        if records == 0:
            return "overlap"
        if records >= 300:
            return "ann"
        if self._get_sentence_model() is not None:
            return "model"
        # For larger stores prefer indexed embedding fallback.
        if records >= 200:
            return "embedding"
        return "overlap"

    def benchmark_search(
        self,
        query: str,
        limit: int = 10,
        repeats: int = 5,
        path: str = "",
    ) -> dict[str, Any]:
        """Micro-benchmark semantic search modes and return latency summary in ms."""
        repeats = max(1, int(repeats))
        modes = ["overlap", "embedding", "auto"]
        if self._line_count(self.path) >= 20:
            modes.append("ann")
        if self._get_sentence_model() is not None:
            modes.append("model")

        mode_stats: dict[str, Any] = {}
        for mode in modes:
            durations: list[float] = []
            last_count = 0
            for _ in range(repeats):
                t0 = time.perf_counter()
                out = self.semantic_search(
                    query=query,
                    limit=limit,
                    path=path,
                    mode=mode,
                )
                dt_ms = (time.perf_counter() - t0) * 1000.0
                durations.append(dt_ms)
                last_count = len(out)

            mode_stats[mode] = {
                "runs": repeats,
                "avg_ms": round(mean(durations), 3),
                "min_ms": round(min(durations), 3),
                "max_ms": round(max(durations), 3),
                "result_count": last_count,
            }

        return {
            "query": query,
            "records": self._line_count(self.path),
            "index_records": self._line_count(self.index_path),
            "recommended_mode": self.recommended_mode(),
            "modes": mode_stats,
        }

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

    @staticmethod
    def _stable_hash_index(token: str, dim: int) -> tuple[int, int]:
        digest = hashlib.sha1(token.encode("utf-8")).hexdigest()
        idx = int(digest[:8], 16) % dim
        sign = -1 if (int(digest[8:10], 16) % 2) else 1
        return idx, sign

    @classmethod
    def _hashed_embedding(cls, text: str, dim: int = 256) -> dict[int, float]:
        tokens = cls._tokenize(text)
        if not tokens:
            return {}

        vec: dict[int, float] = {}
        for token in tokens:
            idx, sign = cls._stable_hash_index(token, dim)
            vec[idx] = vec.get(idx, 0.0) + float(sign)

        norm = math.sqrt(sum(v * v for v in vec.values()))
        if norm == 0.0:
            return {}
        return {k: (v / norm) for k, v in vec.items()}

    @staticmethod
    def _cosine_sparse(a: dict[int, float], b: dict[int, float]) -> float:
        if not a or not b:
            return 0.0
        if len(a) > len(b):
            a, b = b, a
        return sum(av * b.get(ai, 0.0) for ai, av in a.items())

    @classmethod
    def _get_sentence_model(cls):
        if cls._st_model_failed:
            return None
        if cls._st_model is not None:
            return cls._st_model
        try:
            from sentence_transformers import SentenceTransformer

            model_name = os.environ.get(
                "MEMORY_EMBEDDING_MODEL",
                "sentence-transformers/all-MiniLM-L6-v2",
            )
            cls._st_model = SentenceTransformer(model_name)
            return cls._st_model
        except Exception:
            cls._st_model_failed = True
            return None

    @classmethod
    def _sentence_embeddings(cls, texts: list[str]) -> list[list[float]] | None:
        model = cls._get_sentence_model()
        if model is None:
            return None
        try:
            vectors = model.encode(texts, normalize_embeddings=True)
            if hasattr(vectors, "tolist"):
                vectors = vectors.tolist()
            out: list[list[float]] = []
            for v in vectors:
                if hasattr(v, "tolist"):
                    v = v.tolist()
                out.append([float(x) for x in v])
            return out
        except Exception:
            return None

    @staticmethod
    def _dot_dense(a: list[float], b: list[float]) -> float:
        if not a or not b:
            return 0.0
        n = min(len(a), len(b))
        return sum(a[i] * b[i] for i in range(n))

    def semantic_search(
        self,
        query: str,
        limit: int = 10,
        path: str = "",
        mode: str = "overlap",
        time_weight: float = 0.15,
        half_life_days: float = 14.0,
    ) -> list[dict[str, Any]]:
        """Semantic retrieval.

        Modes:
        - overlap: token overlap score (Jaccard-like)
        - embedding: hashed sparse vector cosine similarity
        - ann: approximate nearest neighbors over persisted ANN sidecar
        - model: sentence-transformers cosine (requires package/model)
        - auto: try model first, fallback to embedding

        Rerank:
        - final score = (1-time_weight) * relevance + time_weight * freshness
        - freshness = exp(-age_seconds / half_life_seconds)
        """
        mode = str(mode or "overlap").strip().lower()
        if mode not in {"overlap", "embedding", "ann", "model", "auto"}:
            mode = "overlap"

        try:
            time_weight = float(time_weight)
        except Exception:
            time_weight = 0.15
        time_weight = max(0.0, min(1.0, time_weight))

        try:
            half_life_days = float(half_life_days)
        except Exception:
            half_life_days = 14.0
        half_life_days = max(0.25, half_life_days)

        now_ts = time.time()
        half_life_seconds = half_life_days * 86400.0

        def _freshness(ts: float) -> float:
            age = max(0.0, now_ts - float(ts))
            return math.exp(-age / half_life_seconds)

        def _pack(rec: MemoryRecord, relevance: float) -> dict[str, Any]:
            fr = _freshness(rec.ts)
            final = ((1.0 - time_weight) * relevance) + (time_weight * fr)
            return {
                "score": round(float(final), 4),
                "relevance": round(float(relevance), 4),
                "freshness": round(float(fr), 4),
                "record": rec,
            }

        records = [r for r in self.load_all() if not path or r.path == path]
        if not records:
            return []

        if mode in {"model", "auto"}:
            texts = [f"{r.objective} {r.hint} {r.path} {r.kind} {r.outcome}" for r in records]
            vectors = self._sentence_embeddings([query] + texts)
            if vectors:
                q_vec = vectors[0]
                scored_dense: list[dict[str, Any]] = []
                for i, rec in enumerate(records, start=1):
                    score = self._dot_dense(q_vec, vectors[i])
                    if score <= 0.0:
                        continue
                    scored_dense.append(_pack(rec, float(score)))
                scored_dense.sort(
                    key=lambda x: (x["score"], x["relevance"], x["record"].ts),
                    reverse=True,
                )
                return scored_dense[: max(1, int(limit))]
            if mode == "model":
                return []

        q_tokens = self._tokenize(query)
        if not q_tokens and mode != "embedding":
            return []

        query_emb = self._hashed_embedding(query) if mode in {"embedding", "ann"} else {}
        if mode in {"embedding", "ann"} and not query_emb:
            return []

        scored: list[dict[str, Any]] = []
        if mode == "embedding":
            self.ensure_hash_index()
            indexed = self._iter_hash_index()
            if indexed:
                for rec, rec_emb in indexed:
                    if path and rec.path != path:
                        continue
                    score = self._cosine_sparse(query_emb, rec_emb)
                    if score <= 0.0:
                        continue
                    scored.append(_pack(rec, float(score)))
                scored.sort(
                    key=lambda x: (x["score"], x["relevance"], x["record"].ts),
                    reverse=True,
                )
                return scored[: max(1, int(limit))]

        if mode == "ann":
            self.ensure_hash_index()
            q_key = self._ann_key(query_emb)
            candidates = self._ann_candidates(q_key, path=path)
            q_tokens_ann = self._tokenize(query)
            for rec, rec_emb in candidates:
                score = self._cosine_sparse(query_emb, rec_emb)
                if score <= 0.0:
                    # Robust fallback: derive a weak relevance from token overlap
                    hay = self._record_text(rec)
                    r_tokens = self._tokenize(hay)
                    inter = len(q_tokens_ann & r_tokens)
                    union = len(q_tokens_ann | r_tokens)
                    score = (inter / union) if (inter > 0 and union > 0) else 0.0
                if score > 0.0:
                    scored.append(_pack(rec, float(score)))
            scored.sort(
                key=lambda x: (x["score"], x["relevance"], x["record"].ts),
                reverse=True,
            )
            return scored[: max(1, int(limit))]

        for rec in records:
            hay = f"{rec.objective} {rec.hint} {rec.path} {rec.kind} {rec.outcome}"

            if mode == "embedding":
                rec_emb = self._hashed_embedding(hay)
                score = self._cosine_sparse(query_emb, rec_emb)
                if score <= 0.0:
                    continue
            else:
                r_tokens = self._tokenize(hay)
                if not r_tokens:
                    continue
                inter = len(q_tokens & r_tokens)
                if inter == 0:
                    continue
                union = len(q_tokens | r_tokens)
                score = inter / union if union else 0.0

            scored.append(
                _pack(rec, score)
            )

        scored.sort(
            key=lambda x: (x["score"], x["relevance"], x["record"].ts),
            reverse=True,
        )
        return scored[: max(1, int(limit))]
