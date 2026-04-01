"""JSONL trace storage for agent decision/action timeline."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import aiofiles


class TraceStore:
    def __init__(self, base_dir: Path | str = "log") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _trace_file(self) -> Path:
        return self.base_dir / f"agent_trace_{datetime.now().strftime('%Y%m%d')}.jsonl"

    async def append(self, event: dict[str, Any]) -> None:
        payload = {
            "ts": datetime.now().isoformat(),
            **event,
        }
        async with aiofiles.open(self._trace_file(), "a", encoding="utf-8") as f:
            await f.write(json.dumps(payload, ensure_ascii=False) + "\n")
