"""Default tool set for Phase-1 ReAct agent core."""

from __future__ import annotations

import asyncio
import json
import re
import shlex
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from tool_registry import ToolDefinition, ToolRegistry
from memory_store import MemoryStore


ALLOWED_WRITE_SUFFIXES = {
    ".py", ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".html", ".js", ".css"
}

PYTHON_DANGEROUS_PATTERNS = [
    "import os",
    "import subprocess",
    "os.system(",
    "subprocess.",
    "socket",
    "shutil.rmtree",
    "open('/", 
    "open(\"/",
    "eval(",
    "exec(",
    "__import__(",
]


def _safe_path(base_dir: Path, raw: str) -> Path:
    path = (base_dir / raw).resolve()
    if not str(path).startswith(str(base_dir.resolve())):
        raise PermissionError("Path dışına yazma/okuma engellendi")
    return path


def _backup_dir(base_dir: Path) -> Path:
    d = (base_dir / "temp" / "agent_backups").resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path_slug(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(path))[-120:]


def _simple_line_diff(old_text: str, new_text: str) -> dict[str, int]:
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    old_set = set(old_lines)
    new_set = set(new_lines)
    removed = len([l for l in old_lines if l not in new_set])
    added = len([l for l in new_lines if l not in old_set])
    return {
        "oldLineCount": len(old_lines),
        "newLineCount": len(new_lines),
        "addedLines": added,
        "removedLines": removed,
    }


async def _tool_web_search(args: dict[str, Any]) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "Arama sorgusu boş"

    url = f"https://duckduckgo.com/html/?q={quote(query)}"
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
        r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
    html = r.text

    hits = re.findall(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.IGNORECASE)
    if not hits:
        return "Sonuç bulunamadı"

    rows: list[str] = []
    for href, title in hits[:5]:
        clean_title = re.sub(r"<[^>]+>", "", title)
        rows.append(f"- {clean_title.strip()} | {href}")
    return "\n".join(rows)


