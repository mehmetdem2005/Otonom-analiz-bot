import json
import tempfile
import unittest
from pathlib import Path

from react_tools import build_default_registry


class ReactToolsTests(unittest.IsolatedAsyncioTestCase):
    async def test_file_read_write_inside_repo(self):
        with tempfile.TemporaryDirectory() as td:
            reg = build_default_registry(Path(td))
            writer = reg.get("dosya_yaz")
            reader = reg.get("dosya_oku")

            msg = await writer.execute({"path": "notes/a.txt", "content": "hello"})
            payload = json.loads(msg)
            self.assertEqual(payload.get("status"), "ok")
            self.assertEqual(payload.get("path"), "notes/a.txt")
            content = await reader.execute({"path": "notes/a.txt"})
            self.assertEqual(content, "hello")

    async def test_file_write_blocks_path_escape(self):
        with tempfile.TemporaryDirectory() as td:
            reg = build_default_registry(Path(td))
            writer = reg.get("dosya_yaz")

            with self.assertRaises(PermissionError):
                await writer.execute({"path": "../evil.txt", "content": "x"})

    async def test_file_read_line_window(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            reg = build_default_registry(base)
            writer = reg.get("dosya_yaz")
            reader = reg.get("dosya_oku")

            content = "\n".join([f"line-{i}" for i in range(1, 21)])
            await writer.execute({"path": "notes/window.txt", "content": content})
            excerpt = await reader.execute({"path": "notes/window.txt", "start_line": 5, "end_line": 7})

            self.assertEqual(excerpt, "line-5\nline-6\nline-7")

    async def test_python_tool_blocks_dangerous_code(self):
        with tempfile.TemporaryDirectory() as td:
            reg = build_default_registry(Path(td))
            runner = reg.get("kod_calistir")
            out = await runner.execute({"code": "import os\nprint('x')"})
            self.assertIn("güvenlik", out.lower())

    async def test_test_calistir_rejects_unknown_framework(self):
        with tempfile.TemporaryDirectory() as td:
            reg = build_default_registry(Path(td))
            test_tool = reg.get("test_calistir")
            out = await test_tool.execute({"framework": "unknown"})
            self.assertIn("framework", out)

    async def test_test_calistir_returns_json_payload(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "tests").mkdir(parents=True)
            (base / "tests" / "__init__.py").write_text("", encoding="utf-8")
            (base / "tests" / "test_smoke.py").write_text(
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertTrue(True)\n",
                encoding="utf-8",
            )

            reg = build_default_registry(base)
            test_tool = reg.get("test_calistir")
            raw = await test_tool.execute({"framework": "unittest", "pattern": "test_smoke.py", "timeout": 20})
            payload = json.loads(raw)
            self.assertEqual(payload.get("returncode"), 0)

    async def test_file_write_creates_backup_and_rollback(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            reg = build_default_registry(base)
            writer = reg.get("dosya_yaz")
            rollback = reg.get("dosya_geri_al")
            reader = reg.get("dosya_oku")

            await writer.execute({"path": "notes/b.txt", "content": "v1"})
            raw2 = await writer.execute({"path": "notes/b.txt", "content": "v2"})
            payload2 = json.loads(raw2)
            backup = payload2.get("backupPath")
            self.assertTrue(backup)

            cur = await reader.execute({"path": "notes/b.txt"})
            self.assertEqual(cur, "v2")

            rb = await rollback.execute({"path": "notes/b.txt", "backup_path": backup})
            rb_payload = json.loads(rb)
            self.assertEqual(rb_payload.get("status"), "rolled_back")
            after = await reader.execute({"path": "notes/b.txt"})
            self.assertEqual(after, "v1")

    async def test_hafiza_oku_no_memory_store(self):
        """MemoryStore olmadan hafiza_oku hata mesajı döndürmeli."""
        with tempfile.TemporaryDirectory() as td:
            reg = build_default_registry(Path(td), memory_store=None)
            tool = reg.get("hafiza_oku")
            result = await tool.execute({})
            self.assertEqual(result["status"], "error")

    async def test_hafiza_yaz_no_memory_store(self):
        """MemoryStore olmadan hafiza_yaz hata mesajı döndürmeli."""
        with tempfile.TemporaryDirectory() as td:
            reg = build_default_registry(Path(td), memory_store=None)
            tool = reg.get("hafiza_yaz")
            result = await tool.execute({"event": "fix_attempt", "path": "x.py", "success": True})
            self.assertEqual(result["status"], "error")

    async def test_hafiza_yaz_ve_oku_entegrasyon(self):
        """hafiza_yaz ile kaydedilen kayıt hafiza_oku ile görünmeli."""
        import tempfile
        from memory_store import MemoryStore

        with tempfile.TemporaryDirectory() as td:
            mem = MemoryStore(Path(td) / "mem.jsonl")
            reg = build_default_registry(Path(td), memory_store=mem)
            yaz = reg.get("hafiza_yaz")
            oku = reg.get("hafiza_oku")

            await yaz.execute({"event": "fix_attempt", "path": "foo.py", "hint": "test hatası", "success": False})
            result = await oku.execute({"path": "foo.py", "limit": 10})
            self.assertEqual(result["status"], "ok")
            self.assertGreaterEqual(result["count"], 1)
            hints = [r["hint"] for r in result["records"]]
            self.assertTrue(any("test hatası" in h for h in hints))

    async def test_hafiza_ara_entegrasyon(self):
        """hafiza_ara semantik olarak ilgili kaydı döndürmeli."""
        from memory_store import MemoryStore

        with tempfile.TemporaryDirectory() as td:
            mem = MemoryStore(Path(td) / "mem.jsonl")
            reg = build_default_registry(Path(td), memory_store=mem)
            yaz = reg.get("hafiza_yaz")
            ara = reg.get("hafiza_ara")

            await yaz.execute(
                {
                    "event": "fix_attempt",
                    "path": "services/api.py",
                    "hint": "timeout ve retry problemi",
                    "objective": "api timeout test fix",
                    "success": False,
                }
            )
            result = await ara.execute({"query": "api timeout retry", "limit": 5})
            self.assertEqual(result["status"], "ok")
            self.assertGreaterEqual(result["count"], 1)
            self.assertEqual(result["results"][0]["record"]["path"], "services/api.py")

    async def test_hafiza_ara_mode_passthrough(self):
        """hafiza_ara mode=auto parametresini kabul edip döndürmeli."""
        from memory_store import MemoryStore

        with tempfile.TemporaryDirectory() as td:
            mem = MemoryStore(Path(td) / "mem.jsonl")
            reg = build_default_registry(Path(td), memory_store=mem)
            yaz = reg.get("hafiza_yaz")
            ara = reg.get("hafiza_ara")

            await yaz.execute(
                {
                    "event": "fix_attempt",
                    "path": "services/cache.py",
                    "hint": "cache timeout",
                    "objective": "cache timeout fix",
                    "success": False,
                }
            )
            result = await ara.execute({"query": "cache timeout", "limit": 5, "mode": "auto"})
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["mode"], "auto")
            self.assertGreaterEqual(result["count"], 1)

    async def test_hafiza_ara_time_weight_passthrough(self):
        """hafiza_ara time_weight ve half_life_days parametrelerini döndürmeli."""
        from memory_store import MemoryStore

        with tempfile.TemporaryDirectory() as td:
            mem = MemoryStore(Path(td) / "mem.jsonl")
            reg = build_default_registry(Path(td), memory_store=mem)
            yaz = reg.get("hafiza_yaz")
            ara = reg.get("hafiza_ara")

            await yaz.execute(
                {
                    "event": "fix_attempt",
                    "path": "services/search.py",
                    "hint": "semantic timeout",
                    "objective": "search timeout fix",
                    "success": False,
                }
            )
            result = await ara.execute(
                {
                    "query": "search timeout",
                    "limit": 5,
                    "mode": "auto",
                    "time_weight": 0.3,
                    "half_life_days": 7,
                }
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["time_weight"], 0.3)
            self.assertEqual(result["half_life_days"], 7.0)
            self.assertGreaterEqual(result["count"], 1)


if __name__ == "__main__":
    unittest.main()
