"""Async tool executor with timeout/retry behavior."""

from __future__ import annotations

import asyncio

from action_schema import ActionEnvelope, ToolResult
from tool_registry import ToolRegistry


class ActionExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    async def run(self, action: ActionEnvelope) -> ToolResult:
        action.validate()
        tool = self.registry.get(action.name)

        attempts = action.retry + 1
        last_error: str | None = None
        for i in range(1, attempts + 1):
            try:
                output = await asyncio.wait_for(
                    tool.execute(action.args),
                    timeout=action.timeout_sec,
                )
                return ToolResult(
                    tool_name=action.name,
                    trace_id=action.trace_id,
                    ok=True,
                    output=output,
                    attempts=i,
                )
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if i < attempts:
                    await asyncio.sleep(min(1.5 * i, 4.0))

        return ToolResult(
            tool_name=action.name,
            trace_id=action.trace_id,
            ok=False,
            output="",
            error=last_error,
            attempts=attempts,
        )
