"""Async tool executor with timeout/retry, security validation, and parallel execution."""

from __future__ import annotations

import asyncio
import os

from action_schema import ActionEnvelope, ToolResult
from tool_registry import ToolRegistry


class ActionExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    # ------------------------------------------------------------------
    # Security helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _check_path_arg(args: dict, allowed_paths: list[str]) -> str | None:
        """Returns error msg if 'path' arg resolves outside allowed_paths. None if OK."""
        path_arg = str(args.get("path", "")).strip()
        if not path_arg:
            return None
        resolved = os.path.realpath(os.path.abspath(path_arg))
        for ap in allowed_paths:
            if resolved.startswith(os.path.realpath(os.path.abspath(ap))):
                return None
        return f"Yol erişim engeli: '{path_arg}' izinli alan dışında"

    @staticmethod
    def _check_blocked_commands(args: dict, blocked_commands: list[str]) -> str | None:
        """Returns error msg if any arg value contains a blocked command pattern. None if OK."""
        for key in ("code", "command", "cmd"):
            val = str(args.get(key, ""))
            if not val:
                continue
            for blocked in blocked_commands:
                if blocked.lower() in val.lower():
                    return f"Engellenen komut tespit edildi: '{blocked}'"
        return None

    # ------------------------------------------------------------------
    # Single-action executor
    # ------------------------------------------------------------------

    async def run(self, action: ActionEnvelope) -> ToolResult:
        action.validate()
        tool = self.registry.get(action.name)

        # Security layer 1: path validation
        if tool.allowed_paths:
            path_err = self._check_path_arg(action.args, tool.allowed_paths)
            if path_err:
                return ToolResult(
                    tool_name=action.name,
                    trace_id=action.trace_id,
                    ok=False,
                    output="",
                    error=path_err,
                    attempts=0,
                )

        # Security layer 2: blocked command validation
        if tool.blocked_commands:
            cmd_err = self._check_blocked_commands(action.args, tool.blocked_commands)
            if cmd_err:
                return ToolResult(
                    tool_name=action.name,
                    trace_id=action.trace_id,
                    ok=False,
                    output="",
                    error=cmd_err,
                    attempts=0,
                )

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

    # ------------------------------------------------------------------
    # Parallel batch executor
    # ------------------------------------------------------------------

    async def run_parallel_batch(self, actions: list[ActionEnvelope]) -> list[ToolResult]:
        """Run a list of independent actions concurrently via asyncio.gather."""
        if not actions:
            return []
        results = await asyncio.gather(*[self.run(a) for a in actions])
        return list(results)
