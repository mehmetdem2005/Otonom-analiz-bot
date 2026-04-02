"""Phase-1 agent core: deterministic state machine + tool-calling loop."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

from action_executor import ActionExecutor
from action_schema import ActionEnvelope
from memory_store import MemoryStore
from trace_store import TraceStore
from tool_registry import ToolDefinition, ToolRegistry


class AgentState(str, Enum):
    IDLE = "IDLE"
    THINKING = "THINKING"
    TOOL_CALLING = "TOOL_CALLING"
    EVALUATING = "EVALUATING"
    DONE = "DONE"
    FAILED = "FAILED"


LLMCallable = Callable[[list[dict[str, Any]], list[dict[str, Any]]], Awaitable[str | dict[str, Any]]]
ConfirmationPolicy = Callable[[ActionEnvelope, ToolDefinition], bool]
CanaryDecisionProvider = Callable[[], dict[str, Any]]


@dataclass(slots=True)
class AgentContext:
    objective: str
    state: AgentState = AgentState.IDLE
    history: list[dict[str, Any]] = field(default_factory=list)
    pending_actions: list[ActionEnvelope] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    final_answer: str = ""


class AgentCore:
    def __init__(
        self,
        llm_call: LLMCallable,
        registry: ToolRegistry,
        trace_store: TraceStore | None = None,
        confirmation_policy: ConfirmationPolicy | None = None,
        memory_store: MemoryStore | None = None,
        canary_provider: CanaryDecisionProvider | None = None,
        *,
        max_iterations: int = 12,
    ) -> None:
        self.llm_call = llm_call
        self.registry = registry
        self.executor = ActionExecutor(registry)
        self.trace = trace_store or TraceStore()
        self.memory = memory_store
        self.canary_provider = canary_provider
        self.confirmation_policy = confirmation_policy or self._default_confirmation_policy
        self.max_iterations = max_iterations
        self.runtime_policy: dict[str, Any] = {
            "mode": "normal",
            "write_protect": False,
            "deny_high_risk": False,
            "decision": "none",
            "sync_risk_score": None,
            "sync_risk_level": None,
        }

    def _runtime_policy_allows(self, action: ActionEnvelope) -> bool:
        if self.runtime_policy.get("write_protect") and action.name == "dosya_yaz":
            return False
        if self.runtime_policy.get("deny_high_risk") and action.safety_level in {"high", "critical"}:
            return False
        return True

    def _derive_canary_decision(self) -> dict[str, Any]:
        if self.canary_provider:
            try:
                provided = self.canary_provider() or {}
                if isinstance(provided, dict):
                    return provided
            except Exception as exc:
                return {
                    "decision": "hold",
                    "reason": [f"provider_exception:{exc.__class__.__name__}"],
                }

        try:
            from quality_evaluator import QualityEvaluator

            memory_path = self.memory.path if self.memory else None
            evaluator = QualityEvaluator(memory_path=memory_path, trace_dir=self.trace.base_dir)
            use_trend = os.getenv("AGENT_CANARY_TREND_WINDOWS", "0").strip() == "1"
            if use_trend:
                trend_ewma = os.getenv("AGENT_CANARY_TREND_EWMA", "0").strip() == "1"
                try:
                    trend_ewma_alpha = float(os.getenv("AGENT_CANARY_TREND_EWMA_ALPHA", "0.35"))
                except Exception:
                    trend_ewma_alpha = 0.35
                trend_seasonality = os.getenv("AGENT_CANARY_TREND_SEASONALITY", "0").strip() == "1"
                try:
                    trend_seasonality_days = int(os.getenv("AGENT_CANARY_TREND_SEASONALITY_DAYS", "14"))
                except Exception:
                    trend_seasonality_days = 14
                trend_decision = evaluator.trend_canary_decision(
                    lines=20000,
                    use_ewma=trend_ewma,
                    ewma_alpha=trend_ewma_alpha,
                    use_seasonality=trend_seasonality,
                    seasonality_lookback_days=max(1, trend_seasonality_days),
                )
                decision = {
                    "decision": trend_decision.get("decision", "hold"),
                    "reason": trend_decision.get("reason", []),
                    "ewma": trend_decision.get("ewma", {}),
                    "seasonality": trend_decision.get("seasonality", {}),
                    "trend": trend_decision.get("trend", {}),
                }
                # trend modunda status/score kısa pencereden türetilir
                windows = (decision.get("trend", {}) or {}).get("windows", {})
                short = windows.get("1h") or next(iter(windows.values()), {})
                return {
                    **decision,
                    "status": short.get("status"),
                    "score": short.get("score"),
                }

            summary = evaluator.summary(lines=1000, days=7.0)
            adaptive = os.getenv("AGENT_CANARY_ADAPTIVE", "0").strip() == "1"
            decision = evaluator.canary_decision(summary, adaptive=adaptive)
            return {
                **decision,
                "status": summary.get("status"),
                "score": summary.get("score"),
            }
        except Exception as exc:
            return {
                "decision": "hold",
                "reason": [f"evaluator_exception:{exc.__class__.__name__}"],
            }

    async def _apply_runtime_canary_policy(self, ctx: AgentContext) -> None:
        if os.getenv("AGENT_CANARY_RUNTIME_POLICY", "0").strip() != "1":
            return

        dec = self._derive_canary_decision()
        decision = str(dec.get("decision", "hold")).strip().lower()

        mode = "normal"
        write_protect = False
        deny_high_risk = False

        if decision == "rollback":
            mode = "rollback_protect"
            write_protect = True
            deny_high_risk = True
        elif decision == "hold":
            mode = "hold_cautious"
            deny_high_risk = True
        elif decision == "promote":
            mode = "promote_normal"

        sync_risk_score = None
        sync_risk_level = None
        use_sync_risk = os.getenv("AGENT_CANARY_RUNTIME_USE_SYNC_RISK", "1").strip() == "1"
        if use_sync_risk:
            try:
                from quality_evaluator import QualityEvaluator

                st = QualityEvaluator.get_calendar_sync_status()
                rc = ((st or {}).get("trend_summary", {}) or {}).get("risk_confidence", {}) or {}
                raw_score = rc.get("score")
                raw_level = rc.get("level")
                if raw_score is not None:
                    sync_risk_score = max(0.0, min(1.0, float(raw_score)))
                if raw_level is not None:
                    sync_risk_level = str(raw_level)
            except Exception:
                sync_risk_score = None
                sync_risk_level = None

        try:
            sync_med = float(os.getenv("AGENT_CANARY_RUNTIME_SYNC_RISK_MEDIUM", "0.5"))
        except Exception:
            sync_med = 0.5
        try:
            sync_high = float(os.getenv("AGENT_CANARY_RUNTIME_SYNC_RISK_HIGH", "0.75"))
        except Exception:
            sync_high = 0.75
        sync_med = max(0.0, min(1.0, sync_med))
        sync_high = max(sync_med, min(1.0, sync_high))

        if sync_risk_score is not None:
            if sync_risk_score >= sync_high:
                mode = "rollback_protect_sync_risk"
                write_protect = True
                deny_high_risk = True
                decision = "rollback"
                reasons = dec.get("reason", []) if isinstance(dec.get("reason", []), list) else [str(dec.get("reason"))]
                reasons.append(f"sync_risk_high:{sync_risk_score}")
                dec["reason"] = reasons
            elif sync_risk_score >= sync_med:
                if mode == "promote_normal":
                    mode = "hold_cautious_sync_risk"
                    decision = "hold"
                deny_high_risk = True

        self.runtime_policy.update(
            {
                "mode": mode,
                "write_protect": write_protect,
                "deny_high_risk": deny_high_risk,
                "decision": decision,
                "sync_risk_score": sync_risk_score,
                "sync_risk_level": sync_risk_level,
            }
        )

        reasons = dec.get("reason", []) if isinstance(dec.get("reason", []), list) else [str(dec.get("reason"))]
        msg = (
            f"Canary runtime policy aktif: decision={decision}, mode={mode}, "
            f"write_protect={write_protect}, deny_high_risk={deny_high_risk}, "
            f"reason={','.join(str(r) for r in reasons)}"
        )
        ctx.history.append({"role": "system", "content": msg})
        await self.trace.append(
            {
                "event": "runtime_policy_applied",
                "decision": decision,
                "mode": mode,
                "write_protect": write_protect,
                "deny_high_risk": deny_high_risk,
                "reason": reasons,
            }
        )

    @staticmethod
    def _default_confirmation_policy(action: ActionEnvelope, tool: ToolDefinition) -> bool:
        if not tool.requires_confirmation:
            return True
        if os.getenv("AGENT_AUTO_APPROVE_HIGH_RISK", "0").strip() == "1":
            return True
        # Varsayılan güvenli davranış: onay isteyen araçları reddet.
        return False

    @staticmethod
    def _objective_requires_test_gate(objective: str) -> bool:
        lower = objective.lower()
        keywords = ["bug", "fix", "hata", "düzelt", "duzelt", "regression"]
        return any(k in lower for k in keywords)

    @staticmethod
    def _extract_test_returncode(result: dict[str, Any]) -> int | None:
        if result.get("tool") != "test_calistir":
            return None
        if not result.get("ok"):
            return 1
        output = result.get("output", "")
        try:
            payload = json.loads(output)
            rc = payload.get("returncode")
            if isinstance(rc, int):
                return rc
        except Exception:
            return None
        return None

    @classmethod
    def _latest_test_status(cls, tool_results: list[dict[str, Any]]) -> str | None:
        for r in reversed(tool_results):
            if r.get("tool") != "test_calistir":
                continue
            rc = cls._extract_test_returncode(r)
            if rc is None:
                return "pass" if r.get("ok") else "fail"
            return "pass" if rc == 0 else "fail"
        return None

    @staticmethod
    def _extract_test_failure_hint(tool_results: list[dict[str, Any]]) -> str:
        for r in reversed(tool_results):
            if r.get("tool") != "test_calistir":
                continue
            out = str(r.get("output", ""))
            try:
                payload = json.loads(out)
                stderr = str(payload.get("stderr", "")).strip()
                stdout = str(payload.get("stdout", "")).strip()
                if stderr:
                    return stderr[:400]
                if stdout:
                    return stdout[:400]
            except Exception:
                if out:
                    return out[:400]
        return "Test hatası ayrıntısı bulunamadı."

    @staticmethod
    def _normalize_candidate_path(path: str) -> str | None:
        p = path.strip().replace("\\", "/")
        if not p:
            return None
        if p.startswith("./"):
            return p[2:]
        if p.startswith("/"):
            marker = "/workspaces/Otonom-analiz-bot/"
            if marker in p:
                return p.split(marker, 1)[1]
            return None
        return p

    @classmethod
    def _extract_fix_candidates(cls, tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Parse latest failed test output and derive candidate file/line targets."""
        for r in reversed(tool_results):
            if r.get("tool") != "test_calistir":
                continue
            out = str(r.get("output", ""))
            try:
                payload = json.loads(out)
                merged = f"{payload.get('stderr', '')}\n{payload.get('stdout', '')}"
            except Exception:
                merged = out

            candidate_map: dict[str, dict[str, Any]] = {}

            # Python traceback format: File "path", line 123
            for m in re.finditer(r'File\s+"([^"]+)"\s*,\s*line\s*(\d+)', merged):
                raw_path, raw_line = m.group(1), m.group(2)
                norm = cls._normalize_candidate_path(raw_path)
                if not norm:
                    continue
                key = f"{norm}:{raw_line}"
                line_i = int(raw_line)
                rec = candidate_map.get(key) or {"path": norm, "line": line_i, "hits": 0}
                rec["hits"] += 1
                candidate_map[key] = rec

            # Generic format: path.py:45 or path.py:45: message
            for m in re.finditer(r'([\w./\\-]+\.py):(\d+)', merged):
                raw_path, raw_line = m.group(1), m.group(2)
                norm = cls._normalize_candidate_path(raw_path)
                if not norm:
                    continue
                key = f"{norm}:{raw_line}"
                line_i = int(raw_line)
                rec = candidate_map.get(key) or {"path": norm, "line": line_i, "hits": 0}
                rec["hits"] += 1
                candidate_map[key] = rec

            def _score(c: dict[str, Any]) -> int:
                s = int(c.get("hits", 1)) * 5
                p = str(c.get("path", ""))
                if p.endswith(".py"):
                    s += 8
                if "/tests/" in p or p.startswith("tests/"):
                    s -= 2
                if "/site-packages/" in p:
                    s -= 10
                return s

            ranked = sorted(candidate_map.values(), key=_score, reverse=True)
            for c in ranked:
                c["score"] = _score(c)
            return ranked[:5]
        return []

    @staticmethod
    def _contains_action(actions: list[ActionEnvelope], name: str) -> bool:
        return any(a.name == name for a in actions)

    @staticmethod
    def _build_min_cost_fix_plan(candidates: list[dict[str, Any]], objective: str = "") -> str:
        if not candidates:
            return "Plan: 1) Son test hatasını oku. 2) En küçük kod değişikliğini uygula. 3) test_calistir ile doğrula."

        top = candidates[:3]
        targets = ", ".join(f"{c.get('path', '?')}:{c.get('line', '?')}" for c in top)
        fix_scope = "dar kapsamlı"
        if any("tests/" in str(c.get("path", "")) for c in top):
            fix_scope = "kod+test uyumlu"

        return (
            "Plan: "
            f"1) Önce şu hedefleri incele: {targets}. "
            f"2) En düşük riskli {fix_scope} yamayı uygula (tek dosya/tek davranış). "
            "3) Önce hedefli test, sonra test_calistir ile geniş doğrulama yap."
        )

    def _memory_hint_for_path(self, path: str) -> str:
        if not self.memory:
            return ""
        failures = self.memory.recall_failures_for_path(path, limit=3)
        if not failures:
            return ""
        hints = [f.hint for f in failures if f.hint][:2]
        sr = self.memory.success_rate_for_path(path)
        parts = [f"Geçmiş başarısızlık ({len(failures)}x, başarı oranı {sr:.0%})"]
        if hints:
            parts.append("Son ipuçları: " + " | ".join(hints))
        return "; ".join(parts)

    @staticmethod
    def _build_structured_fix_plan(candidates: list[dict[str, Any]], objective: str = "") -> dict[str, Any]:
        top = candidates[:3]
        scope = "narrow"
        if any("tests/" in str(c.get("path", "")) for c in top):
            scope = "code_test_alignment"
        return {
            "schema_version": "1.0",
            "objective": objective,
            "strategy": "minimal_cost_fix",
            "scope": scope,
            "targets": [
                {
                    "path": str(c.get("path", "")),
                    "line": int(c.get("line") or 1),
                    "score": int(c.get("score") or 0),
                }
                for c in top
            ],
            "steps": [
                {
                    "id": 1,
                    "action": "inspect_targets",
                    "tool": "dosya_oku",
                    "risk": "low",
                },
                {
                    "id": 2,
                    "action": "apply_minimal_patch",
                    "tool": "dosya_yaz",
                    "risk": "medium",
                },
                {
                    "id": 3,
                    "action": "run_targeted_test",
                    "tool": "test_calistir",
                    "args": {"framework": "unittest", "pattern": "targeted"},
                    "risk": "low",
                },
                {
                    "id": 4,
                    "action": "run_broad_regression",
                    "tool": "test_calistir",
                    "args": {"framework": "unittest", "pattern": "test_*.py"},
                    "risk": "low",
                },
            ],
        }

    @staticmethod
    def _validate_plan_json(plan: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if plan.get("schema_version") != "1.0":
            errors.append("schema_version must be '1.0'")
        if plan.get("strategy") != "minimal_cost_fix":
            errors.append("strategy must be 'minimal_cost_fix'")
        if not isinstance(plan.get("targets"), list):
            errors.append("targets must be a list")
        if not isinstance(plan.get("steps"), list) or len(plan.get("steps", [])) < 2:
            errors.append("steps must have at least 2 items")
        for step in plan.get("steps", []):
            if "tool" not in step:
                errors.append(f"step missing 'tool': {step}")
        return errors

    @staticmethod
    def _infer_narrow_test_pattern(path: str) -> str:
        p = path.replace("\\", "/").strip()
        if not p:
            return "test_*.py"
        if p.startswith("tests/") and p.endswith(".py"):
            return p.split("/", 1)[1]
        stem = p.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if not stem:
            return "test_*.py"
        return f"test*{stem}*.py"

    async def _run_inline_test(self, pattern: str) -> dict[str, Any]:
        action = ActionEnvelope(
            name="test_calistir",
            args={"framework": "unittest", "pattern": pattern, "timeout": 90},
            timeout_sec=95,
            retry=0,
            safety_level="medium",
        )
        res = await self.executor.run(action)
        return {
            "tool": res.tool_name,
            "trace_id": res.trace_id,
            "ok": res.ok,
            "output": res.output,
            "error": res.error,
            "attempts": res.attempts,
        }

    async def _run_inline_rollback(self, path: str, backup_path: str) -> dict[str, Any]:
        action = ActionEnvelope(
            name="dosya_geri_al",
            args={"path": path, "backup_path": backup_path},
            timeout_sec=20,
            retry=0,
            safety_level="high",
        )
        res = await self.executor.run(action)
        return {
            "tool": res.tool_name,
            "trace_id": res.trace_id,
            "ok": res.ok,
            "output": res.output,
            "error": res.error,
            "attempts": res.attempts,
        }

    async def _run_inline_read(self, path: str, line: int | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"path": path}
        if isinstance(line, int) and line > 0:
            args["start_line"] = max(1, line - 20)
            args["end_line"] = line + 20
        action = ActionEnvelope(
            name="dosya_oku",
            args=args,
            timeout_sec=25,
            retry=0,
            safety_level="low",
        )
        res = await self.executor.run(action)
        return {
            "tool": res.tool_name,
            "trace_id": res.trace_id,
            "ok": res.ok,
            "output": res.output,
            "error": res.error,
            "attempts": res.attempts,
        }

    async def run(self, objective: str) -> AgentContext:
        ctx = AgentContext(objective=objective)
        ctx.history.append({"role": "user", "content": objective})
        await self.trace.append({"event": "run_start", "objective": objective})
        await self._apply_runtime_canary_policy(ctx)

        for iteration in range(1, self.max_iterations + 1):
            ctx.state = AgentState.THINKING
            await self.trace.append({"event": "state", "state": ctx.state, "iter": iteration})

            model_out = await self.llm_call(ctx.history, self.registry.list_definitions())
            parsed = self._parse_model_output(model_out)
            await self.trace.append({"event": "llm_output", "iter": iteration, "kind": parsed["kind"]})

            if parsed["kind"] == "final":
                if self._objective_requires_test_gate(ctx.objective):
                    latest_test = self._latest_test_status(ctx.tool_results)
                    if latest_test is None:
                        await self.trace.append({"event": "test_gate_injected", "iter": iteration})
                        parsed = {
                            "kind": "actions",
                            "actions": [
                                {
                                    "name": "test_calistir",
                                    "args": {"framework": "unittest", "pattern": "test_*.py", "timeout": 90},
                                    "timeout_sec": 95,
                                    "retry": 0,
                                    "safety_level": "medium",
                                }
                            ],
                        }
                    elif latest_test == "fail":
                        hint = self._extract_test_failure_hint(ctx.tool_results)
                        candidates = self._extract_fix_candidates(ctx.tool_results)
                        min_plan = self._build_min_cost_fix_plan(candidates, ctx.objective)
                        plan_json = self._build_structured_fix_plan(candidates, ctx.objective)
                        candidate_text = (
                            " | Aday dosyalar: " + ", ".join(f"{c['path']}:{c['line']}" for c in candidates)
                            if candidates else
                            ""
                        )
                        # Hafızadaki geçmiş başarısızlık ipuçlarını ekle
                        mem_hints = ""
                        if self.memory and candidates:
                            for c in candidates[:2]:
                                mh = self._memory_hint_for_path(str(c.get("path", "")))
                                if mh:
                                    mem_hints += f" | Hafıza({c['path']}): {mh}"
                        ctx.history.append(
                            {
                                "role": "system",
                                "content": (
                                    "Test gate: Son test_calistir başarısız. "
                                    "Final reddedildi. Önce hata düzeltme aksiyonları üret, ardından test_calistir ile tekrar doğrula. "
                                    f"Son hata özeti: {hint}{candidate_text}{mem_hints} | {min_plan}"
                                ),
                            }
                        )
                        ctx.history.append(
                            {
                                "role": "system",
                                "content": f"PlanJSON: {json.dumps(plan_json, ensure_ascii=False)}",
                            }
                        )
                        await self.trace.append({
                            "event": "test_gate_rejected_final",
                            "iter": iteration,
                            "candidates": candidates,
                            "plan": min_plan,
                            "plan_json": plan_json,
                        })
                        continue
                else:
                    ctx.final_answer = parsed["final"]
                    ctx.state = AgentState.DONE
                    await self.trace.append({"event": "run_done", "iter": iteration})
                    return ctx

            if parsed["kind"] == "final":
                ctx.final_answer = parsed["final"]
                ctx.state = AgentState.DONE
                await self.trace.append({"event": "run_done", "iter": iteration})
                if self.memory:
                    success_writes = [
                        r for r in ctx.tool_results
                        if r.get("tool") == "dosya_yaz" and r.get("ok")
                    ]
                    for wr in success_writes:
                        try:
                            p = json.loads(wr.get("output", "{}")).get("path", "")
                            if p:
                                self.memory.record_fix_attempt(
                                    objective=ctx.objective,
                                    path=str(p),
                                    line=0,
                                    outcome="success",
                                )
                        except Exception:
                            pass
                return ctx

            actions = [ActionEnvelope(**a) for a in parsed["actions"]]

            if self._objective_requires_test_gate(ctx.objective):
                latest_test = self._latest_test_status(ctx.tool_results)
                if latest_test == "fail" and not self._contains_action(actions, "test_calistir"):
                    candidates = self._extract_fix_candidates(ctx.tool_results)
                    if candidates and not self._contains_action(actions, "dosya_oku"):
                        targeted_reads = []
                        for c in candidates[:3]:
                            line = int(c.get("line") or 1)
                            targeted_reads.append(
                                ActionEnvelope(
                                    name="dosya_oku",
                                    args={
                                        "path": c["path"],
                                        "start_line": max(1, line - 20),
                                        "end_line": line + 20,
                                    },
                                    timeout_sec=20,
                                    retry=0,
                                    safety_level="low",
                                )
                            )
                        actions = targeted_reads + actions
                        await self.trace.append({
                            "event": "test_gate_appended_candidate_read",
                            "iter": iteration,
                            "count": len(targeted_reads),
                            "targets": candidates[:3],
                        })
                    actions.append(
                        ActionEnvelope(
                            name="test_calistir",
                            args={"framework": "unittest", "pattern": "test_*.py", "timeout": 90},
                            timeout_sec=95,
                            retry=0,
                            safety_level="medium",
                        )
                    )
                    await self.trace.append({"event": "test_gate_appended_retest", "iter": iteration})

            ctx.pending_actions = actions
            ctx.state = AgentState.TOOL_CALLING
            await self.trace.append({
                "event": "tool_batch_start",
                "iter": iteration,
                "count": len(actions),
            })

            for action in actions:
                tool = self.registry.get(action.name)
                if not self._runtime_policy_allows(action):
                    record = {
                        "tool": action.name,
                        "trace_id": action.trace_id,
                        "ok": False,
                        "output": "",
                        "error": f"Canary runtime policy nedeniyle engellendi (mode={self.runtime_policy.get('mode')})",
                        "attempts": 0,
                    }
                    await self.trace.append(
                        {
                            "event": "tool_denied",
                            "iter": iteration,
                            "policy": "canary_runtime",
                            "policy_mode": self.runtime_policy.get("mode"),
                            **record,
                        }
                    )
                    ctx.tool_results.append(record)
                    ctx.history.append(
                        {
                            "role": "tool",
                            "name": record["tool"],
                            "content": json.dumps(record, ensure_ascii=False),
                        }
                    )
                    continue

                allowed = self.confirmation_policy(action, tool)
                if not allowed:
                    record = {
                        "tool": action.name,
                        "trace_id": action.trace_id,
                        "ok": False,
                        "output": "",
                        "error": "Onay politikası nedeniyle araç çağrısı engellendi",
                        "attempts": 0,
                    }
                    await self.trace.append({"event": "tool_denied", "iter": iteration, **record})
                else:
                    res = await self.executor.run(action)
                    record = {
                        "tool": res.tool_name,
                        "trace_id": res.trace_id,
                        "ok": res.ok,
                        "output": res.output,
                        "error": res.error,
                        "attempts": res.attempts,
                    }
                ctx.tool_results.append(record)
                ctx.history.append({"role": "tool", "name": record["tool"], "content": json.dumps(record, ensure_ascii=False)})
                await self.trace.append({"event": "tool_result", "iter": iteration, **record})

                if record["tool"] == "dosya_yaz" and record.get("ok"):
                    try:
                        payload = json.loads(record.get("output", ""))
                        diff = payload.get("diff", {})
                        pth = payload.get("path", "?")
                        backup = payload.get("backupPath", "")
                        summary = (
                            f"Dosya güncellendi: {pth}; "
                            f"+{diff.get('addedLines', 0)} -{diff.get('removedLines', 0)} satır; "
                            f"backup: {backup or 'yok'}"
                        )
                        ctx.history.append({"role": "system", "content": summary})

                        # Otonom güvenlik kapısı: yazılan dosya için dar test koş, başarısızsa rollback yap.
                        if self._objective_requires_test_gate(ctx.objective):
                            narrow_pattern = self._infer_narrow_test_pattern(str(pth))
                            narrow_res = await self._run_inline_test(narrow_pattern)
                            ctx.tool_results.append(narrow_res)
                            ctx.history.append({
                                "role": "tool",
                                "name": narrow_res["tool"],
                                "content": json.dumps(narrow_res, ensure_ascii=False),
                            })
                            await self.trace.append({
                                "event": "inline_narrow_test_result",
                                "iter": iteration,
                                "pattern": narrow_pattern,
                                **narrow_res,
                            })

                            narrow_pass = False
                            if narrow_res.get("ok"):
                                rc = self._extract_test_returncode(narrow_res)
                                narrow_pass = (rc == 0) if rc is not None else True

                            if (not narrow_pass) and backup:
                                rb_res = await self._run_inline_rollback(str(pth), str(backup))
                                ctx.tool_results.append(rb_res)
                                ctx.history.append({
                                    "role": "tool",
                                    "name": rb_res["tool"],
                                    "content": json.dumps(rb_res, ensure_ascii=False),
                                })
                                await self.trace.append({
                                    "event": "auto_rollback_applied",
                                    "iter": iteration,
                                    "path": pth,
                                    "backup": backup,
                                    **rb_res,
                                })

                                hint = self._extract_test_failure_hint([narrow_res])
                                rollback_candidates = self._extract_fix_candidates([narrow_res])
                                min_plan = self._build_min_cost_fix_plan(rollback_candidates, ctx.objective)
                                plan_json = self._build_structured_fix_plan(rollback_candidates, ctx.objective)
                                candidate_text = (
                                    ", ".join(
                                        f"{c['path']}:{c['line']} (skor={c.get('score', 0)})"
                                        for c in rollback_candidates[:3]
                                    )
                                    if rollback_candidates else "aday yok"
                                )
                                mem_hints_rb = ""
                                if self.memory and rollback_candidates:
                                    for c in rollback_candidates[:2]:
                                        mh = self._memory_hint_for_path(str(c.get("path", "")))
                                        if mh:
                                            mem_hints_rb += f" | Hafıza({c['path']}): {mh}"
                                ctx.history.append({
                                    "role": "system",
                                    "content": (
                                        "Auto rollback uygulandı. "
                                        f"Kök neden özeti: {hint}. "
                                        f"Hedef inceleme adayları: {candidate_text}.{mem_hints_rb} "
                                        f"{min_plan}"
                                    ),
                                })
                                ctx.history.append(
                                    {
                                        "role": "system",
                                        "content": f"PlanJSON: {json.dumps(plan_json, ensure_ascii=False)}",
                                    }
                                )
                                await self.trace.append({
                                    "event": "auto_rollback_guidance_added",
                                    "iter": iteration,
                                    "candidates": rollback_candidates[:3],
                                    "hint": hint,
                                    "plan": min_plan,
                                    "plan_json": plan_json,
                                })

                                if rollback_candidates:
                                    for c in rollback_candidates[:2]:
                                        read_res = await self._run_inline_read(str(c.get("path", "")), int(c.get("line") or 1))
                                        ctx.tool_results.append(read_res)
                                        ctx.history.append({
                                            "role": "tool",
                                            "name": read_res["tool"],
                                            "content": json.dumps(read_res, ensure_ascii=False),
                                        })
                                        await self.trace.append({
                                            "event": "auto_rollback_candidate_read",
                                            "iter": iteration,
                                            "target": {"path": c.get("path"), "line": c.get("line")},
                                            **read_res,
                                        })

                                if self.memory:
                                    self.memory.record_rollback(
                                        objective=ctx.objective,
                                        path=str(pth),
                                        hint=hint,
                                        trigger="narrow_test_fail",
                                    )
                                    for c in rollback_candidates[:3]:
                                        self.memory.record_fix_attempt(
                                            objective=ctx.objective,
                                            path=str(c.get("path", "")),
                                            line=int(c.get("line") or 0),
                                            outcome="failure",
                                            hint=hint,
                                            plan_json=plan_json,
                                        )

                            if os.getenv("AGENT_AUTOTEST_BROAD_AFTER_WRITE", "0").strip() == "1" and narrow_pass:
                                broad_res = await self._run_inline_test("test_*.py")
                                ctx.tool_results.append(broad_res)
                                ctx.history.append({
                                    "role": "tool",
                                    "name": broad_res["tool"],
                                    "content": json.dumps(broad_res, ensure_ascii=False),
                                })
                                await self.trace.append({
                                    "event": "inline_broad_test_result",
                                    "iter": iteration,
                                    **broad_res,
                                })

                                broad_pass = False
                                if broad_res.get("ok"):
                                    broad_rc = self._extract_test_returncode(broad_res)
                                    broad_pass = (broad_rc == 0) if broad_rc is not None else True

                                auto_rb_on_broad = os.getenv("AGENT_AUTOROLLBACK_ON_BROAD_FAIL", "1").strip() == "1"
                                if (not broad_pass) and backup and auto_rb_on_broad:
                                    rb_res = await self._run_inline_rollback(str(pth), str(backup))
                                    ctx.tool_results.append(rb_res)
                                    ctx.history.append({
                                        "role": "tool",
                                        "name": rb_res["tool"],
                                        "content": json.dumps(rb_res, ensure_ascii=False),
                                    })
                                    await self.trace.append({
                                        "event": "auto_rollback_applied_broad",
                                        "iter": iteration,
                                        "path": pth,
                                        "backup": backup,
                                        **rb_res,
                                    })
                                    ctx.history.append({
                                        "role": "system",
                                        "content": (
                                            "Broad test başarısız olduğu için otomatik rollback uygulandı. "
                                            "Geniş regresyon kırılımını hedefleyerek düzelt ve testleri yeniden doğrula."
                                        ),
                                    })
                                    if self.memory:
                                        self.memory.record_rollback(
                                            objective=ctx.objective,
                                            path=str(pth),
                                            hint="broad_test_fail",
                                            trigger="broad_test_fail",
                                        )
                                elif narrow_pass and broad_pass and self.memory:
                                    self.memory.record_fix_attempt(
                                        objective=ctx.objective,
                                        path=str(pth),
                                        line=0,
                                        outcome="success",
                                    )
                    except Exception:
                        pass

            ctx.state = AgentState.EVALUATING
            await self.trace.append({"event": "state", "state": ctx.state, "iter": iteration})

        ctx.state = AgentState.FAILED
        ctx.final_answer = "Maksimum iterasyon aşıldı."
        await self.trace.append({"event": "run_failed", "reason": "max_iterations"})
        return ctx

    @staticmethod
    def _parse_model_output(model_out: str | dict[str, Any]) -> dict[str, Any]:
        if isinstance(model_out, dict):
            payload = model_out
        else:
            text = model_out.strip()
            payload = json.loads(text)

        if "final" in payload and payload["final"]:
            return {"kind": "final", "final": str(payload["final"])}

        actions = payload.get("actions", [])
        if not isinstance(actions, list):
            raise ValueError("actions must be a list")
        return {"kind": "actions", "actions": actions}