async def _tool_fetch_page(args: dict[str, Any]) -> str:
    url = str(args.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        return "Geçersiz URL"

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
    text = re.sub(r"<[^>]+>", " ", r.text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:2500] if text else "Boş içerik"


async def _tool_read_file_factory(base_dir: Path, args: dict[str, Any]) -> str:
    rel_path = str(args.get("path", "")).strip()
    if not rel_path:
        return "path zorunlu"
    path = _safe_path(base_dir, rel_path)
    if not path.exists() or not path.is_file():
        return "Dosya bulunamadı"
    content = path.read_text(encoding="utf-8")

    start_line = args.get("start_line")
    end_line = args.get("end_line")
    if start_line is not None or end_line is not None:
        try:
            s = max(1, int(start_line if start_line is not None else 1))
            lines = content.splitlines()
            e = int(end_line if end_line is not None else len(lines))
            e = min(len(lines), max(s, e))
            excerpt = lines[s - 1:e]
            return "\n".join(excerpt)[:5000]
        except Exception:
            return "Geçersiz satır aralığı"

    return content[:5000]


async def _tool_write_file_factory(base_dir: Path, args: dict[str, Any]) -> str:
    rel_path = str(args.get("path", "")).strip()
    content = str(args.get("content", ""))
    if not rel_path:
        return "path zorunlu"
    path = _safe_path(base_dir, rel_path)
    if path.suffix and path.suffix.lower() not in ALLOWED_WRITE_SUFFIXES:
        return "Bu dosya uzantısına yazma izni yok"

    old_content = ""
    if path.exists() and path.is_file():
        old_content = path.read_text(encoding="utf-8")

    backup_path = ""
    if old_content:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        bpath = _backup_dir(base_dir) / f"{ts}_{_path_slug(path)}.bak"
        bpath.write_text(old_content, encoding="utf-8")
        backup_path = str(bpath.relative_to(base_dir))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    diff = _simple_line_diff(old_content, content)
    payload = {
        "status": "ok",
        "path": rel_path,
        "backupPath": backup_path,
        "diff": diff,
    }
    return json.dumps(payload, ensure_ascii=False)


async def _tool_rollback_file_factory(base_dir: Path, args: dict[str, Any]) -> str:
    rel_path = str(args.get("path", "")).strip()
    backup_path = str(args.get("backup_path", "")).strip()
    if not rel_path or not backup_path:
        return "path ve backup_path zorunlu"

    target = _safe_path(base_dir, rel_path)
    backup = _safe_path(base_dir, backup_path)
    if not backup.exists() or not backup.is_file():
        return "Backup dosyası bulunamadı"

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
    return json.dumps({"status": "rolled_back", "path": rel_path, "backupPath": backup_path}, ensure_ascii=False)


def _python_code_guvenli_mi(code: str) -> bool:
    lower = code.lower()
    return not any(pat in lower for pat in PYTHON_DANGEROUS_PATTERNS)


async def _tool_run_python(args: dict[str, Any]) -> str:
    code = str(args.get("code", "")).strip()
    timeout = float(args.get("timeout", 10))
    if not code:
        return "code zorunlu"
    if not _python_code_guvenli_mi(code):
        return "Kod güvenlik politikası tarafından reddedildi"

    proc = await asyncio.create_subprocess_exec(
        "python3",
        "-c",
        code,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return "Çalıştırma zaman aşımı"

    out = stdout.decode("utf-8", errors="replace").strip()
    err = stderr.decode("utf-8", errors="replace").strip()
    payload = {"returncode": proc.returncode, "stdout": out[:3000], "stderr": err[:3000]}
    return json.dumps(payload, ensure_ascii=False)


async def _tool_run_tests_factory(base_dir: Path, args: dict[str, Any]) -> str:
    framework = str(args.get("framework", "unittest")).strip().lower()
    pattern = str(args.get("pattern", "test_*.py")).strip() or "test_*.py"
    timeout = float(args.get("timeout", 40))

    if framework not in {"unittest", "pytest"}:
        return "framework yalnızca unittest veya pytest olabilir"

    if framework == "unittest":
        cmd = [
            "python3",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-t",
            ".",
            "-p",
            pattern,
            "-v",
        ]
    else:
        target = str(args.get("target", "tests")).strip() or "tests"
        safe_target = _safe_path(base_dir, target)
        cmd = ["pytest", str(safe_target), "-q"]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(base_dir),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return "Test çalıştırma zaman aşımı"

    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    payload = {
        "command": " ".join(shlex.quote(x) for x in cmd),
        "returncode": proc.returncode,
        "stdout": out[-6000:],
        "stderr": err[-3000:],
    }
    return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Hafıza araçları (memory_store yoksa graceful no-op)
# ---------------------------------------------------------------------------

def _make_hafiza_oku(memory: MemoryStore | None):
    async def _tool_hafiza_oku(args: dict) -> dict:
        if memory is None:
            return {"status": "error", "message": "MemoryStore bağlı değil"}
        path = args.get("path", "")
        limit = int(args.get("limit", 10))
        if path:
            records = memory.recall_failures_for_path(path, limit=limit)
        else:
            records = memory.recall_recent(limit=limit)
        rate = memory.success_rate_for_path(path) if path else None
        return {
            "status": "ok",
            "count": len(records),
            "success_rate": rate,
            "records": [
                {
                    "ts": r.ts,
                    "kind": r.kind,
                    "path": r.path,
                    "outcome": r.outcome,
                    "hint": r.hint[:120] if r.hint else "",
                }
                for r in records
            ],
        }
    return _tool_hafiza_oku


def _make_hafiza_yaz(memory: MemoryStore | None):
    async def _tool_hafiza_yaz(args: dict) -> dict:
        if memory is None:
            return {"status": "error", "message": "MemoryStore bağlı değil"}
        event = args.get("event", "fix_attempt")
        path = str(args.get("path", ""))
        hint = str(args.get("hint", ""))
        success = bool(args.get("success", False))
        objective = str(args.get("objective", "manual"))
        if event == "rollback":
            memory.record_rollback(objective=objective, path=path, hint=hint)
        else:
            memory.record_fix_attempt(
                objective=objective,
                path=path,
                line=int(args.get("line", 0)),
                outcome="success" if success else "failure",
                hint=hint,
            )
        return {"status": "ok", "recorded": event, "path": path}
    return _tool_hafiza_yaz


def _make_hafiza_ara(memory: MemoryStore | None):
    async def _tool_hafiza_ara(args: dict) -> dict:
        if memory is None:
            return {"status": "error", "message": "MemoryStore bağlı değil"}
        query = str(args.get("query", "")).strip()
        if not query:
            return {"status": "error", "message": "query zorunlu"}
        limit = int(args.get("limit", 10))
        path = str(args.get("path", "")).strip()
        mode = str(args.get("mode", "auto")).strip().lower() or "auto"
        time_weight = float(args.get("time_weight", 0.15))
        half_life_days = float(args.get("half_life_days", 14.0))
        results = memory.semantic_search(
            query=query,
            limit=limit,
            path=path,
            mode=mode,
            time_weight=time_weight,
            half_life_days=half_life_days,
        )
        return {
            "status": "ok",
            "mode": mode,
            "time_weight": time_weight,
            "half_life_days": half_life_days,
            "count": len(results),
            "results": [
                {
                    "score": x["score"],
                    "relevance": x.get("relevance"),
                    "freshness": x.get("freshness"),
                    "record": {
                        "ts": x["record"].ts,
                        "kind": x["record"].kind,
                        "outcome": x["record"].outcome,
                        "path": x["record"].path,
                        "line": x["record"].line,
                        "hint": x["record"].hint,
                        "objective": x["record"].objective,
                    },
                }
                for x in results
            ],
        }
    return _tool_hafiza_ara


def build_default_registry(base_dir: Path | str = ".", memory_store: MemoryStore | None = None) -> ToolRegistry:
    base = Path(base_dir).resolve()
    reg = ToolRegistry()

    reg.register(
        ToolDefinition(
            name="web_ara",
            description="Web araması yapar ve ilk sonuçları döndürür",
            execute=_tool_web_search,
            risk_level="medium",
        )
    )
    reg.register(
        ToolDefinition(
            name="sayfa_oku",
            description="Verilen URL içeriğini özet metin olarak döndürür",
            execute=_tool_fetch_page,
            risk_level="medium",
        )
    )
    reg.register(
        ToolDefinition(
            name="dosya_oku",
            description="Repo içinden dosya okur",
            execute=lambda args: _tool_read_file_factory(base, args),
            risk_level="low",
        )
    )
    reg.register(
        ToolDefinition(
            name="dosya_yaz",
            description="Repo içinde dosyaya yazar",
            execute=lambda args: _tool_write_file_factory(base, args),
            risk_level="high",
            requires_confirmation=True,
        )
    )
    reg.register(
        ToolDefinition(
            name="dosya_geri_al",
            description="Önceki backup dosyasından hedef dosyayı geri alır",
            execute=lambda args: _tool_rollback_file_factory(base, args),
            risk_level="high",
            requires_confirmation=True,
        )
    )
    reg.register(
        ToolDefinition(
            name="kod_calistir",
            description="Kısa Python kodunu çalıştırır ve çıktıyı döndürür",
            execute=_tool_run_python,
            risk_level="high",
            requires_confirmation=True,
        )
    )
    reg.register(
        ToolDefinition(
            name="test_calistir",
            description="Test setini çalıştırır (unittest/pytest)",
            execute=lambda args: _tool_run_tests_factory(base, args),
            risk_level="medium",
            requires_confirmation=True,
        )
    )
    reg.register(
        ToolDefinition(
            name="hafiza_oku",
            description="Ajan hafızasından geçmiş başarı/başarısızlık kayıtlarını okur",
            execute=_make_hafiza_oku(memory_store),
            risk_level="low",
        )
    )
    reg.register(
        ToolDefinition(
            name="hafiza_yaz",
            description="Ajan hafızasına yeni bir fix/rollback olayı kaydeder",
            execute=_make_hafiza_yaz(memory_store),
            risk_level="low",
        )
    )
    reg.register(
        ToolDefinition(
            name="hafiza_ara",
            description="Ajan hafızasında semantik benzerlik ile kayıt arar",
            execute=_make_hafiza_ara(memory_store),
            risk_level="low",
        )
    )
    return reg
