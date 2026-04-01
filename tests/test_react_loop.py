import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from agent_core import AgentCore, AgentState
from tool_registry import ToolRegistry, ToolDefinition
from trace_store import TraceStore


class AgentCoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_completes_with_tool_then_final(self):
        calls = {"n": 0}

        async def llm(history, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return {
                    "actions": [
                        {
                            "name": "echo",
                            "args": {"text": "merhaba"},
                            "timeout_sec": 2,
                            "retry": 0,
                            "safety_level": "low",
                        }
                    ]
                }
            return {"final": "tamamlandı"}

        async def echo_tool(args):
            return f"echo:{args.get('text', '')}"

        registry = ToolRegistry()
        registry.register(ToolDefinition(name="echo", description="echo", execute=echo_tool))

        with tempfile.TemporaryDirectory() as td:
            core = AgentCore(llm, registry, trace_store=TraceStore(Path(td)), max_iterations=4)
            ctx = await core.run("test objective")

        self.assertEqual(ctx.state, AgentState.DONE)
        self.assertEqual(ctx.final_answer, "tamamlandı")
        self.assertTrue(any(r["tool"] == "echo" and r["ok"] for r in ctx.tool_results))

    async def test_retry_on_tool_error(self):
        tool_calls = {"n": 0}

        async def llm(history, tools):
            if not any(item.get("role") == "tool" for item in history):
                return {"actions": [{"name": "flaky", "args": {}, "retry": 1, "timeout_sec": 1}]}
            return {"final": "done"}

        async def flaky(_args):
            tool_calls["n"] += 1
            if tool_calls["n"] == 1:
                raise RuntimeError("ilk deneme hata")
            return "ok"

        registry = ToolRegistry()
        registry.register(ToolDefinition(name="flaky", description="flaky", execute=flaky))

        core = AgentCore(llm, registry, max_iterations=4)
        ctx = await core.run("retry objective")

        self.assertEqual(ctx.state, AgentState.DONE)
        self.assertEqual(tool_calls["n"], 2)

    async def test_fails_on_bad_llm_json(self):
        async def llm(history, tools):
            return "not-json"

        registry = ToolRegistry()
        core = AgentCore(llm, registry, max_iterations=1)

        with self.assertRaises(json.JSONDecodeError):
            await core.run("bad output")

    async def test_confirmation_policy_denies_high_risk_tool(self):
        async def llm(history, tools):
            if not any(item.get("role") == "tool" for item in history):
                return {"actions": [{"name": "danger", "args": {}, "retry": 0, "timeout_sec": 1}]}
            return {"final": "done"}

        async def danger_tool(_args):
            return "never-should-run"

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="danger",
                description="high risk",
                execute=danger_tool,
                requires_confirmation=True,
                risk_level="high",
            )
        )

        def deny_all(action, tool):
            return False

        core = AgentCore(llm, registry, confirmation_policy=deny_all, max_iterations=4)
        ctx = await core.run("danger objective")

        self.assertEqual(ctx.state, AgentState.DONE)
        denied = [r for r in ctx.tool_results if r.get("tool") == "danger"]
        self.assertTrue(denied)
        self.assertFalse(denied[0]["ok"])
        self.assertIn("engellendi", denied[0]["error"])

    async def test_bugfix_objective_injects_test_gate(self):
        calls = {"n": 0}

        async def llm(history, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"final": "erken final"}
            return {"final": "gercek final"}

        async def test_tool(_args):
            return "test-ok"

        registry = ToolRegistry()
        registry.register(ToolDefinition(name="test_calistir", description="run tests", execute=test_tool))

        core = AgentCore(llm, registry, max_iterations=4)
        ctx = await core.run("bug fix yap ve tamamla")

        self.assertEqual(ctx.state, AgentState.DONE)
        self.assertEqual(ctx.final_answer, "gercek final")
        self.assertTrue(any(r.get("tool") == "test_calistir" for r in ctx.tool_results))

    async def test_failed_test_gate_rejects_final_until_pass(self):
        calls = {"n": 0}
        test_calls = {"n": 0}

        async def llm(history, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"actions": [{"name": "test_calistir", "args": {}, "timeout_sec": 2, "retry": 0}]}
            if calls["n"] == 2:
                return {"final": "erken-final"}
            if calls["n"] == 3:
                return {"actions": [{"name": "test_calistir", "args": {}, "timeout_sec": 2, "retry": 0}]}
            return {"final": "dogru-final"}

        async def test_tool(_args):
            test_calls["n"] += 1
            if test_calls["n"] == 1:
                return json.dumps({"returncode": 1, "stdout": "", "stderr": "fail"}, ensure_ascii=False)
            return json.dumps({"returncode": 0, "stdout": "ok", "stderr": ""}, ensure_ascii=False)

        registry = ToolRegistry()
        registry.register(ToolDefinition(name="test_calistir", description="run tests", execute=test_tool))

        core = AgentCore(llm, registry, max_iterations=8)
        ctx = await core.run("bug fix ve duzelt")

        self.assertEqual(ctx.state, AgentState.DONE)
        self.assertEqual(ctx.final_answer, "dogru-final")
        self.assertGreaterEqual(test_calls["n"], 2)

    async def test_failed_test_auto_appends_retest(self):
        calls = {"n": 0}
        test_calls = {"n": 0}

        async def llm(history, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"actions": [{"name": "test_calistir", "args": {}, "timeout_sec": 2, "retry": 0}]}
            if calls["n"] == 2:
                # Bilerek test_calistir olmadan düzeltme aksiyonu döndürüyoruz.
                return {"actions": [{"name": "dosya_yaz", "args": {"path": "x.txt", "content": "fix"}, "timeout_sec": 2, "retry": 0}]}
            return {"final": "ok-final"}

        async def test_tool(_args):
            test_calls["n"] += 1
            if test_calls["n"] == 1:
                return json.dumps({"returncode": 1, "stdout": "", "stderr": "fail"}, ensure_ascii=False)
            return json.dumps({"returncode": 0, "stdout": "ok", "stderr": ""}, ensure_ascii=False)

        async def write_tool(_args):
            return "written"

        registry = ToolRegistry()
        registry.register(ToolDefinition(name="test_calistir", description="run tests", execute=test_tool))
        registry.register(ToolDefinition(name="dosya_yaz", description="write", execute=write_tool))

        core = AgentCore(llm, registry, max_iterations=8)
        ctx = await core.run("bug fix et")

        self.assertEqual(ctx.state, AgentState.DONE)
        self.assertEqual(ctx.final_answer, "ok-final")
        # İlk fail + otomatik append edilen retest => en az 2 test koşmalı.
        self.assertGreaterEqual(test_calls["n"], 2)

    async def test_failed_test_appends_candidate_file_read(self):
        calls = {"n": 0}
        reads = {"n": 0}
        tests = {"n": 0}

        async def llm(history, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"actions": [{"name": "test_calistir", "args": {}, "timeout_sec": 2, "retry": 0}]}
            if calls["n"] == 2:
                return {"actions": [{"name": "dosya_yaz", "args": {"path": "x.py", "content": "fix"}, "timeout_sec": 2, "retry": 0}]}
            return {"final": "done"}

        async def test_tool(_args):
            tests["n"] += 1
            if tests["n"] == 1:
                return json.dumps(
                    {
                        "returncode": 1,
                        "stdout": "",
                        "stderr": (
                            'Traceback\nFile "app/main.py", line 12\nAssertionError\n'
                            'File "app/utils.py", line 3\nValueError\n'
                            'File "app/core.py", line 8\nRuntimeError'
                        ),
                    },
                    ensure_ascii=False,
                )
            return json.dumps({"returncode": 0, "stdout": "ok", "stderr": ""}, ensure_ascii=False)

        async def read_tool(args):
            reads["n"] += 1
            self.assertIn("start_line", args)
            self.assertIn("end_line", args)
            return "file-content"

        async def write_tool(_args):
            return "written"

        registry = ToolRegistry()
        registry.register(ToolDefinition(name="test_calistir", description="run tests", execute=test_tool))
        registry.register(ToolDefinition(name="dosya_oku", description="read", execute=read_tool))
        registry.register(ToolDefinition(name="dosya_yaz", description="write", execute=write_tool))

        core = AgentCore(llm, registry, max_iterations=8)
        ctx = await core.run("bug fix et")

        self.assertEqual(ctx.state, AgentState.DONE)
        self.assertEqual(ctx.final_answer, "done")
        self.assertGreaterEqual(reads["n"], 3)
        self.assertGreaterEqual(tests["n"], 2)

    def test_extract_fix_candidates_ranking(self):
        tool_results = [
            {
                "tool": "test_calistir",
                "ok": True,
                "output": json.dumps(
                    {
                        "returncode": 1,
                        "stderr": (
                            'File "tests/test_alpha.py", line 11\n'
                            'File "app/core.py", line 44\n'
                            'File "app/core.py", line 44\n'
                        ),
                        "stdout": "",
                    },
                    ensure_ascii=False,
                ),
            }
        ]

        ranked = AgentCore._extract_fix_candidates(tool_results)
        self.assertTrue(ranked)
        self.assertEqual(ranked[0]["path"], "app/core.py")
        self.assertEqual(ranked[0]["line"], 44)

    def test_build_min_cost_fix_plan_contains_ranked_targets(self):
        plan = AgentCore._build_min_cost_fix_plan(
            [
                {"path": "app/core.py", "line": 44, "score": 13},
                {"path": "app/utils.py", "line": 9, "score": 10},
            ],
            "bug fix et",
        )
        self.assertIn("Plan:", plan)
        self.assertIn("app/core.py:44", plan)
        self.assertIn("app/utils.py:9", plan)
        self.assertIn("test_calistir", plan)

    def test_build_structured_fix_plan_schema(self):
        plan = AgentCore._build_structured_fix_plan(
            [
                {"path": "app/core.py", "line": 44, "score": 13},
                {"path": "tests/test_core.py", "line": 10, "score": 9},
            ],
            "bug fix et",
        )
        self.assertEqual(plan["schema_version"], "1.0")
        self.assertEqual(plan["strategy"], "minimal_cost_fix")
        self.assertEqual(plan["scope"], "code_test_alignment")
        self.assertTrue(plan["targets"])
        self.assertEqual(plan["targets"][0]["path"], "app/core.py")
        self.assertEqual(plan["steps"][0]["tool"], "dosya_oku")
        self.assertEqual(plan["steps"][1]["tool"], "dosya_yaz")
        self.assertEqual(plan["steps"][3]["tool"], "test_calistir")

    def test_validate_plan_json_passes_valid(self):
        plan = AgentCore._build_structured_fix_plan(
            [{"path": "app/core.py", "line": 10, "score": 5}],
            "fix",
        )
        errors = AgentCore._validate_plan_json(plan)
        self.assertEqual(errors, [])

    def test_validate_plan_json_catches_bad_schema(self):
        bad = {"schema_version": "99.0", "strategy": "unknown", "targets": "nope", "steps": []}
        errors = AgentCore._validate_plan_json(bad)
        self.assertTrue(len(errors) >= 3)

    async def test_auto_rollback_applied_when_inline_narrow_test_fails(self):
        calls = {"n": 0}
        tests = {"n": 0}
        rollbacks = {"n": 0}
        reads = {"n": 0}

        async def llm(history, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return {
                    "actions": [
                        {
                            "name": "dosya_yaz",
                            "args": {"path": "services/core.py", "content": "print('x')"},
                            "timeout_sec": 2,
                            "retry": 0,
                        }
                    ]
                }
            if calls["n"] == 2:
                return {
                    "actions": [
                        {"name": "test_calistir", "args": {}, "timeout_sec": 2, "retry": 0}
                    ]
                }
            return {"final": "done"}

        async def write_tool(_args):
            return json.dumps(
                {
                    "status": "ok",
                    "path": "services/core.py",
                    "backupPath": "temp/backup/services/core.py.bak",
                    "diff": {"addedLines": 1, "removedLines": 1},
                },
                ensure_ascii=False,
            )

        async def test_tool(args):
            tests["n"] += 1
            if tests["n"] == 1:
                self.assertEqual(args.get("pattern"), "test*core*.py")
                return json.dumps(
                    {
                        "returncode": 1,
                        "stdout": "",
                        "stderr": 'Traceback\nFile "services/core.py", line 7\nAssertionError: fail',
                    },
                    ensure_ascii=False,
                )
            return json.dumps({"returncode": 0, "stdout": "ok", "stderr": ""}, ensure_ascii=False)

        async def rollback_tool(args):
            rollbacks["n"] += 1
            self.assertEqual(args.get("path"), "services/core.py")
            self.assertEqual(args.get("backup_path"), "temp/backup/services/core.py.bak")
            return "rollback-ok"

        async def read_tool(args):
            reads["n"] += 1
            self.assertEqual(args.get("path"), "services/core.py")
            self.assertIn("start_line", args)
            self.assertIn("end_line", args)
            return "context-read"

        registry = ToolRegistry()
        registry.register(ToolDefinition(name="dosya_yaz", description="write", execute=write_tool))
        registry.register(ToolDefinition(name="test_calistir", description="test", execute=test_tool))
        registry.register(ToolDefinition(name="dosya_geri_al", description="rollback", execute=rollback_tool))
        registry.register(ToolDefinition(name="dosya_oku", description="read", execute=read_tool))

        with tempfile.TemporaryDirectory() as td:
            core = AgentCore(llm, registry, trace_store=TraceStore(Path(td)), max_iterations=6)
            ctx = await core.run("bug fix et")

        self.assertEqual(ctx.state, AgentState.DONE)
        self.assertEqual(ctx.final_answer, "done")
        self.assertEqual(tests["n"], 2)
        self.assertEqual(rollbacks["n"], 1)
        self.assertGreaterEqual(reads["n"], 1)
        self.assertTrue(any("Kök neden özeti" in str(h.get("content", "")) for h in ctx.history))
        self.assertTrue(any(str(h.get("content", "")).startswith("PlanJSON:") for h in ctx.history))

    async def test_no_auto_rollback_when_inline_narrow_test_passes(self):
        calls = {"n": 0}
        rollbacks = {"n": 0}

        async def llm(history, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return {
                    "actions": [
                        {
                            "name": "dosya_yaz",
                            "args": {"path": "app/service.py", "content": "x=1"},
                            "timeout_sec": 2,
                            "retry": 0,
                        }
                    ]
                }
            return {"final": "done"}

        async def write_tool(_args):
            return json.dumps(
                {
                    "status": "ok",
                    "path": "app/service.py",
                    "backupPath": "temp/backup/app/service.py.bak",
                    "diff": {"addedLines": 1, "removedLines": 0},
                },
                ensure_ascii=False,
            )

        async def test_tool(_args):
            return json.dumps({"returncode": 0, "stdout": "ok", "stderr": ""}, ensure_ascii=False)

        async def rollback_tool(_args):
            rollbacks["n"] += 1
            return "rollback-ok"

        registry = ToolRegistry()
        registry.register(ToolDefinition(name="dosya_yaz", description="write", execute=write_tool))
        registry.register(ToolDefinition(name="test_calistir", description="test", execute=test_tool))
        registry.register(ToolDefinition(name="dosya_geri_al", description="rollback", execute=rollback_tool))

        core = AgentCore(llm, registry, max_iterations=6)
        ctx = await core.run("bug fix et")

        self.assertEqual(ctx.state, AgentState.DONE)
        self.assertEqual(ctx.final_answer, "done")
        self.assertEqual(rollbacks["n"], 0)

    async def test_auto_rollback_on_broad_test_fail_when_enabled(self):
        calls = {"n": 0}
        tests = {"n": 0}
        rollbacks = {"n": 0}

        async def llm(history, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return {
                    "actions": [
                        {
                            "name": "dosya_yaz",
                            "args": {"path": "app/engine.py", "content": "x=2"},
                            "timeout_sec": 2,
                            "retry": 0,
                        }
                    ]
                }
            if calls["n"] == 2:
                return {
                    "actions": [
                        {"name": "test_calistir", "args": {}, "timeout_sec": 2, "retry": 0}
                    ]
                }
            return {"final": "done"}

        async def write_tool(_args):
            return json.dumps(
                {
                    "status": "ok",
                    "path": "app/engine.py",
                    "backupPath": "temp/backup/app/engine.py.bak",
                    "diff": {"addedLines": 1, "removedLines": 0},
                },
                ensure_ascii=False,
            )

        async def test_tool(args):
            tests["n"] += 1
            if tests["n"] == 1:
                self.assertEqual(args.get("pattern"), "test*engine*.py")
                return json.dumps({"returncode": 0, "stdout": "narrow ok", "stderr": ""}, ensure_ascii=False)
            if tests["n"] == 2:
                self.assertEqual(args.get("pattern"), "test_*.py")
                return json.dumps({"returncode": 1, "stdout": "", "stderr": "broad fail"}, ensure_ascii=False)
            return json.dumps({"returncode": 0, "stdout": "ok", "stderr": ""}, ensure_ascii=False)

        async def rollback_tool(args):
            rollbacks["n"] += 1
            self.assertEqual(args.get("path"), "app/engine.py")
            self.assertEqual(args.get("backup_path"), "temp/backup/app/engine.py.bak")
            return "rollback-ok"

        registry = ToolRegistry()
        registry.register(ToolDefinition(name="dosya_yaz", description="write", execute=write_tool))
        registry.register(ToolDefinition(name="test_calistir", description="test", execute=test_tool))
        registry.register(ToolDefinition(name="dosya_geri_al", description="rollback", execute=rollback_tool))

        prev_broad = os.environ.get("AGENT_AUTOTEST_BROAD_AFTER_WRITE")
        prev_broad_rb = os.environ.get("AGENT_AUTOROLLBACK_ON_BROAD_FAIL")
        os.environ["AGENT_AUTOTEST_BROAD_AFTER_WRITE"] = "1"
        os.environ["AGENT_AUTOROLLBACK_ON_BROAD_FAIL"] = "1"
        try:
            core = AgentCore(llm, registry, max_iterations=8)
            ctx = await core.run("bug fix et")
        finally:
            if prev_broad is None:
                os.environ.pop("AGENT_AUTOTEST_BROAD_AFTER_WRITE", None)
            else:
                os.environ["AGENT_AUTOTEST_BROAD_AFTER_WRITE"] = prev_broad
            if prev_broad_rb is None:
                os.environ.pop("AGENT_AUTOROLLBACK_ON_BROAD_FAIL", None)
            else:
                os.environ["AGENT_AUTOROLLBACK_ON_BROAD_FAIL"] = prev_broad_rb

        self.assertEqual(ctx.state, AgentState.DONE)
        self.assertEqual(ctx.final_answer, "done")
        self.assertEqual(rollbacks["n"], 1)
        self.assertGreaterEqual(tests["n"], 3)


if __name__ == "__main__":
    unittest.main()
