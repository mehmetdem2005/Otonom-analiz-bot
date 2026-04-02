"""Tool registry with lightweight safety controls (ADIM-2: Tool Security)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable
import os


ToolFunc = Callable[[dict[str, Any]], Awaitable[str]]


class ToolRiskLevel(Enum):
    """Tool risk classification per ADIM-2 security framework"""
    LOW = "low"           # Read-only, no side effects
    MEDIUM = "medium"     # Limited write, no external network
    HIGH = "high"         # Network/file write, potential impact
    CRITICAL = "critical" # System level, requires audit


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    execute: ToolFunc
    requires_confirmation: bool = False
    risk_level: str = "medium"
    timeout_sec: int = 30
    max_retries: int = 1
    allowed_paths: list[str] | None = None  # Path allowlist (repo-only by default)
    blocked_commands: list[str] | None = None  # Command blocklist (dangerous: rm -rf, etc.)

    def __post_init__(self) -> None:
        """Validate and set defaults"""
        if self.allowed_paths is None:
            self.allowed_paths = [os.getenv("PWD", "/workspaces")]  # Repo root only
        if self.blocked_commands is None:
            self.blocked_commands = [
                "rm -rf",
                "sudo",
                "chmod 777",
                "kill -9",
                ":(){:|:&};:",  # Fork bomb
            ]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self.audit_log: list[dict[str, Any]] = []  # For ADIM-2 security audit

    def register(self, tool: ToolDefinition) -> None:
        if not tool.name:
            raise ValueError("Tool name is required")
        self._tools[tool.name] = tool

    def has(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> ToolDefinition:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Unknown tool: {name}")
        return tool

    def list_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "requires_confirmation": t.requires_confirmation,
                "risk_level": t.risk_level,
                "timeout_sec": t.timeout_sec,
            }
            for t in self._tools.values()
        ]
    
    def can_execute(self, tool_name: str, runtime_mode: str = "normal") -> tuple[bool, str]:
        """Check if tool can execute based on runtime policy (ADIM-2 integration)"""
        schema = self.get(tool_name)
        
        # rollback_protect: only LOW risk tools
        if runtime_mode == "rollback_protect":
            if schema.risk_level != ToolRiskLevel.LOW.value:
                return False, f"Tool {tool_name} risk level {schema.risk_level} blocked in rollback_protect mode"
        
        # hold_cautious: LOW + MEDIUM, no CRITICAL
        if runtime_mode == "hold_cautious":
            if schema.risk_level == ToolRiskLevel.CRITICAL.value:
                return False, f"Tool {tool_name} is CRITICAL, blocked in hold_cautious mode"
        
        # promote_normal: all tools allowed
        return True, "OK"
    
    def audit_execution(self, tool_name: str, args: dict[str, Any], result: str, error: str | None = None) -> None:
        """Log tool execution for security audit trail"""
        self.audit_log.append({
            "tool": tool_name,
            "args": args,
            "result": result[:100],  # Truncate long results
            "error": error,
            "timestamp": __import__("time").time(),
        })
