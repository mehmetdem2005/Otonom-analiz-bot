"""
Sohbet Oturum Yöneticisi
- Çok turlu konuşma geçmişi (in-memory + opsiyonel JSONL kalıcılık)
- Her session bir UUID, başlık ve mesaj listesinden oluşur
- Thread-safe: asyncio.Lock ile korunan yazma işlemleri
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


_SESSIONS_DIR = Path(os.getenv("SESSIONS_DIR", "memory/sessions"))
_MAX_SESSIONS = int(os.getenv("MAX_CHAT_SESSIONS", "100"))
_MAX_MESSAGES_PER_SESSION = int(os.getenv("MAX_MESSAGES_PER_SESSION", "500"))


@dataclass
class Message:
    role: str          # "user" | "assistant" | "system" | "tool"
    content: str
    ts: float = field(default_factory=time.time)
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result: Any | None = None

    def to_llm_dict(self) -> dict:
        """LLM API'sine gönderilecek format (role + content)."""
        return {"role": self.role, "content": self.content}


@dataclass
class Session:
    id: str
    title: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    messages: list[Message] = field(default_factory=list)
    model: str = ""
    provider: str = ""

    def summary(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": len(self.messages),
            "model": self.model,
            "provider": self.provider,
        }

    def llm_messages(self) -> list[dict]:
        """LLM API'sine gönderilecek mesaj listesi (system hariç)."""
        return [m.to_llm_dict() for m in self.messages
                if m.role in ("user", "assistant") and m.content.strip()]


class SessionStore:
    """Thread-safe oturum deposu. In-memory, opsiyonel disk kalıcılığı."""

    def __init__(self, persist: bool = True):
        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()
        self._persist = persist
        if persist:
            _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
            self._load_from_disk()

    # ── CRUD ──────────────────────────────────────────────────────────────

    async def create(self, title: str = "Yeni Konuşma", model: str = "", provider: str = "") -> Session:
        async with self._lock:
            # Eski session'ları temizle (limit aşımı)
            if len(self._sessions) >= _MAX_SESSIONS:
                oldest = sorted(self._sessions.values(), key=lambda s: s.updated_at)[0]
                del self._sessions[oldest.id]

            sid = str(uuid.uuid4())
            s = Session(id=sid, title=title, model=model, provider=provider)
            self._sessions[sid] = s
            return s

    async def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    async def list_sessions(self) -> list[dict]:
        sessions = sorted(self._sessions.values(), key=lambda s: s.updated_at, reverse=True)
        return [s.summary() for s in sessions]

    async def delete(self, session_id: str) -> bool:
        async with self._lock:
            if session_id not in self._sessions:
                return False
            del self._sessions[session_id]
            if self._persist:
                p = _SESSIONS_DIR / f"{session_id}.jsonl"
                if p.exists():
                    p.unlink(missing_ok=True)
            return True

    async def add_message(self, session_id: str, role: str, content: str, **kwargs) -> Message | None:
        async with self._lock:
            s = self._sessions.get(session_id)
            if not s:
                return None
            if len(s.messages) >= _MAX_MESSAGES_PER_SESSION:
                # En eski user/assistant çiftini sil (baştan)
                s.messages = s.messages[2:]
            msg = Message(role=role, content=content, **kwargs)
            s.messages.append(msg)
            s.updated_at = msg.ts
            # İlk user mesajından başlık türet
            if role == "user" and s.title == "Yeni Konuşma":
                s.title = content[:60].strip().replace("\n", " ")
            if self._persist:
                self._append_to_disk(s.id, msg)
            return msg

    async def update_last_assistant(self, session_id: str, content: str) -> None:
        """Streaming sırasında son assistant mesajını güncelle."""
        async with self._lock:
            s = self._sessions.get(session_id)
            if not s:
                return
            # Son mesaj assistant ise güncelle, yoksa ekle
            if s.messages and s.messages[-1].role == "assistant":
                s.messages[-1].content = content
                s.messages[-1].ts = time.time()
            else:
                s.messages.append(Message(role="assistant", content=content))
            s.updated_at = time.time()

    # ── Kalıcılık ─────────────────────────────────────────────────────────

    def _append_to_disk(self, session_id: str, msg: Message) -> None:
        try:
            path = _SESSIONS_DIR / f"{session_id}.jsonl"
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(msg), ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _load_from_disk(self) -> None:
        """Başlangıçta disk'teki session'ları yükle."""
        try:
            for p in sorted(_SESSIONS_DIR.glob("*.jsonl"),
                            key=lambda x: x.stat().st_mtime, reverse=True)[:_MAX_SESSIONS]:
                sid = p.stem
                messages: list[Message] = []
                try:
                    with p.open(encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            d = json.loads(line)
                            messages.append(Message(**d))
                except Exception:
                    continue
                if not messages:
                    continue
                title = "Yeni Konuşma"
                for m in messages:
                    if m.role == "user" and m.content.strip():
                        title = m.content[:60].strip().replace("\n", " ")
                        break
                s = Session(
                    id=sid,
                    title=title,
                    created_at=messages[0].ts if messages else time.time(),
                    updated_at=messages[-1].ts if messages else time.time(),
                    messages=messages,
                )
                self._sessions[sid] = s
        except Exception:
            pass


# Singleton (web_arayuzu.py ile paylaşılacak)
_store: SessionStore | None = None


def get_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore(persist=True)
    return _store
